"""HTTP layer for the big-data processing workspace.

The catalogue is the only authority on what may run: this module never accepts a command,
script, module path or expression, and every engine it reports is the engine the pipeline
module actually used.
"""

from __future__ import annotations

from pathlib import Path, PurePath
from typing import Any

from apps.authentication.permissions import role_allows
from apps.datasets import repository as datasets
from apps.processing import catalogue, jobs, repository, runner
from common.audit import audit
from common.datalake import LAYERS, list_layer
from common.db import find_one, oid
from common.env import ENV
from common.exceptions import Forbidden, NotFound, ServiceUnavailableConfig, ValidationError
from common.pagination import normalize_pagination
from common.paths import ensure_paths
from common.responses import paginated
from common.tasks import retry, submit, sweep_stale_jobs
from common.validators import ProcessingJobRequest, parse
from common.views import ApiView


class WorkflowListView(ApiView):
    """The catalogue plus what this runtime can honestly do with each entry."""

    required_capability = "processing.view"
    http_method_names = ["get"]

    def get(self, request):
        params = self.params(request)
        dataset = catalogue.dataset_slug_for(params.get("dataset", "")) if params.get("dataset") else ""
        runtime = catalogue.runtime_availability()
        modules = catalogue.registered_modules()
        index = catalogue.layer_index()
        last_runs = repository.last_run_per_workflow()
        items = [catalogue.describe(item, dataset=dataset, modules=modules, index=index,
                                    last_run=last_runs.get(str(item["id"])))
                 for item in catalogue.workflows()]
        return self.ok(request, {
            "runtime": runtime,
            "workflows": items,
            "analytics": repository.summary_totals(),
            "jobs": repository.status_totals(),
            "availability_note": "engine_planned is the engine attempted first; the engine that "
                                 "actually produced each figure is recorded as engine_used on the "
                                 "summary and on the job.",
        }, meta={"dataset_filter": dataset or None})


class WorkflowDetailView(ApiView):
    """One workflow: contract, live availability and a sample of the last real run."""

    required_capability = "processing.view"
    http_method_names = ["get"]

    def get(self, request, workflow_id: str):
        item = catalogue.resolve_workflow(workflow_id)
        params = self.params(request)
        dataset = catalogue.dataset_slug_for(params.get("dataset", "")) if params.get("dataset") else ""
        described = catalogue.describe(item, dataset=dataset)
        last_summary = repository.latest_summary_for_workflow(item["id"])
        sample = None
        if last_summary:
            outputs = last_summary.get("outputs") or []
            sample = {
                "summary_id": last_summary.get("id"),
                "processing_job_id": last_summary.get("processing_job_id"),
                "engine_used": last_summary.get("engine_used"),
                "rows_out": last_summary.get("rows_out"),
                "outputs": outputs,
                "created_at": last_summary.get("created_at"),
                "preview": runner.output_preview(outputs[0], limit=int(params.get("sample_rows", 10) or 10))
                if outputs else {"available": False, "reason": "the last run recorded no output files"},
            }
        recent_jobs, job_total = repository.list_jobs(workflow=item["id"], page=1, page_size=5)
        return self.ok(request, {
            "workflow": described,
            "parameter_schema": _parameter_schema(item),
            "output_path_template": item.get("output_path"),
            "last_run_sample": sample,
            "recent_jobs": recent_jobs,
            "recent_job_total": job_total,
            "submit_contract": {
                "endpoint": "/api/v1/processing/jobs",
                "body": {"workflow": item["id"], "dataset_id": "<optional Mongo id>",
                         "parameters": {name: "<value>" for name in catalogue.allowed_parameter_names(item)}},
            },
        })


class JobListView(ApiView):
    required_capability = "processing.view"

    def get(self, request):
        params = self.params(request)
        page, size, _skip = normalize_pagination(request.query_params)
        items, total = repository.list_jobs(
            status=params.get("status", ""), workflow=params.get("workflow", ""),
            dataset_id=params.get("dataset_id", ""), page=page, page_size=size)
        return paginated(items, total=total, page=page, page_size=size,
                         extra={"status_totals": repository.status_totals(),
                                "catalogue_workflows": catalogue.workflow_ids()},
                         request=request)

    def post(self, request):
        if not role_allows(request.auth_identity.role, "processing.run"):
            raise Forbidden("Your role cannot start processing jobs.", code="permission_denied")
        payload: ProcessingJobRequest = parse(ProcessingJobRequest, self.body(request))
        item = catalogue.resolve_workflow(payload.workflow)
        parameters = dict(payload.parameters or {})

        dataset_name = ""
        if payload.dataset_id:
            dataset = datasets.get_optional(payload.dataset_id)
            if not dataset:
                raise NotFound(f"No dataset with id '{payload.dataset_id}'.")
            dataset_name = catalogue.dataset_slug_for(dataset.get("name", ""))
            parameters.setdefault("dataset", dataset_name)

        parameters = catalogue.validate(item, parameters)
        _preflight(item, parameters)

        run_token = jobs.new_run_token(item["id"])
        kwargs = jobs.task_kwargs(workflow=item["id"], params=parameters,
                                  dataset_id=payload.dataset_id or "", run_token=run_token,
                                  created_by=request.auth_identity.user_id)
        job = submit(jobs.TASK_KIND, jobs.run_processing_job, kwargs=kwargs,
                     created_by=request.auth_identity.user_id, payload=dict(kwargs),
                     label=f"{item.get('title')} ({item['id']})")
        engine_planned, note = catalogue.planned_engine(item)
        audit(request, "processing.job_create", resource_type="processing_jobs",
              resource_id=str(job.get("_id")),
              metadata={"workflow": item["id"], "parameters": parameters,
                        "engine_planned": engine_planned, "dataset_id": payload.dataset_id})
        return self.created(request, {
            "job_id": str(job.get("_id")),
            "job_ref": job.get("job_ref"),
            "workflow": item["id"],
            "status": job.get("status"),
            "task_engine": job.get("engine"),
            "engine_planned": engine_planned,
            "engine_note": note,
            "parameters": parameters,
            "poll": f"/api/v1/processing/jobs/{job.get('_id')}",
        })


class JobDetailView(ApiView):
    required_capability = "processing.view"
    http_method_names = ["get"]

    def get(self, request, job_id: str):
        return self.ok(request, repository.job_detail(job_id))


class JobRetryView(ApiView):
    required_capability = "processing.view"
    throttle_scope = "processing"
    http_method_names = ["post"]

    def post(self, request, job_id: str):
        if not role_allows(request.auth_identity.role, "processing.run"):
            raise Forbidden("Your role cannot retry processing jobs.", code="permission_denied")
        parsed = oid(job_id)
        document = find_one("processing_jobs", {"_id": parsed}) if parsed else None
        if not document or document.get("kind") != repository.PROCESSING_KIND:
            raise NotFound("No processing job with that id exists in this workspace.")
        kwargs = jobs.kwargs_from_payload(document.get("payload"))
        result = retry(parsed, jobs.run_processing_job, kwargs=kwargs)
        if result is None:
            raise NotFound("No processing job with that id exists in this workspace.")
        audit(request, "processing.job_retry", resource_type="processing_jobs", resource_id=job_id,
              metadata={"workflow": kwargs["workflow"], "retryable": result.get("retryable"),
                        "attempt": document.get("attempt")})
        return self.ok(request, {
            **result,
            "job_id": job_id,
            "workflow": kwargs["workflow"],
            "parameters": kwargs["params"],
            "poll": f"/api/v1/processing/jobs/{job_id}",
        })


class SummaryListView(ApiView):
    """Analytical figures with the engine that computed each one."""

    required_capability = "processing.view"
    http_method_names = ["get"]

    def get(self, request):
        params = self.params(request)
        page, size, _skip = normalize_pagination(request.query_params)
        items, total = repository.list_summaries(
            workflow=params.get("workflow", ""), engine=params.get("engine", ""),
            dataset=params.get("dataset", ""), job_id=params.get("job_id", ""),
            page=page, page_size=size)
        return paginated(items, total=total, page=page, page_size=size,
                         extra={"totals": repository.summary_totals(),
                                "engine_legend": {
                                    "pyspark": "computed by a real SparkSession in this runtime",
                                    "pandas": "computed by the pandas fallback; the same job id, "
                                              "parameters and output schema",
                                    "python-validation": "computed by the pure-Python validation engine",
                                    "spark_error": "present when Spark was attempted and did not run"},
                                "catalogue_version": catalogue.load().get("catalogue_version")},
                         request=request)


class MapReduceCatalogueView(ApiView):
    """Hadoop Streaming pairs, how to run them for real, and the data-lake listing."""

    required_capability = "processing.view"
    http_method_names = ["get"]

    def get(self, request):
        params = self.params(request)
        dataset = catalogue.dataset_slug_for(params.get("dataset", "")) if params.get("dataset") else ""
        pairs = _streaming_pairs(dataset=dataset)
        lake = _lake_listing(dataset=dataset)
        return self.ok(request, {
            "engine": "hadoop-streaming",
            "mode": ENV.raw("MAPREDUCE_MODE", "streaming-local"),
            "cluster": {
                "hadoop_home": ENV.raw("HADOOP_HOME") or None,
                "hadoop_bin": ENV.raw("HADOOP_BIN") or None,
                "hadoop_streaming_jar": ENV.raw("HADOOP_STREAMING_JAR") or None,
                "configured": bool(ENV.raw("HADOOP_HOME") or ENV.raw("HADOOP_BIN")),
                "cluster_note": cluster_note(),
                "detail": ("A Hadoop launcher is configured; commands below are for you to run "
                           "on the cluster - this API does not start MapReduce jobs.")
                if (ENV.raw("HADOOP_HOME") or ENV.raw("HADOOP_BIN")) else
                ("No Hadoop installation is configured (HADOOP_HOME/HADOOP_BIN are empty), so no "
                 "MapReduce job has run on a cluster from this runtime."),
            },
            "pairs": pairs,
            "data_lake": lake,
            "honesty": "Nothing here has been executed by this request; the local runner and the "
                       "cluster command lines are documented for operators, and any 'last_local_run' "
                       "block is read from a manifest a previous run actually wrote.",
        })


class StreamStatusView(ApiView):
    """Live streaming state, reported from checkpoints and configuration only."""

    required_capability = "processing.view"
    http_method_names = ["get"]

    def get(self, request):
        ensure_paths()
        kafka_brokers = ENV.raw("KAFKA_BROKERS")
        topic = ENV.raw("KAFKA_CLIMATE_TOPIC", "earthscape.climate.observations")
        group = ENV.raw("KAFKA_CONSUMER_GROUP", "earthscape-processing")
        probe: dict[str, Any] = {"attempted": False}
        if kafka_brokers:
            try:
                from streaming.kafka_ingest import status as kafka_status

                probe = {"attempted": True, **kafka_status()}
            except Exception as exc:
                probe = {"attempted": True, "status": "unavailable",
                         "reason": f"probe raised {type(exc).__name__}"}
        else:
            probe = {"attempted": True, "status": "unavailable",
                     "reason": "KAFKA_BROKERS is not set, so there is no broker to contact"}

        poller: dict[str, Any] = {"available": False,
                                  "reason": "the polling ingest module is not importable here"}
        try:
            from streaming.polling_ingest import load_checkpoint

            poller = {"available": True, **load_checkpoint()}
        except Exception as exc:
            poller = {"available": False, "reason": f"{type(exc).__name__}"}

        services = [entry.as_dict() for entry in catalogue_startup_entries()]
        checkpoints = repository.stream_checkpoints()
        kafka_reachable = str(probe.get("status")) == "ok"
        return self.ok(request, {
            "kafka": {
                "configured": bool(kafka_brokers),
                "brokers": kafka_brokers or None,
                "topic": topic,
                "consumer_group": group,
                "dead_letter_topic": ENV.raw("KAFKA_DEAD_LETTER_TOPIC") or None,
                "event_stream_enabled": ENV.flag("EVENT_STREAM_ENABLED", False),
                "probe": probe,
                "reachable": kafka_reachable,
                "statement": ("Kafka is reachable and the topic is resolvable." if kafka_reachable else
                              f"Kafka is NOT reachable: {probe.get('reason') or probe.get('status')}. "
                              "Nothing is being consumed right now."),
            },
            "polling": {
                "enabled": ENV.flag("POLLER_ENABLED", False),
                "interval_seconds": ENV.integer("POLL_INTERVAL_SECONDS",
                                                ENV.integer("POLLER_INTERVAL_SECONDS", 900)),
                "checkpoint": poller,
                "statement": "Periodic polling is not a continuous sensor stream; each cycle calls a "
                             "provider endpoint and appends a validated JSONL batch to the raw layer.",
            },
            "checkpoints": checkpoints,
            "services": services,
            "honesty": "This endpoint reads persisted checkpoints and configuration; it never starts "
                       "a consumer and never reports a stream that is not running.",
        })


class StaleJobSweepView(ApiView):
    """Marks jobs orphaned by a restart as failed instead of leaving them 'running'."""

    required_capability = "ops.logs"
    throttle_scope = "processing"
    http_method_names = ["post"]

    def post(self, request):
        body = self.body(request)
        try:
            max_age = int(body.get("max_age_minutes", 120))
        except (TypeError, ValueError):
            raise ValidationError("'max_age_minutes' must be an integer between 1 and 1440.")
        if not 1 <= max_age <= 1440:
            raise ValidationError("'max_age_minutes' must be between 1 and 1440.")
        marked = sweep_stale_jobs(max_age_minutes=max_age)
        audit(request, "processing.sweep", resource_type="processing_jobs",
              metadata={"max_age_minutes": max_age, "marked_failed": marked})
        return self.ok(request, {
            "marked_failed": marked,
            "max_age_minutes": max_age,
            "status_totals_after": repository.status_totals(),
            "note": "Jobs left 'running' or 'queued' with no worker behind them are now recorded as "
                    "failed with 'stale: worker no longer reporting'; retry them explicitly.",
        })


def _parameter_schema(item: dict) -> list[dict]:
    schema = []
    for spec in item.get("parameters", []) or []:
        entry = {
            "name": spec.get("name"),
            "type": spec.get("type"),
            "required": bool(spec.get("required")),
            "default": spec.get("default"),
            "minimum": spec.get("minimum"),
            "maximum": spec.get("maximum"),
            "allowed_values": spec.get("values"),
        }
        schema.append(entry)
    return schema


def _preflight(item: dict, parameters: dict) -> None:
    """Fail fast and honestly instead of queueing a job that cannot produce results."""
    modules = catalogue.registered_modules()
    if not modules.get(str(item["id"])):
        raise ServiceUnavailableConfig(
            f"Workflow '{item['id']}' is catalogued but no pipeline module is registered for it.",
            details=[f"Registered workflows: {', '.join(sorted(modules)) or 'none'}."])
    if parameters.get("paths"):
        return
    inputs = catalogue.input_state(item, dataset=str(parameters.get("dataset") or ""))
    if not inputs["populated"]:
        layer = inputs["layer"]
        details = [f"Required inputs for this workflow: {', '.join(str(x) for x in item.get('required_inputs', []))}",
                   f"Datasets currently present in '{layer}': {', '.join(inputs['datasets_present']) or 'none'}",
                   "Run the 'normalize_observations' workflow first, or supply an explicit 'paths' parameter."]
        raise ValidationError(
            f"The '{layer}' input layer is empty"
            + (f" for dataset '{parameters.get('dataset')}'" if parameters.get("dataset") else "")
            + ", so this job would produce no results.",
            details=details)


def _streaming_pairs(dataset: str = "") -> list[dict]:
    ensure_paths()
    try:
        from mapreduce import local_runner as runner_module
    except Exception as exc:
        raise ServiceUnavailableConfig(
            "The Hadoop Streaming job definitions are not importable in this runtime.",
            details=[type(exc).__name__])
    streaming_dir = runner_module.STREAMING_DIR
    script_root = _script_root(streaming_dir)
    base = _hdfs_base()
    pairs = []
    for name, spec in runner_module.JOBS.items():
        input_path = _lake_input_path(dataset)
        hdfs_input = f"{base}/datalake/{input_path}" if input_path else f"{base}/datalake/<layer>/dataset=<id>"
        hdfs_output = f"{base}/datalake/processed/dataset={dataset or 'mixed'}/{name}"
        pairs.append({
            "job": name,
            "mapper": f"data-pipeline/mapreduce/streaming/{spec['mapper']}",
            "reducer": f"data-pipeline/mapreduce/streaming/{spec['reducer']}",
            "output_schema": f"data-pipeline/mapreduce/streaming/{spec['output_schema']}",
            "columns": spec["columns"],
            "combiner_capable": True,
            "cluster_runnable": bool(input_path),
            "cluster_command": _cluster_command(script_root, spec, hdfs_input, hdfs_output),
            "local_command": (
                f"python data-pipeline/mapreduce/local_runner.py "
                f"\"{input_path or '<local jsonl input>'}\" --job {name} "
                f"--dataset {dataset or 'mixed'}"),
            "last_local_run": _last_local_run(name, dataset=dataset),
        })
    return pairs


def _script_root(streaming_dir: Any) -> str:
    """Absolute location of the mapper/reducer scripts as seen by the cluster nodes.

    Hadoop Streaming ships the scripts named by -files, so the path has to resolve on the
    machine that submits the job. Set HADOOP_STREAMING_LOCAL_ROOT when the repository lives
    somewhere else there; the POSIX separator keeps the command line valid on the Linux
    images even when this API runs on Windows.
    """
    configured = ENV.raw("HADOOP_STREAMING_LOCAL_ROOT")
    base = Path(configured).expanduser() if configured else Path(str(streaming_dir))
    return PurePath(str(base)).as_posix().rstrip("/")


def cluster_note() -> str:
    """What a cluster operator must do before the command lines below can run."""
    source = ENV.raw("HADOOP_STREAMING_LOCAL_ROOT") or "<the data-pipeline checkout on the submitting node>"
    return (
        f"Mapper and reducer are shipped from {source}; that directory must exist on the node that "
        "submits the job, and the -input path must already be in HDFS (see the "
        "infrastructure/hadoop configuration and the hadoop-datanode service in the compose file). "
        "This API prints the command line and never submits a MapReduce job, so no cluster job has "
        "run merely because this endpoint answered."
    )


def _cluster_command(script_root: str, spec: dict, hdfs_input: str, hdfs_output: str) -> str:
    jar = ENV.raw("HADOOP_STREAMING_JAR") or "$HADOOP_HOME/share/hadoop/tools/lib/hadoop-streaming-*.jar"
    mapper = spec["mapper"]
    reducer = spec["reducer"]
    lines = [
        "hadoop jar " + jar + " org.apache.hadoop.streaming.HadoopStreaming",
        "  -files " + script_root + "/_mrlib.py," + script_root + "/" + mapper
        + "," + script_root + "/" + reducer,
        "  -input " + hdfs_input,
        "  -output " + hdfs_output,
        "  -mapper \"python3 " + mapper + "\"",
        "  -reducer \"python3 " + reducer + "\"",
        "  -jobconf stream.num.map.output.key.fields=2",
        "  -jobconf mapreduce.job.reduces=2",
        "  -jobconf mapreduce.job.reduce.slowstart.completedmaps=0.8",
    ]
    return " \\\n".join(lines)


def _hdfs_base() -> str:
    """NameNode URI of the lake root, matching the infrastructure/hadoop configuration."""
    explicit = ENV.raw("HDFS_NAMENODE_URI")
    if explicit:
        return explicit.rstrip("/")
    host = ENV.raw("HDFS_NAMENODE_HOST", "hadoop-namenode:9000")
    root = ENV.raw("HDFS_ROOT", "/earthscape").rstrip("/")
    return f"hdfs://{host}{root}"


def _lake_input_path(dataset: str) -> str:
    """A real HDFS input location: MapReduce reads directories recursively.

    Prefers the whole validated dataset partition and falls back to the single combined file;
    an empty answer means the lake genuinely has nothing for that dataset, which the caller
    reports instead of printing a path that does not exist.
    """
    if not dataset:
        return ""
    entries = list_layer("validated", dataset=dataset)
    if not entries:
        return ""
    combined = [entry["relative_path"] for entry in entries if entry["name"] == "combined.jsonl"]
    if combined:
        return combined[0].rsplit("/", 1)[0]
    first = entries[0]["relative_path"]
    return first.split("/year=")[0] if "/year=" in first else first.rsplit("/", 1)[0]


def _last_local_run(job: str, dataset: str = "") -> dict:
    """Read a manifest a previous local run actually wrote; never invent numbers."""
    from common.datalake import read_local

    wanted = f"processed/dataset={dataset}/{job}/manifest.json" if dataset else None
    candidates = []
    for entry in list_layer("processed"):
        if entry["relative_path"].endswith(f"/{job}/manifest.json"):
            candidates.append(entry["relative_path"])
    target = wanted if wanted in candidates else (candidates[0] if candidates else None)
    if not target:
        return {"available": False, "reason": f"no local run manifest recorded for '{job}' yet"}
    import json

    try:
        manifest = json.loads(read_local(target).decode("utf-8"))
    except Exception as exc:
        return {"available": False, "path": target, "reason": f"manifest unreadable: {type(exc).__name__}"}
    return {"available": True, "path": target, "manifest": manifest}


def _lake_listing(dataset: str = "") -> dict:
    from common.datalake import status as lake_status

    listing: dict[str, Any] = {}
    for layer in LAYERS:
        entries = list_layer(layer, dataset=dataset)
        listing[layer] = {
            "files": len(entries),
            "bytes": int(sum(int(entry.get("bytes") or 0) for entry in entries)),
            "entries": entries[:25],
            "truncated": len(entries) > 25,
        }
    return {"engine": lake_status(), "layers": listing, "dataset_filter": dataset or None}


def catalogue_startup_entries() -> list:
    from common.env import startup_status

    keep = {"kafka", "spark", "hdfs", "mapreduce", "background-tasks"}
    return [entry for entry in startup_status() if entry.name in keep]
