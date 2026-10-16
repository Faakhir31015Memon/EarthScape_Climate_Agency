"""Bridge between the Django HTTP layer and the offline ``ml`` package.

Every ML number this app returns comes from a call made here: the package is invoked through
its ``mlkit`` facade, and its raw dicts are converted into Mongo-ready documents. Nothing in
this module invents a metric, a forecast value or an anomaly - a refusal from the package is
converted into an honest status plus its reason instead of a fabricated result.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

from common.env import ENV
from common.exceptions import NotFound, ServiceUnavailableConfig, ValidationError
from common.logging_config import get_logger
from common.paths import ML_ROOT, ensure_paths
from common.security import fingerprint, guard_query, sanitize_text
from common.serialization import parse_datetime

logger = get_logger("earthscape.machine_learning")

ANOMALY_METHODS = ("threshold", "statistical", "isolation_forest")
GRANULARITIES = ("instantaneous", "hourly", "daily", "monthly")

# Model refs are registry keys, never filesystem paths: separators and traversal are refused
# before the string is ever handed to the artifact store.
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{1,118}(?:@[A-Za-z0-9][A-Za-z0-9._\-]{0,59})?$")

# Request vocabulary (TrainingRequest.models) -> the ml package's model ladder names.
LADDER_ALIASES: dict[str, str] = {
    "baseline": "naive",
    "naive": "naive",
    "seasonal": "seasonal_naive",
    "seasonal_naive": "seasonal_naive",
    "linear": "linear_ridge",
    "linear_ridge": "linear_ridge",
    "ridge": "linear_ridge",
    "random_forest": "random_forest",
    "xgboost": "xgboost",
    "gbm": "xgboost",
}

TTL_SECONDS = ENV.integer("ML_TIDY_CACHE_SECONDS", 900)
INLINE_FORECAST_MAX_STEPS = ENV.integer("ML_FORECAST_INLINE_MAX_STEPS", 120)
MAX_ANOMALY_EVENTS = ENV.integer("ML_ANOMALY_MAX_EVENTS", 100)
AVAILABILITY_MAX_SLICES = ENV.integer("ML_AVAILABILITY_MAX_SLICES", 200)

_mlkit_cache: dict[str, Any] = {"module": None, "error": None}
_frame_cache: dict[str, Any] = {"frame": None, "loaded_at": None, "load_seconds": None,
                                "hit": False}
_lock = threading.Lock()


def _load_mlkit() -> Any:
    """Import the ``ml`` facade through ``common.paths`` wiring (never a hand-built path)."""
    if _mlkit_cache["module"] is not None:
        return _mlkit_cache["module"]
    with _lock:
        if _mlkit_cache["module"] is not None:
            return _mlkit_cache["module"]
        layout = ensure_paths()
        if not layout.get("ml_root_present"):
            raise ServiceUnavailableConfig(
                "The ML package directory is missing from this deployment.",
                details=[f"Expected the `ml/` package beside the repository root; found "
                         f"{ML_ROOT} = {layout.get('ml_root_present')}."])
        try:
            from ml import mlkit  # works when the repository root is already importable

            _mlkit_cache["module"] = mlkit
            return mlkit
        except ImportError:
            pass
        try:
            # WHY: `ml/` uses intra-package relative imports, so it must be bound as the
            # package rooted at common.paths.ML_ROOT rather than imported as loose modules.
            spec = importlib.util.spec_from_file_location(
                "ml", str(ML_ROOT / "__init__.py"), submodule_search_locations=[str(ML_ROOT)])
            if spec is None or spec.loader is None:
                raise ImportError("no import spec for the ml package")
            module = importlib.util.module_from_spec(spec)
            sys.modules["ml"] = module
            spec.loader.exec_module(module)
            from ml import mlkit as facade

            _mlkit_cache["module"] = facade
            return facade
        except Exception as exc:
            reason = f"ml package could not be imported: {type(exc).__name__}: {str(exc)[:200]}"
            _mlkit_cache["error"] = reason
            logger.error(reason, exc_info=True)
            raise ServiceUnavailableConfig(
                "The ML runtime is unavailable, so no model can be trained, evaluated or run.",
                details=[reason]) from exc


def mlkit() -> Any:
    """The ``ml`` facade, or an honest 503 explaining why it is not usable."""
    return _load_mlkit()


def package_error() -> Optional[str]:
    return _mlkit_cache["error"]


# --------------------------------------------------------------------------- observation data
def observations(*, force: bool = False) -> Any:
    """The tidy observation frame, cached per process because one full lake read is slow."""
    kit = mlkit()
    with _lock:
        cached = _frame_cache["frame"]
        age = None
        if cached is not None and _frame_cache["loaded_at"] is not None:
            age = (datetime.now(timezone.utc) - _frame_cache["loaded_at"]).total_seconds()
        if cached is not None and not force and age is not None and age < TTL_SECONDS:
            _frame_cache["hit"] = True
            return cached
    started = time.perf_counter()
    frame = kit.load_observations()
    elapsed = round(time.perf_counter() - started, 3)
    with _lock:
        _frame_cache["frame"] = frame
        _frame_cache["loaded_at"] = datetime.now(timezone.utc)
        _frame_cache["load_seconds"] = elapsed
        _frame_cache["hit"] = False
    return frame


def cache_state() -> dict[str, Any]:
    with _lock:
        loaded_at = _frame_cache["loaded_at"]
        age = (datetime.now(timezone.utc) - loaded_at).total_seconds() if loaded_at else None
        rows = int(len(_frame_cache["frame"])) if _frame_cache["frame"] is not None else 0
        return {
            "warm": bool(rows),
            "rows_cached": rows,
            "hit": bool(_frame_cache["hit"]),
            "age_seconds": round(age, 1) if age is not None else None,
            "ttl_seconds": TTL_SECONDS,
            "last_load_seconds": _frame_cache["load_seconds"],
        }


def _windowed(frame: Any, date_range: Optional[dict], station_id: Optional[str]) -> tuple[Any, dict]:
    """Restrict the tidy frame to the requested window/station; empty slices stay empty."""
    note = {"rows_before_window": int(len(frame)), "rows_after_window": int(len(frame)),
            "window": None, "station_id": None}
    if date_range:
        try:
            guard_query(date_range)
        except ValueError as exc:
            raise ValidationError("date_range contains a disallowed key.") from exc
        start = parse_datetime(date_range.get("start") or date_range.get("from"))
        end = parse_datetime(date_range.get("end") or date_range.get("to"))
        if not start or not end:
            raise ValidationError("date_range needs both 'start' and 'end' as ISO dates.")
        if end < start:
            raise ValidationError("date_range.end must not precede date_range.start.")
        import pandas as pd

        start_ts = pd.Timestamp(start.astimezone(timezone.utc))
        end_ts = pd.Timestamp(end.astimezone(timezone.utc))
        frame = frame[(frame["timestamp"] >= start_ts) & (frame["timestamp"] <= end_ts)]
        note["window"] = {"start": start.isoformat(), "end": end.isoformat()}
    if station_id:
        station = sanitize_text(station_id, max_length=64)
        frame = frame[frame["station"] == station]
        note["station_id"] = station
    note["rows_after_window"] = int(len(frame))
    return frame, note


# --------------------------------------------------------------------------- model refs
def validate_ref(raw: Any) -> str:
    """Normalise a client-supplied model ref into a registry lookup key."""
    ref = sanitize_text(raw, max_length=120)
    if not ref or not _REF_RE.match(ref) or ".." in ref or "/" in ref or "\\" in ref:
        raise ValidationError(
            "model_ref must look like 'temperature-Karachi-daily-h30@v20261016T161737Z'.",
            details=["letters, digits, dot, dash, underscore; optional '@version' suffix."])
    return ref


def disk_registry() -> list[dict[str, Any]]:
    """Registry documents for every model artifact actually saved on disk."""
    kit = mlkit()
    try:
        entries = kit.registry_entries()
    except Exception as exc:
        logger.error("registry read failed: %s", exc, exc_info=True)
        raise ServiceUnavailableConfig(
            "The on-disk model registry could not be read.",
            details=[f"{type(exc).__name__}: {str(exc)[:200]}"]) from exc
    return [registry_document(entry) for entry in entries]


def find_disk_entry(ref: str) -> dict[str, Any]:
    """Resolve ``name`` or ``name@version`` against the saved artifacts (never the filesystem)."""
    documents = disk_registry()
    by_ref = {doc["model_ref"]: doc for doc in documents}
    if ref in by_ref:
        return by_ref[ref]
    if "@" not in ref:
        candidates = sorted(doc for doc in by_ref if doc["model_name"] == ref)
        if candidates:
            return by_ref[candidates[-1]]
    available = sorted(by_ref)[:20]
    raise NotFound(
        f"No registered model matches '{ref}'.",
        details=[f"{len(by_ref)} model(s) are readable on disk"
                 + (f": {', '.join(available)}" if available else " (none yet)")])


# --------------------------------------------------------------------------- availability
def availability(*, min_rows: Optional[int] = None, region: Optional[str] = None,
                 variable: Optional[str] = None) -> dict[str, Any]:
    """Honest per-slice modelling verdict plus the files and rows it came from."""
    kit = mlkit()
    started = time.perf_counter()
    frame = observations()
    cache = cache_state()
    # WHY: the facade's no-argument helpers each re-read the whole lake; these are the same
    # functions availability_report()/data_sources() call internally, given the shared frame.
    slices = kit.summarize_availability(frame, min_rows=min_rows)
    discovered = {key: [str(path) for path in value]
                  for key, value in kit.discover_files().items()}
    loading = kit.loading_report(frame)
    gate = int(min_rows) if min_rows else int(kit.model_capabilities()["min_training_rows"])

    if region:
        wanted = sanitize_text(region, max_length=80)
        slices = [row for row in slices if row.get("region") == wanted]
    if variable:
        wanted_var = sanitize_text(variable, max_length=32)
        slices = [row for row in slices if row.get("variable") == wanted_var]

    trainable = [row for row in slices if row.get("enough_rows")]
    blocked = [row for row in slices if not row.get("enough_rows")]
    regions = sorted({str(row.get("region")) for row in slices if row.get("region")})
    trainable_regions = sorted({str(row.get("region")) for row in trainable})
    blocked_regions: dict[str, list[str]] = {}
    for row in blocked:
        blocked_regions.setdefault(str(row.get("region")), []).append(
            f"{row.get('variable')}/{row.get('granularity')} ({row.get('rows')} rows): "
            f"{row.get('reason')}")

    limit = max(1, AVAILABILITY_MAX_SLICES)
    return {
        "generated_at": datetime.now(timezone.utc),
        "gate": {
            "min_training_rows": gate,
            "requested_min_rows": int(min_rows) if min_rows else None,
            "meaning": "A (region, variable, granularity) slice with fewer observed rows than this "
                       "is never trained on, forecast from, or presented as trainable.",
        },
        "sources": {
            "storage_root": str(kit.mlconfig.storage_root()),
            "model_root": str(kit.mlconfig.model_artifact_root()),
            "discovered_files": {key: len(value) for key, value in discovered.items()},
            "discovered_file_paths": {key: value[:20] for key, value in discovered.items()},
            "loading": loading,
        },
        "summary": {
            "observation_rows": int(loading.get("rows") or 0),
            "regions": regions,
            "regions_with_training_data": trainable_regions,
            "regions_without_training_data": sorted(set(regions) - set(trainable_regions)),
            "variables": sorted(str(v) for v in (loading.get("variables") or {}).keys()),
            "granularities": loading.get("granularities") or [],
            "date_range": loading.get("date_range"),
            "slices_total": len(slices),
            "slices_trainable": len(trainable),
            "slices_not_trainable": len(blocked),
            "models_on_disk": len(disk_registry()),
        },
        "regions_blocked": [
            {"region": key, "reasons": value[:10]} for key, value in sorted(blocked_regions.items())
        ],
        "slices": slices[:limit],
        "slices_truncated": max(0, len(slices) - limit),
        "timing": {"read_seconds": round(time.perf_counter() - started, 3)},
        "cache": cache,
    }


def trainable_regions(variable: str, granularity: str = "daily") -> list[str]:
    """Regions that genuinely clear the row gate for one variable (used when region is omitted)."""
    frame = observations()
    kit = mlkit()
    rows = kit.summarize_availability(frame)
    return sorted({str(row["region"]) for row in rows
                   if row.get("enough_rows") and row.get("variable") == variable
                   and row.get("granularity") == granularity})


# --------------------------------------------------------------------------- training
def resolve_ladder(models: Optional[list[str]]) -> tuple[list[str], list[str]]:
    """Map requested model names onto the ladder; return (ladder, rejected)."""
    ladder: list[str] = []
    rejected: list[str] = []
    for raw in (models or []):
        name = sanitize_text(raw, max_length=32).lower()
        mapped = LADDER_ALIASES.get(name)
        if mapped is None:
            rejected.append(str(raw))
            continue
        if mapped not in ladder:
            ladder.append(mapped)
    return ladder, rejected


def train(*, target_variable: str, region: str, granularity: str = "daily",
          horizon_steps: int = 30, folds: int = 5, models: Optional[list[str]] = None,
          date_range: Optional[dict] = None, station_id: Optional[str] = None,
          save: bool = True) -> dict[str, Any]:
    """Run the model ladder on one slice and convert the report into registry-ready form."""
    kit = mlkit()
    frame, window = _windowed(observations(), date_range, station_id)
    ladder, rejected = resolve_ladder(models)
    if rejected:
        raise ValidationError(
            "Unsupported model name(s) in request.",
            details=[f"'{name}' is not in the ladder; allowed: "
                     f"{', '.join(sorted(set(LADDER_ALIASES)))}"])
    started = time.perf_counter()
    report = kit.train_model(target_variable=target_variable, region=region,
                             granularity=granularity, horizon_steps=int(horizon_steps),
                             folds=int(folds), save=bool(save), tidy=frame)
    result = dict(report or {})
    result["ladder_requested"] = ladder or ["naive", "seasonal_naive", "linear_ridge",
                                            "random_forest"]
    result["ladder_tried"] = [entry.get("algorithm") for entry in (result.get("comparison") or [])]
    result["window"] = window
    result["requested"] = {"target_variable": target_variable, "region": region,
                           "granularity": granularity, "horizon_steps": int(horizon_steps),
                           "folds": int(folds), "save": bool(save)}
    result["duration_seconds"] = round(time.perf_counter() - started, 3)
    result["cache"] = cache_state()
    entry = result.get("registry")
    if isinstance(entry, dict) and entry.get("model_name") and save:
        result["registry_document"] = registry_document(entry)
    return result


def evaluate(model_ref: str) -> dict[str, Any]:
    """Stored chronological + walk-forward numbers for one registered model."""
    kit = mlkit()
    resolved = find_disk_entry(model_ref)
    ref = resolved["model_ref"]
    started = time.perf_counter()
    try:
        payload = kit.evaluate_model(ref)
    except FileNotFoundError as exc:
        raise NotFound(f"The artifact for '{ref}' could not be loaded.",
                       details=[str(exc)[:200]]) from exc
    except Exception as exc:
        logger.error("evaluate failed for %s: %s", ref, exc, exc_info=True)
        raise ServiceUnavailableConfig(
            "This model could not be evaluated in the current runtime.",
            details=[f"{type(exc).__name__}: {str(exc)[:200]}"]) from exc
    payload = dict(payload or {})
    payload["requested_ref"] = model_ref
    payload["resolved_ref"] = ref
    payload["mode"] = "stored_evaluation"
    payload["note"] = ("mlkit.evaluate_model() reads the chronological holdout and walk-forward "
                       "numbers recorded at training time; it does not refit the model.")
    payload["duration_seconds"] = round(time.perf_counter() - started, 3)
    return payload


def forecast(*, model_ref: str, target_variable: str, region: Optional[str] = None,
             horizon_steps: int = 30, origin_date: Optional[str] = None,
             station_id: Optional[str] = None) -> dict[str, Any]:
    """Project one horizon from a registered model, passing every refusal through."""
    kit = mlkit()
    resolved = find_disk_entry(model_ref)
    ref = resolved["model_ref"]
    scope = sanitize_text(region or "", max_length=80) or sanitize_text(
        station_id or "", max_length=64) or str(resolved.get("region") or "")
    origin = None
    if origin_date:
        origin = parse_datetime(origin_date)
        if not origin:
            raise ValidationError("origin_date must be an ISO-8601 date or timestamp.")
    started = time.perf_counter()
    result = kit.forecast(ref, target_variable, scope, int(horizon_steps),
                          origin_date=origin.isoformat() if origin else None,
                          tidy=observations())
    result = dict(result or {})
    result["requested_ref"] = model_ref
    result["resolved_ref"] = ref
    result["requested_scope"] = {"region": region, "station_id": station_id}
    result["duration_seconds"] = round(time.perf_counter() - started, 3)
    result["cache"] = cache_state()
    if result.get("status") != "forecast":
        result.setdefault("honest_status", result.get("status") or "refused")
    return result


def scan_anomalies(*, region: str, variable: str, method: str = "statistical",
                   window_days: int = 90, sigma: float = 3.0,
                   granularity: str = "daily", origin: Optional[str] = None) -> dict[str, Any]:
    """Detect anomalies for one slice; the package's refusal states are returned untouched."""
    kit = mlkit()
    scope = sanitize_text(region, max_length=80)
    if not scope:
        raise ValidationError("An anomaly scan needs a region: the baseline is that location's "
                              "own recorded climatology. Omit 'region' to scan every trainable "
                              "region as a background job.")
    started = time.perf_counter()
    kwargs: dict[str, Any] = {}
    tuning_applied = False
    if method == "statistical":
        # WHY: `window_days`/`sigma` in the request are the detector's climatology window and
        # its reporting floor (z_info); severity tiers themselves stay the package's fixed map.
        kwargs = {"window_days": int(window_days), "z_info": float(sigma)}
        tuning_applied = True
    cut = parse_datetime(origin) if origin else None
    result = kit.detect_anomalies(scope, variable, method, origin=cut.isoformat() if cut else None,
                                  granularity=granularity, tidy=observations(), **kwargs)
    result = dict(result or {})
    result["requested"] = {"region": scope, "variable": variable, "method": method,
                           "window_days": int(window_days), "sigma": float(sigma),
                           "granularity": granularity}
    result["tuning"] = {
        "window_days": int(window_days), "sigma": float(sigma), "applied": tuning_applied,
        "note": (("'window_days' and 'sigma' tune the season-aware detector (climatology half-window "
                  "and reporting floor). For '%s' the detector has no such knobs, so the request "
                  "values were recorded but not used; severity tiers are always the package's "
                  "fixed 2/3/4 robust-z mapping.") % (method,)) if not tuning_applied else
                  ("sigma raises the reporting floor only; severity tiers stay the package's fixed "
                   "2/3/4 robust-z mapping."),
    }
    result["duration_seconds"] = round(time.perf_counter() - started, 3)
    result["cache"] = cache_state()
    return result


def anomaly_methods() -> dict[str, Any]:
    """What each detector assumes, and whether it can actually run in this process."""
    kit = mlkit()
    stats = kit.isolation_forest_status()
    capabilities = kit.model_capabilities()
    return {
        "methods": [
            {
                "method": "threshold",
                "function": "ml/anomaly/threshold.check_thresholds",
                "assumes": "Fixed plausibility limits per variable; a value outside [min, max] is "
                           "a sensor-fault candidate, not a climate statement.",
                "baseline": "none - limits table only",
                "available": True,
                "severity_rule": "marginal vs extreme breach (extreme = >25% of the span beyond "
                                 "a limit); threshold-only detections cap at 'warning'.",
                "tunable": ["none via this API"],
            },
            {
                "method": "statistical",
                "function": "ml/anomaly/statistical.statistical_anomalies",
                "assumes": "Season-aware day-of-year climatology: median of every other year's "
                           "observations within +/-window days of the day-of-year "
                           "(leave-one-out), scaled by 1.4826*MAD (robust z).",
                "baseline": "this location's own recorded seasonality, never a global mean",
                "available": True,
                "severity_rule": "none z<2.0, info z>=2.0, warning z>=3.0, severe z>=4.0 "
                                 "(combined with breach level where present)",
                "tunable": ["window_days -> climatology half-window", "sigma -> reporting floor"],
            },
            {
                "method": "isolation_forest",
                "function": "ml/anomaly/isolation_forest.isolation_forest_anomalies",
                "assumes": "Multivariate temporal features; fits the first ~70% of the record as "
                           "'normal behaviour' and scores the remainder. Contamination is "
                           "heuristic, so flags are 'unusual', never proof.",
                "baseline": "fit-window score distribution (robust z against it)",
                "available": bool(stats.get("available")),
                "severity_rule": "score spread vs the fit window, floor 'info' when undefined",
                "tunable": ["none via this API"],
            },
        ],
        "sklearn_importable": bool(stats.get("available")),
        "isolation_forest_status": stats,
        "severity_mapping": dict(kit.MAPPING_DOC),
        "threshold_limits": {key: dict(value) for key, value in kit.DEFAULT_LIMITS.items()},
        "min_baseline_sample": int(kit.mlconfig.min_baseline_sample()),
        "min_training_rows": capabilities.get("min_training_rows"),
        "note": "Every detection is a deviation from recorded data at that location. Nothing here "
                "attributes a cause, and a thin baseline is reported as a refusal, not an anomaly.",
    }


def capabilities() -> dict[str, Any]:
    """Runtime capability picture: which model tiers can actually run right now."""
    kit = mlkit()
    payload = kit.model_capabilities()
    payload["ml_package"] = {"imported": True, "root": str(ML_ROOT),
                             "facade": "ml/mlkit.py", "error": package_error()}
    payload["variables"] = list(kit.VARIABLES)
    payload["tidy_columns"] = list(kit.TIDY_COLUMNS)
    payload["anomaly_methods"] = list(ANOMALY_METHODS)
    payload["granularities"] = list(GRANULARITIES)
    payload["projection_note"] = kit.PROJECTION_NOTE
    payload["models_on_disk"] = len(disk_registry())
    payload["data_cache"] = cache_state()
    return payload


# --------------------------------------------------------------------------- document builders
def _metrics(entry: dict[str, Any]) -> dict[str, Any]:
    raw = entry.get("evaluation_metrics") or {}
    return {
        "mae": raw.get("mae"),
        "rmse": raw.get("rmse"),
        "r2": raw.get("r2"),
        "mape": raw.get("mape"),
        "mape_skipped_reason": raw.get("mape_skipped_reason"),
        "baseline_mae": raw.get("baseline_mae"),
        "skill_vs_baseline": raw.get("skill_vs_baseline"),
        "test_n": raw.get("test_n"),
    }


def registry_document(entry: dict[str, Any]) -> dict[str, Any]:
    """Convert an ``mlkit.registry_entries()`` item into a ``model_registry`` document."""
    name = sanitize_text(entry.get("model_name"), max_length=120)
    version = sanitize_text(entry.get("model_version"), max_length=60)
    ref = f"{name}@{version}" if version else name
    created = parse_datetime(entry.get("created_at")) or datetime.now(timezone.utc)
    return {
        "model_ref": ref,
        "model_name": name,
        "model_version": version or "unsaved",
        "algorithm": sanitize_text(entry.get("algorithm"), max_length=48),
        "target_variable": sanitize_text(entry.get("target_variable"), max_length=32),
        "granularity": sanitize_text(entry.get("granularity"), max_length=24),
        "horizon_steps": int(entry.get("horizon_steps") or 0),
        "region": sanitize_text(entry.get("region") or "", max_length=80),
        "training_period": dict(entry.get("training_period") or {}),
        "test_period": dict(entry.get("test_period") or {}),
        "rows_used": int(entry.get("rows_used") or 0),
        "metrics": _metrics(entry),
        "status": "candidate",
        "status_origin": "ml_package_disk",
        "registry_status": sanitize_text(entry.get("status"), max_length=24),
        "artifact_uri": sanitize_text(entry.get("artifact_uri"), max_length=400),
        "feature_definition": list(entry.get("feature_definition") or []),
        "backtest": dict(entry.get("backtest") or {}),
        "limitations": [sanitize_text(item, max_length=600)
                        for item in (entry.get("limitations") or [])],
        "data_source_files": [str(item) for item in (entry.get("data_source_files") or [])][:20],
        "created_at": created,
        "first_seen_at": created,
        "last_synced_at": datetime.now(timezone.utc),
        "promoted_at": None,
        "promoted_by": None,
        "retired_at": None,
        "retired_by": None,
        "retire_reason": None,
        "evaluations": [],
        "artifact_present_on_disk": True,
    }


def prediction_document(result: dict[str, Any], *, requested: dict[str, Any],
                        actor_id: Optional[str], job_id: Optional[str] = None) -> Optional[dict]:
    """Build the ``predictions`` document, or ``None`` when mlkit refused to forecast."""
    status = str(result.get("status") or "error")
    if status != "forecast":
        return None
    model = dict(result.get("model") or {})
    name = sanitize_text(model.get("model_name"), max_length=120)
    version = sanitize_text(model.get("model_version"), max_length=60)
    values = list(result.get("values") or [])
    timestamps = [str(item) for item in (result.get("timestamps") or [])]
    uncertainty = result.get("uncertainty")
    lower = list((uncertainty or {}).get("lower") or []) if isinstance(uncertainty, dict) else []
    upper = list((uncertainty or {}).get("upper") or []) if isinstance(uncertainty, dict) else []
    series: list[dict[str, Any]] = []
    for index in range(len(values)):
        step = {
            "step": index + 1,
            "timestamp": timestamps[index] if index < len(timestamps) else None,
            "value": values[index],
        }
        if index < len(lower):
            step["lower"] = lower[index]
        if index < len(upper):
            step["upper"] = upper[index]
        series.append(step)
    numeric = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    region = sanitize_text(str(result.get("region") or requested.get("region") or ""), max_length=80)
    granularity = sanitize_text(str(result.get("granularity") or "daily"), max_length=24)
    document: dict[str, Any] = {
        "model_ref": f"{name}@{version}",
        "model_name": name,
        "model_version": version,
        "algorithm": sanitize_text(str(model.get("algorithm") or ""), max_length=48),
        "target_variable": sanitize_text(str(result.get("target_variable")), max_length=32),
        "granularity": granularity,
        "geographic_scope": {"region": region,
                             "station_id": sanitize_text(requested.get("station_id") or "",
                                                          max_length=64) or None},
        "region": region,
        "horizon": int(result.get("horizon_steps") or len(values)),
        "horizon_steps": int(result.get("horizon_steps") or len(values)),
        "validated_horizon": int(result.get("validated_horizon") or 0),
        "horizon_unit": granularity,
        "values": values,
        "timestamps": timestamps,
        "series": series,
        "units": sanitize_text(str(result.get("units") or "unknown"), max_length=24),
        "summary": {
            "steps_stored": len(values),
            "numeric_steps": len(numeric),
            "first_value": numeric[0] if numeric else None,
            "last_value": numeric[-1] if numeric else None,
            "min_value": min(numeric) if numeric else None,
            "max_value": max(numeric) if numeric else None,
            "mean_value": round(sum(numeric) / len(numeric), 4) if numeric else None,
        },
        "status": "forecast",
        "generated_at": datetime.now(timezone.utc),
        "origin_date": requested.get("origin_date"),
        "data_freshness": dict(result.get("data_freshness") or {}),
        "availability": dict(result.get("availability") or {}),
        "metrics": dict(result.get("metrics") or {}),
        "backtest_aggregate": dict(result.get("backtest_aggregate") or {}),
        "training_period": dict(result.get("training_period") or {}),
        "warnings": [sanitize_text(item, max_length=800) for item in (result.get("warnings") or [])],
        "requested_by": str(actor_id) if actor_id else None,
        "job_id": str(job_id) if job_id else None,
        "artifact_uri": sanitize_text(str(model.get("artifact_uri") or ""), max_length=400),
    }
    # Uncertainty is stored only when the fitted model really produced bands; otherwise the
    # package's own reason travels instead of a manufactured interval.
    if isinstance(uncertainty, dict) and uncertainty.get("lower"):
        document["uncertainty"] = {
            "method": uncertainty.get("method"),
            "coverage_nominal": uncertainty.get("coverage_nominal"),
            "lower": lower,
            "upper": upper,
            "caveat": uncertainty.get("caveat"),
        }
        document["has_uncertainty"] = True
    else:
        document["uncertainty"] = None
        document["has_uncertainty"] = False
        document["uncertainty_reason"] = sanitize_text(
            str(result.get("uncertainty_reason") or "the model returned no uncertainty bands"),
            max_length=600)
    return document


def _threshold_detection(item: dict[str, Any]) -> dict[str, Any]:
    """Put a raw ``check_thresholds`` row into the same shape as the other detectors.

    Severity comes from the package's own mapping (threshold-only caps at 'warning'), so no
    new scale is invented here; only the key names differ between detectors.
    """
    rule = item.get("rule") or {}
    breach = item.get("breach")
    unit = sanitize_text(str(rule.get("unit") or "1"), max_length=24)
    severity = mlkit().severity_for(None, item.get("breach_level"))
    return {
        "timestamp": item.get("timestamp"),
        "observed_value": item.get("value"),
        "baseline_value": None,
        "deviation_units": item.get("margin_beyond_limit"),
        "robust_sigma": None,
        "direction": "above" if breach == "above_max" else ("below" if breach == "below_min" else None),
        "season": {},
        "method": "threshold_limits",
        "severity": severity if severity != "none" else "info",
        "observable_deviation": (f"{item.get('value')} {unit} lies outside the configured plausibility "
                                 f"limits [{rule.get('min')}, {rule.get('max')}] {unit} "
                                 f"({item.get('breach_level')} breach)"),
        "note": ("Fixed plausibility-limit breach; the threshold detector uses no seasonal baseline, "
                 "so this is a sensor-fault candidate rather than a statement about local climate."),
        "breach": breach,
        "breach_level": item.get("breach_level"),
    }


def detection_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Every detection in one shape, whichever detector produced it."""
    rows: list[dict[str, Any]] = []
    for item in (result.get("detections") or []):
        if not isinstance(item, dict):
            continue
        rows.append(item if "observed_value" in item else _threshold_detection(item))
    return rows


def _detection_unit(result: dict[str, Any]) -> str:
    return sanitize_text(str(result.get("unit") or (result.get("limits") or {}).get("unit") or "1"),
                         max_length=24)


def anomaly_event_documents(result: dict[str, Any], *, job_id: Optional[str] = None,
                            actor_id: Optional[str] = None,
                            max_events: int = MAX_ANOMALY_EVENTS) -> list[dict[str, Any]]:
    """Turn ml detections into ``alert_events`` rows so the alerts engine can notify."""
    method = sanitize_text(str(result.get("requested", {}).get("method")
                               or (detection_rows(result) or [{}])[0].get("method")
                               or "unknown"), max_length=48)
    region = sanitize_text(str(result.get("region") or ""), max_length=80)
    variable = sanitize_text(str(result.get("variable") or ""), max_length=32)
    unit = _detection_unit(result)
    granularity = sanitize_text(str(result.get("requested", {}).get("granularity") or "daily"),
                                max_length=24)
    now = datetime.now(timezone.utc)
    rows = detection_rows(result)
    documents: list[dict[str, Any]] = []
    for detection in rows[:max(1, int(max_events))]:
        moment = parse_datetime(detection.get("timestamp")) or now
        observed = detection.get("observed_value")
        baseline = detection.get("baseline_value")
        explanation = sanitize_text(str(detection.get("observable_deviation") or ""), max_length=900)
        note = sanitize_text(str(detection.get("note") or ""), max_length=400)
        documents.append({
            "rule_id": None,
            "rule_name": f"ML anomaly scan ({method})",
            "dedupe_key": fingerprint("ml-anomaly", method, region, variable,
                                      str(detection.get("timestamp")), str(observed)),
            "severity": sanitize_text(str(detection.get("severity") or "info"), max_length=16),
            "status": "open",
            "detected_at": moment,
            "description": f"{variable} in {region}: {explanation}"[:1000],
            "condition": "anomaly",
            "target_variable": variable,
            "region": region,
            "scope_label": region,
            "source": "machine_learning",
            "reference": {
                "kind": "observation",
                "observation_id": None,
                "prediction_id": None,
                "dataset_id": None,
                "timestamp": moment,
                "granularity": granularity,
                "region": region,
                "station_id": None,
                "variable": variable,
                "value": observed,
                "unit": unit,
                "model_ref": None,
                "method": method,
                "scan_job_id": str(job_id) if job_id else None,
            },
            "comparison": {
                "observed_value": observed,
                "unit": unit,
                "threshold": None,
                "baseline": baseline,
                "deviation": detection.get("deviation_units"),
                "basis": "ml_seasonal_climatology",
                "robust_sigma": detection.get("robust_sigma"),
                "direction": detection.get("direction"),
                "season": dict(detection.get("season") or {}),
            },
            "explanation": f"{explanation} {note}".strip()[:1500],
            "detector": {
                "method": method,
                "engine": "ml package (ml/anomaly/*)",
                "params": dict(result.get("params") or result.get("requested") or {}),
                "threshold_summary": dict(result.get("threshold_summary") or {}),
            },
            "acknowledged_by": None,
            "acknowledged_at": None,
            "resolved_by": None,
            "resolved_at": None,
            "notification_ids": [],
            "audit": [{"at": now, "actor": sanitize_text(actor_id or "system", max_length=64),
                       "action": "created", "reason": f"anomaly scan method={method}"}],
            "created_at": now,
            "updated_at": now,
        })
    return documents


def anomaly_scan_summary(result: dict[str, Any], *, events_created: int, events_deduped: int,
                         events_skipped: int, job_id: Optional[str] = None) -> dict[str, Any]:
    """Scan outcome with the real baseline it compared against; refusals stay refusals."""
    rows = detection_rows(result)
    by_severity: dict[str, int] = {}
    for row in rows:
        key = str(row.get("severity") or "unknown")
        by_severity[key] = by_severity.get(key, 0) + 1
    top = [
        {
            "timestamp": row.get("timestamp"), "observed_value": row.get("observed_value"),
            "baseline_value": row.get("baseline_value"),
            "deviation_units": row.get("deviation_units"),
            "robust_sigma": row.get("robust_sigma"), "severity": row.get("severity"),
            "direction": row.get("direction"), "explanation": row.get("observable_deviation"),
        }
        for row in rows[:10]
    ]
    baselines = [row.get("baseline_value") for row in rows if row.get("baseline_value") is not None]
    status = str(result.get("status") or "unknown")
    return {
        "status": status,
        "ran": status in ("ok", "no_data", "insufficient_baseline"),
        "region": result.get("region"),
        "variable": result.get("variable"),
        "unit": _detection_unit(result),
        "method": (result.get("requested") or {}).get("method"),
        "checked_slots": result.get("checked"),
        "flagged": result.get("flagged", len(rows)),
        "count_by_severity": by_severity,
        "top_events": top,
        "baseline": {
            "kind": "ml detector baseline (this location's own record, never a global mean)",
            "window_days": (result.get("params") or {}).get("window_days"),
            "min_baseline_sample": (result.get("params") or {}).get("min_baseline_sample"),
            "reporting_floor": (result.get("params") or {}).get("z_info"),
            "leave_one_out": (result.get("params") or {}).get("leave_one_out"),
            "slots_with_insufficient_baseline": result.get("insufficient_baseline_slots"),
            "slots_with_degenerate_baseline": result.get("degenerate_baseline_slots"),
            "example_values": [round(float(v), 3) for v in baselines[:5]],
            "threshold_limits": (result.get("limits") or None),
            "threshold_summary": dict(result.get("threshold_summary") or {}),
        },
        "limits": result.get("limits"),
        "fit_rows": result.get("fit_rows"),
        "scored_rows": result.get("scored_rows"),
        "violations": result.get("violations"),
        "notes": [str(item) for item in (result.get("notes") or [])],
        "reason": result.get("reason"),
        "truncated_detections": result.get("truncated"),
        "events_written": events_created,
        "events_deduped": events_deduped,
        "events_not_written": events_skipped,
        "duration_seconds": result.get("duration_seconds"),
        "job_id": str(job_id) if job_id else None,
        "note": str(result.get("note") or "Deviations are measured against this location's own "
                                          "recorded climatology; they are observations, not "
                                          "causal attributions."),
    }


def anomaly_summary_document(result: dict[str, Any], *, job_id: Optional[str] = None) -> Optional[dict]:
    """``analytics_summaries`` row so anomaly-type alert rules get a real baseline."""
    kit = mlkit()
    region = sanitize_text(str(result.get("region") or ""), max_length=80)
    variable = sanitize_text(str(result.get("variable") or ""), max_length=32)
    if not region or not variable:
        return None
    frame = observations()
    assembled = kit.SeriesAssembler(frame).assemble(region, variable,
                                                    (result.get("requested") or {}).get(
                                                        "granularity") or "daily")
    observed = assembled.series.dropna()
    if observed.empty:
        return None
    window = int((result.get("params") or {}).get("window_days") or 0)
    import numpy as np

    values = observed.to_numpy(dtype="float64")
    payload = {
        "summary_type": "anomaly_features",
        "computed_at": datetime.now(timezone.utc),
        "engine_used": "ml package (pandas)",
        "region": region,
        "scope": {"region": region},
        "scope_label": region,
        "variable": variable,
        "window_days": window or None,
        "granularity": str(observed.index.freq or "daily"),
        "job_id": str(job_id) if job_id else None,
        "features": {
            variable: {
                "mean": round(float(np.mean(values)), 4),
                "std": round(float(np.std(values, ddof=1)), 4) if len(values) > 1 else None,
                "median": round(float(np.median(values)), 4),
                "min": round(float(np.min(values)), 4),
                "max": round(float(np.max(values)), 4),
                "sample_size": int(len(values)),
                "unit": _detection_unit(result),
                "current_value": round(float(values[-1]), 4),
                "current_timestamp": str(observed.index.max()),
                "robust_sigma_floor": (result.get("params") or {}).get("z_info"),
                "detections": int(result.get("flagged") or 0),
                "method": (result.get("requested") or {}).get("method"),
                "basis": "recorded observations at this location over the full window",
            }
        },
        "provenance_files": [str(item) for item in
                             (assembled.availability.provenance_files or [])][:10],
    }
    return payload


__all__ = ["mlkit", "observations", "cache_state", "availability", "trainable_regions",
           "validate_ref", "find_disk_entry", "disk_registry", "train", "evaluate", "forecast",
           "scan_anomalies", "anomaly_methods", "capabilities", "registry_document",
           "prediction_document", "anomaly_event_documents", "anomaly_scan_summary",
           "anomaly_summary_document", "resolve_ladder", "ANOMALY_METHODS", "GRANULARITIES",
           "INLINE_FORECAST_MAX_STEPS", "MAX_ANOMALY_EVENTS"]
