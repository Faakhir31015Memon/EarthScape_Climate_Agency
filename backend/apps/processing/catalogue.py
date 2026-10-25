"""The machine-readable workflow catalogue and every request-time check made against it.

`data-pipeline/spark/jobs.json` is the only source of runnable work: an unknown workflow id,
an unknown parameter or an out-of-bounds value is rejected here, so the runner can only ever
import a module the catalogue names. Nothing in this module accepts a command, script path,
module path or expression from a client.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from typing import Any, Optional

from common.datalake import LAYERS, list_layer, slug
from common.env import BACKEND_DIR, ENV
from common.exceptions import ServiceUnavailableConfig, ValidationError
from common.paths import ensure_paths
from common.tasks import engine as task_engine

CATALOGUE_PATH = BACKEND_DIR / "data-pipeline" / "spark" / "jobs.json"

MAX_LIST_ITEMS = 200
MAX_STRING_LENGTH = 300
LAYER_LISTING_CAP = 500
DATASET_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
ACCEPTED_INPUT_SUFFIXES = (".jsonl", ".json")

_CACHE: dict[str, Any] = {"mtime": None, "data": None}


def load() -> dict:
    """Read and cache the catalogue; a missing or broken file is a 503, never a guess."""
    ensure_paths()
    if not CATALOGUE_PATH.is_file():
        raise ServiceUnavailableConfig(
            "The workflow catalogue is not installed, so no processing job can be validated.",
            details=[f"Expected data-pipeline/spark/jobs.json at {CATALOGUE_PATH}."])
    mtime = CATALOGUE_PATH.stat().st_mtime
    if _CACHE["data"] is None or _CACHE["mtime"] != mtime:
        try:
            document = json.loads(CATALOGUE_PATH.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            raise ServiceUnavailableConfig(
                "The workflow catalogue could not be parsed, so processing is unavailable.")
        if not isinstance(document, dict) or not isinstance(document.get("jobs"), list):
            raise ServiceUnavailableConfig("The workflow catalogue has no 'jobs' array.")
        _CACHE.update(mtime=mtime, data=document)
    return _CACHE["data"]


def workflows() -> list[dict]:
    return [item for item in load().get("jobs", []) if isinstance(item, dict) and item.get("id")]


def workflow_ids() -> list[str]:
    return [str(item["id"]) for item in workflows()]


def workflow(workflow_id: str) -> Optional[dict]:
    for item in workflows():
        if item.get("id") == workflow_id:
            return item
    return None


def summary_collection() -> str:
    return str(load().get("summary_collection") or "analytics_summaries")


def output_layer_root() -> str:
    return str(load().get("output_layer_root") or "artifacts/datalake")


def registered_modules() -> dict:
    """The module map the shipped pipeline package publishes for catalogued ids."""
    ensure_paths()
    try:
        pipeline = importlib.import_module("spark")
    except Exception:
        return {}
    return dict(getattr(pipeline, "JOB_MODULES", {}))


# ------------------------------------------------------------------ engine truth
def pyspark_importable() -> bool:
    if importlib.util.find_spec("pyspark") is None:
        return False
    ensure_paths()
    try:
        from spark.common import spark_importable as _real

        return bool(_real())
    except Exception:
        return False


def spark_preferred() -> bool:
    ensure_paths()
    try:
        from spark.common import prefer_spark

        return bool(prefer_spark())
    except Exception:
        return False


def supports_spark(item: dict) -> bool:
    return "pyspark" in str(item.get("engine", "")) and "spark" in str(item.get("engine", ""))


def planned_engine(item: dict) -> tuple[str, str]:
    """The engine that will actually be used, stated without optimism."""
    if not supports_spark(item):
        return "python-validation", "Validation is pure Python; no Spark stage exists for this job."
    if spark_preferred():
        return ("pyspark",
                "A real SparkSession is attempted because SPARK_MASTER/SPARK_ENGINE_PREFER allow it; "
                "if the JVM or session fails the module degrades to pandas and records "
                "engine_attempted plus spark_error on the summary.")
    reason = "pyspark is not importable here" if not pyspark_importable() else "SPARK_MASTER and SPARK_ENGINE_PREFER are unset"
    return "pandas", f"{reason}; the pandas fallback keeps the same job id, parameters and output schema."


def input_layer(item: dict) -> str:
    if str(item.get("engine", "")) == "python-validation":
        return "raw"
    return "validated"


def layer_index(layers: tuple = ("raw", "validated")) -> dict:
    """One filesystem walk per layer, reused for every workflow in a listing."""
    index: dict[str, dict[str, int]] = {}
    for layer in layers:
        counts: dict[str, int] = {}
        for entry in list_layer(layer):
            dataset = str(entry.get("partition", {}).get("dataset") or "unknown")
            counts[dataset] = counts.get(dataset, 0) + 1
        index[layer] = counts
    return index


def input_state(item: dict, dataset: str = "", index: Optional[dict] = None) -> dict:
    layer = input_layer(item)
    counts = (index or {}).get(layer)
    if counts is None:
        entries = list_layer(layer)
        counts = {}
        for entry in entries:
            name = str(entry.get("partition", {}).get("dataset") or "unknown")
            counts[name] = counts.get(name, 0) + 1
    datasets_present = sorted(counts)
    matching = int(counts.get(dataset, 0)) if dataset else int(sum(counts.values()))
    return {
        "layer": layer,
        "populated": matching > 0,
        "matching_files": matching,
        "listing_capped_at": LAYER_LISTING_CAP,
        "datasets_present": [name for name in datasets_present if name != "unknown"],
        "requested_dataset": dataset or None,
    }


# ------------------------------------------------------------------ validation
def parameter_contract(item: dict) -> dict:
    contract: dict[str, dict] = {}
    for spec in item.get("parameters", []) or []:
        name = spec.get("name")
        if isinstance(name, str) and name:
            contract[name] = spec
    return contract


def allowed_parameter_names(item: dict) -> list[str]:
    return sorted(parameter_contract(item))


def _required_by_contract(item: dict) -> list[str]:
    return sorted(name for name, spec in parameter_contract(item).items() if spec.get("required"))


def _resolve_lake_path(value: Any, name: str, errors: list[str]) -> Optional[str]:
    """Explicit input paths must stay inside the data lake; never a general file reader."""
    from common.env import storage_root

    text = str(value or "").strip()
    if not text or len(text) > 400:
        errors.append(f"'{name}' must be a non-empty path of at most 400 characters.")
        return None
    candidate = Path(text)
    if not candidate.is_absolute():
        candidate = BACKEND_DIR / candidate
    resolved = candidate.resolve()
    root = storage_root().resolve()
    if root not in resolved.parents:
        errors.append(f"'{name}' must live inside the data lake root ({root}).")
        return None
    if resolved.suffix.lower() not in ACCEPTED_INPUT_SUFFIXES:
        errors.append(f"'{name}' must point at a {' or '.join(ACCEPTED_INPUT_SUFFIXES)} file.")
        return None
    if not resolved.is_file():
        errors.append(f"'{name}' does not exist: {resolved.name}.")
        return None
    return str(resolved)


def _coerce_string(name: str, value: Any, errors: list[str]) -> Optional[str]:
    if not isinstance(value, str):
        errors.append(f"'{name}' must be a string.")
        return None
    text = value.strip()
    if not text:
        errors.append(f"'{name}' must not be empty.")
        return None
    if len(text) > MAX_STRING_LENGTH:
        errors.append(f"'{name}' is longer than {MAX_STRING_LENGTH} characters.")
        return None
    return text


def _coerce_dataset(name: str, value: Any, errors: list[str]) -> Optional[str]:
    text = _coerce_string(name, value, errors)
    if text is None:
        return None
    if not DATASET_PATTERN.match(text):
        errors.append(f"'{name}' may only contain letters, digits, dot, underscore and hyphen.")
        return None
    return text


def _coerce_integer(name: str, value: Any, spec: dict, errors: list[str]) -> Optional[int]:
    parsed: Optional[int] = None
    if isinstance(value, bool):
        errors.append(f"'{name}' must be an integer.")
    elif isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
        parsed = int(value.strip())
    else:
        errors.append(f"'{name}' must be an integer.")
    if parsed is None:
        return None
    minimum, maximum = spec.get("minimum"), spec.get("maximum")
    if minimum is not None and parsed < int(minimum):
        errors.append(f"'{name}' must be >= {minimum} (got {parsed}).")
    if maximum is not None and parsed > int(maximum):
        errors.append(f"'{name}' must be <= {maximum} (got {parsed}).")
    return parsed


def _coerce_number(name: str, value: Any, spec: dict, errors: list[str]) -> Optional[float]:
    parsed: Optional[float] = None
    if isinstance(value, bool) or isinstance(value, (int, float)):
        parsed = float(value) if not isinstance(value, bool) else None
    elif isinstance(value, str):
        try:
            parsed = float(value.strip())
        except ValueError:
            parsed = None
    if parsed is None:
        errors.append(f"'{name}' must be a number.")
        return None
    minimum, maximum = spec.get("minimum"), spec.get("maximum")
    if minimum is not None and parsed < float(minimum):
        errors.append(f"'{name}' must be >= {minimum} (got {parsed}).")
    if maximum is not None and parsed > float(maximum):
        errors.append(f"'{name}' must be <= {maximum} (got {parsed}).")
    return parsed


def _coerce_enum(name: str, value: Any, spec: dict, errors: list[str]) -> Optional[str]:
    allowed = [str(option) for option in spec.get("values", []) or []]
    if not isinstance(value, str) or value not in allowed:
        errors.append(f"'{name}' must be one of: {', '.join(allowed)}.")
        return None
    return value


def _coerce_list(name: str, value: Any, spec: dict, errors: list[str]) -> Optional[list]:
    if not isinstance(value, list) or not value:
        errors.append(f"'{name}' must be a non-empty list.")
        return None
    if len(value) > MAX_LIST_ITEMS:
        errors.append(f"'{name}' accepts at most {MAX_LIST_ITEMS} entries.")
        return None
    if name == "paths":
        resolved: list[str] = []
        for entry in value:
            path = _resolve_lake_path(entry, name, errors)
            if path and path not in resolved:
                resolved.append(path)
        return resolved or None
    items: list[str] = []
    for entry in value:
        text = _coerce_string(f"{name}[]", entry, errors)
        if text is not None and text not in items:
            items.append(text)
    return items or None


def _coerce(name: str, value: Any, spec: dict, errors: list[str]) -> Any:
    declared = str(spec.get("type") or "")
    if declared == "string":
        return _coerce_dataset(name, value, errors) if name == "dataset" else _coerce_string(name, value, errors)
    if declared == "integer":
        return _coerce_integer(name, value, spec, errors)
    if declared == "number":
        return _coerce_number(name, value, spec, errors)
    if declared == "enum":
        return _coerce_enum(name, value, spec, errors)
    if declared.startswith("list"):
        return _coerce_list(name, value, spec, errors)
    errors.append(f"'{name}' has an unsupported catalogue type '{declared}'.")
    return None


def _cross_checks(params: dict, errors: list[str]) -> None:
    if params.get("year_from") and params.get("year_to") and params["year_from"] > params["year_to"]:
        errors.append("'year_from' must not be after 'year_to'.")
    if params.get("baseline_from") and params.get("baseline_to") and params["baseline_from"] > params["baseline_to"]:
        errors.append("'baseline_from' must not be after 'baseline_to'.")
    if params.get("year_from") and params.get("year_to") and params.get("baseline_from") \
            and params["baseline_from"] > params["year_to"]:
        errors.append("The baseline window starts after 'year_to', so it would contain no data.")


def validate(item: dict, parameters: Any) -> dict:
    """Catalogue-driven validation: only declared parameters, only inside declared bounds."""
    if parameters in (None, ""):
        parameters = {}
    if not isinstance(parameters, dict):
        raise ValidationError("The parameters field must be an object.",
                              details=[f"Allowed parameters: {', '.join(allowed_parameter_names(item)) or 'none'}."])
    contract = parameter_contract(item)
    errors: list[str] = []
    unknown = sorted(str(key) for key in parameters if str(key) not in contract)
    for key in unknown:
        errors.append(f"Unknown parameter '{key}'.")
    cleaned: dict[str, Any] = {}
    for name, spec in contract.items():
        if name in parameters:
            value = _coerce(name, parameters[name], spec, errors)
            if value is not None:
                cleaned[name] = value
        elif spec.get("required"):
            errors.append(f"'{name}' is required by this workflow.")
        elif "default" in spec:
            default = _coerce(name, spec["default"], spec, errors)
            if default is not None:
                cleaned[name] = default
    _cross_checks(cleaned, errors)
    if errors:
        details = errors + [f"Allowed parameters: {', '.join(sorted(contract)) or 'none'}",
                            f"Required parameters: {', '.join(_required_by_contract(item)) or 'none'}"]
        raise ValidationError(f"The parameters are not valid for workflow '{item.get('id')}'.",
                              details=details)
    missing_required = [name for name, spec in contract.items() if spec.get("required") and name not in cleaned]
    if missing_required:
        raise ValidationError(f"Workflow '{item.get('id')}' is missing required parameters.",
                              details=[f"'{name}' is required." for name in missing_required])
    return cleaned


def resolve_workflow(workflow_id: Any) -> dict:
    item = workflow(str(workflow_id or ""))
    if item is None:
        raise ValidationError(f"'{workflow_id}' is not a catalogued workflow.",
                              details=[f"Catalogued workflows: {', '.join(workflow_ids())}."])
    return item


# ------------------------------------------------------------------ availability
def runtime_availability() -> dict:
    ensure_paths()
    hadoop_home = ENV.raw("HADOOP_HOME")
    hadoop_bin = ENV.raw("HADOOP_BIN")
    lake_engine = ENV.raw("DATA_LAKE_ENGINE", "local")
    modules = registered_modules()
    from common.datalake import status as lake_status

    lake = lake_status()
    paths = ensure_paths()
    return {
        "catalogue_version": str(load().get("catalogue_version") or ""),
        "engine_policy": str(load().get("engine_policy") or ""),
        "output_layer_root": output_layer_root(),
        "summary_collection": summary_collection(),
        "catalogued_workflows": len(workflow_ids()),
        "modules_registered": len(modules),
        "pipeline_root_present": bool(paths.get("pipeline_root_present")),
        "pyspark_importable": pyspark_importable(),
        "spark_preferred": spark_preferred(),
        "spark_master": ENV.raw("SPARK_MASTER", "local[*]"),
        "mapreduce_mode": ENV.raw("MAPREDUCE_MODE", "streaming-local"),
        "hadoop_home": hadoop_home or None,
        "hadoop_bin": hadoop_bin or None,
        "hadoop_cluster_expected": bool(hadoop_home or hadoop_bin)
                                    or ENV.raw("MAPREDUCE_MODE", "") == "cluster",
        "datalake_engine": lake.get("engine"),
        "datalake_reachable": bool(lake.get("reachable")),
        "datalake_detail": str(lake.get("detail") or lake.get("root") or ""),
        "hdfs_configured": ENV.exists("HDFS_WEBHDFS_URL"),
        "task_engine": task_engine(),
        "layers": list(LAYERS),
    }


def describe(item: dict, *, dataset: str = "", last_run: Optional[dict] = None,
             modules: Optional[dict] = None, index: Optional[dict] = None) -> dict:
    """One workflow, enriched with what this runtime can honestly do with it."""
    modules = registered_modules() if modules is None else modules
    engine, engine_note = planned_engine(item)
    inputs = input_state(item, dataset=dataset, index=index)
    module_path = modules.get(str(item["id"]))
    runnable = bool(module_path) and inputs["populated"]
    if not module_path:
        blocked = "the catalogue names this job but the pipeline package does not register a module for it"
    elif not inputs["populated"]:
        blocked = f"the required input layer '{inputs['layer']}' has no data" + (
            f" for dataset '{dataset}'" if dataset else "")
    else:
        blocked = None
    return {
        "id": item["id"],
        "title": item.get("title"),
        "description": item.get("description"),
        "catalogued_engine": item.get("engine"),
        "engine_planned": engine,
        "engine_note": engine_note,
        "module_registered": bool(module_path),
        "required_inputs": item.get("required_inputs", []),
        "inputs": inputs,
        "parameters": [dict(spec) for spec in item.get("parameters", []) or []],
        "allowed_parameters": allowed_parameter_names(item),
        "output_path": item.get("output_path"),
        "runnable": runnable,
        "blocked_reason": blocked,
        "last_run": last_run,
    }


def dataset_slug_for(name: Any) -> str:
    return slug(str(name or ""))
