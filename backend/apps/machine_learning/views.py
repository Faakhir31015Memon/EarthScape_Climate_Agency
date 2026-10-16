"""HTTP layer for the ML surface: registry, training jobs, forecasts, anomaly scans.

Every number leaves through ``bridge`` (the ``ml`` package) or MongoDB (``repository``). A model
ref is treated as a registry lookup key, never as a path; refusals from the package travel back
with their own reason instead of a made-up result; anything long-running goes through
``common.tasks.submit``.
"""

from __future__ import annotations

from typing import Any, Optional

from apps.authentication.permissions import role_allows
from apps.machine_learning import bridge, jobs, repository
from common.audit import audit
from common.exceptions import Forbidden, NotFound, ValidationError
from common.pagination import normalize_pagination
from common.responses import paginated
from common.tasks import submit
from common.validators import (AnomalyScanRequest, ForecastRequest, TrainingRequest, parse)
from common.views import ApiView

MODEL_COLLECTION = "model_registry"
PREDICTION_COLLECTION = "predictions"
EVENT_COLLECTION = "alert_events"


def _actor(request) -> str:
    identity = getattr(request, "auth_identity", None)
    return str(getattr(identity, "user_id", None) or "anonymous")


def _role(request) -> str:
    identity = getattr(request, "auth_identity", None)
    return str(getattr(identity, "role", "") or "")


def _may_register(request) -> bool:
    return role_allows(_role(request), "model.register")


def _int_param(params: dict[str, str], name: str) -> Optional[int]:
    raw = params.get(name)
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValidationError(f"'{name}' must be an integer.") from None


def _bool_param(params: dict[str, str], name: str, default: bool = True) -> bool:
    raw = params.get(name)
    if raw in (None, ""):
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _job_snapshot(raw: dict[str, Any]) -> dict[str, Any]:
    """``tasks.submit`` returns the raw row: give the client the id/poll shape we document."""
    return repository.job_summary(raw)


def _optional_body(request) -> dict[str, Any]:
    """Body of a DELETE, which may legitimately be empty while still naming a content type."""
    try:
        return request.data if isinstance(request.data, dict) else {}
    except Exception:
        return {}


def _registry_row(ref: str) -> dict[str, Any]:
    """Mongo is the authority for lifecycle: a model target must be a registered row."""
    try:
        return repository.require_row(ref)
    except NotFound as exc:
        raise NotFound(
            f"'{ref}' is not in the model registry.",
            details=list(exc.details) + ["POST /api/v1/models/sync imports the artifacts already "
                                         "saved on disk into the registry."]) from exc


def _resolve_registry_ref(raw: str) -> tuple[str, dict[str, Any]]:
    """Resolve a client ref to (registry ref, row); a bare name means its newest version.

    Resolution goes through the package's registry entries (a key lookup over saved artifacts);
    the string is never treated as a path, and the row must exist in MongoDB.
    """
    ref = bridge.validate_ref(raw)
    try:
        return ref, repository.require_row(ref)
    except NotFound:
        pass
    disk = bridge.find_disk_entry(ref)
    resolved = str(disk["model_ref"])
    return resolved, _registry_row(resolved)


# --------------------------------------------------------------------------- models
class ModelCapabilitiesView(ApiView):
    """What the ML runtime can actually do right now (which tiers are importable)."""

    required_capability = "platform.view"

    def get(self, request):
        return self.ok(request, bridge.capabilities())


class ModelAvailabilityView(ApiView):
    """Per-slice data verdicts: what can be trained on today, and what cannot."""

    required_capability = "platform.view"
    throttle_scope = "processing"

    def get(self, request):
        params = self.params(request)
        report = bridge.availability(min_rows=_int_param(params, "min_rows"),
                                     region=params.get("region"), variable=params.get("variable"))
        report["registry"] = repository.registry_status_counts()
        report["next_step"] = ("POST /api/v1/models/train with one trainable slice, or "
                               "POST /api/v1/models/sync to import artifacts already on disk")
        return self.ok(request, report)


class ModelTrainView(ApiView):
    """Fit the model ladder on one slice; always a background job, always logged."""

    required_capability = "model.train"
    throttle_scope = "training"

    def post(self, request):
        payload = parse(TrainingRequest, self.body(request))
        _ladder, rejected = bridge.resolve_ladder(payload.models)
        if rejected:
            raise ValidationError("Unsupported model name(s) in request.",
                                  details=[f"'{name}' is not in the ml model ladder" for name in rejected])
        region = (payload.region or "").strip()
        if not region:
            trainable = bridge.trainable_regions(payload.target_variable, payload.granularity)
            raise ValidationError(
                "Training needs an explicit region: the row gate is decided per "
                "(region, variable, granularity) slice.",
                details=[f"regions clearing the gate for {payload.target_variable}/"
                         f"{payload.granularity}: {', '.join(trainable) if trainable else 'none'}",
                         "GET /api/v1/models/available-data for the full per-slice verdict"])
        run_token = jobs.new_run_token()
        kwargs = {"target_variable": payload.target_variable, "region": region,
                  "granularity": payload.granularity, "horizon_steps": payload.horizon_steps,
                  "folds": 5, "models": payload.models, "date_range": payload.date_range,
                  "station_id": payload.station_id,
                  "register_as_candidate": payload.register_as_candidate,
                  "auto_promote": payload.auto_promote, "allow_register": _may_register(request),
                  "actor_id": _actor(request), "run_token": run_token}
        job = _job_snapshot(submit(
            "model.train", jobs.run_training, kwargs=kwargs, created_by=_actor(request),
            payload={"run_token": run_token, "region": region, "target_variable": payload.target_variable,
                    "granularity": payload.granularity, "horizon_steps": payload.horizon_steps,
                    "date_range": payload.date_range, "station_id": payload.station_id,
                    "ladder": payload.models,
                    "register_as_candidate": payload.register_as_candidate,
                    "auto_promote": payload.auto_promote, "actor_role": _role(request)},
            label=f"Train {payload.target_variable}-{region}-h{payload.horizon_steps}"))
        audit(request, "model.train_submit", resource_type=MODEL_COLLECTION,
              resource_id=str(job["id"]),
              metadata={"region": region, "target_variable": payload.target_variable,
                        "horizon_steps": payload.horizon_steps, "ladder": payload.models,
                        "register_as_candidate": payload.register_as_candidate,
                        "auto_promote": payload.auto_promote})
        return self.created(request, {
            "job": job,
            "poll": f"/api/v1/models/jobs/{job['id']}",
            "runs_in_background": True,
            "note": "Training walks the ladder (naive -> seasonal_naive -> linear_ridge -> "
                    "random_forest -> xgboost if importable) over a chronological split plus "
                    "walk-forward folds; the job log records the split dates, every model tried, "
                    "the metrics and whether the leakage gate fired.",
            "data_cache": bridge.cache_state(),
        })


class ModelJobListView(ApiView):
    required_capability = "processing.view"

    def get(self, request):
        page, size, _skip = normalize_pagination(request.query_params)
        params = self.params(request)
        items, total = repository.list_jobs(params, page=page, page_size=size)
        return paginated(items, total=total, page=page, page_size=size,
                         extra={"job_counts": repository.job_counts(), "kinds": list(repository.JOB_KINDS)},
                         request=request)


class ModelJobDetailView(ApiView):
    required_capability = "processing.view"

    def get(self, request, job_id: str):
        return self.ok(request, repository.job_detail(job_id))


class ModelRegistryListView(ApiView):
    required_capability = "platform.view"

    def get(self, request):
        page, size, _skip = normalize_pagination(request.query_params)
        params = self.params(request)
        items, total = repository.list_registry(params, page=page, page_size=size)
        counts = repository.registry_status_counts()
        extra = {"status_counts": counts,
                 "statuses": list(repository.MODEL_STATUSES),
                 "filters": ["region", "variable", "granularity", "algorithm", "status", "horizon",
                             "artifact=missing", "search"]}
        if not total:
            extra["empty"] = ("no models in the registry yet; artifacts already on disk are imported "
                              "with POST /api/v1/models/sync")
        return paginated(items, total=total, page=page, page_size=size, extra=extra, request=request)


class ModelSyncView(ApiView):
    """Import the registry entries the ml package actually has on disk into MongoDB."""

    required_capability = "model.register"
    throttle_scope = "processing"

    def post(self, request):
        documents = bridge.disk_registry()
        result = repository.sync_registry(documents)
        audit(request, "model.registry_sync", resource_type=MODEL_COLLECTION, resource_id=None,
              metadata=result)
        return self.ok(request, {
            "sync": result,
            "models": [doc["model_ref"] for doc in documents],
            "registry": repository.registry_status_counts(),
            "note": "Only artifacts readable by the package's own store are imported; lifecycle "
                    "status set in MongoDB is preserved unless a slice gains its first 'registered' "
                    "model, and rows whose artifact disappeared from disk are flagged, not deleted.",
        })


class ModelDetailView(ApiView):
    required_capability = "platform.view"

    def get(self, request, model_ref: str):
        ref, row = _resolve_registry_ref(model_ref)
        disk: Optional[dict[str, Any]] = None
        try:
            disk = bridge.find_disk_entry(ref)
        except NotFound:
            pass
        row_ref = str(row.get("model_ref") or ref)
        return self.ok(request, {
            "model": row,
            "artifact_on_disk": bool(disk),
            "disk_metrics": (disk or {}).get("metrics"),
            "registry_vs_disk": "identical" if disk else "the registry row exists but no artifact is "
                                                         "readable on disk; forecasts from it will be refused",
            "forecast": f"POST /api/v1/predictions/forecast {{\"model_ref\": \"{row_ref}\"}}",
            "evaluate": f"POST /api/v1/models/{row_ref}/evaluate",
        })

    def delete(self, request, model_ref: str):
        """Retire in the registry only: artifacts and weights stay on disk for reproducibility."""
        if not _may_register(request):
            raise Forbidden("Only a role with 'model.register' can retire a model.")
        ref, _row = _resolve_registry_ref(model_ref)
        reason = self.params(request).get("reason") or _optional_body(request).get("reason")
        result = repository.retire(ref, _actor(request), reason)
        audit(request, "model.retire", resource_type=MODEL_COLLECTION, resource_id=ref,
              metadata={"reason": result.get("reason"), "changed": result.get("changed")})
        return self.ok(request, result)


class ModelEvaluateView(ApiView):
    """Stored chronological + walk-forward numbers for one registered model."""

    required_capability = "model.train"
    throttle_scope = "processing"

    def post(self, request, model_ref: str):
        ref, _row = _resolve_registry_ref(model_ref)
        evaluation = bridge.evaluate(ref)
        recorded = repository.record_evaluation(evaluation["resolved_ref"], evaluation,
                                                actor_id=_actor(request))
        audit(request, "model.evaluate", resource_type=MODEL_COLLECTION,
              resource_id=str(evaluation.get("resolved_ref")),
              metadata={"algorithm": evaluation.get("algorithm"), "mode": evaluation.get("mode")})
        return self.ok(request, {"evaluation": evaluation, "recorded": recorded,
                                 "registry_row": repository.registry_row(evaluation["resolved_ref"])})


class ModelPromoteView(ApiView):
    """Make one model the active forecaster for its slice; the previous active one retires."""

    required_capability = "model.register"
    throttle_scope = "processing"

    def post(self, request, model_ref: str):
        ref, _row = _resolve_registry_ref(model_ref)
        result = repository.promote(ref, _actor(request))
        audit(request, "model.promote", resource_type=MODEL_COLLECTION, resource_id=ref,
              metadata={"retired": result["previous_active_retired"],
                        "retired_count": result["retired_count"]})
        return self.ok(request, result)


# --------------------------------------------------------------------------- predictions
class ForecastSubmitView(ApiView):
    """Run a registered model: short horizons answer inline, long ones become a job."""

    required_capability = "prediction.run"
    throttle_scope = "processing"

    def post(self, request):
        payload = parse(ForecastRequest, self.body(request))
        resolved, row = _resolve_registry_ref(payload.model_ref)
        requested = {"model_ref": payload.model_ref, "target_variable": payload.target_variable,
                     "region": payload.region, "station_id": payload.station_id,
                     "horizon_steps": payload.horizon_steps, "origin_date": payload.origin_date}

        if payload.horizon_steps > bridge.INLINE_FORECAST_MAX_STEPS:
            run_token = jobs.new_run_token()
            job = _job_snapshot(submit(
                "prediction.forecast", jobs.run_forecast,
                kwargs={"model_ref": resolved, "target_variable": payload.target_variable,
                        "region": payload.region, "station_id": payload.station_id,
                        "horizon_steps": payload.horizon_steps, "origin_date": payload.origin_date,
                        "actor_id": _actor(request), "run_token": run_token},
                created_by=_actor(request),
                payload={"run_token": run_token, "model_ref": resolved,
                         "target_variable": payload.target_variable, "region": payload.region,
                         "horizon_steps": payload.horizon_steps},
                label=f"Forecast {payload.target_variable}-{payload.region or 'stored-scope'}"
                      f"-h{payload.horizon_steps}"))
            audit(request, "prediction.forecast_submit", resource_type=PREDICTION_COLLECTION,
                  resource_id=str(job["id"]), metadata={"model_ref": resolved,
                                                         "horizon_steps": payload.horizon_steps,
                                                         "mode": "background"})
            return self.ok(request, {
                "job": job, "poll": f"/api/v1/models/jobs/{job['id']}", "mode": "background",
                "threshold": bridge.INLINE_FORECAST_MAX_STEPS,
                "note": f"a {payload.horizon_steps}-step horizon is served as a job because it takes "
                        f"longer than the {bridge.INLINE_FORECAST_MAX_STEPS}-step inline budget; the "
                        f"model still refuses any horizon above its validated range.",
            }, http_status=202)

        result = bridge.forecast(model_ref=resolved, target_variable=payload.target_variable,
                                 region=payload.region, station_id=payload.station_id,
                                 horizon_steps=payload.horizon_steps, origin_date=payload.origin_date)
        document = bridge.prediction_document(result, requested=requested, actor_id=_actor(request))
        if document is None:
            status = str(result.get("status") or "refused")
            reason = str(result.get("reason") or "the ml package refused to produce a forecast")
            audit(request, "prediction.forecast_refused", resource_type=PREDICTION_COLLECTION,
                  resource_id=resolved, outcome="failed",
                  metadata={"status": status, "reason": reason[:200]})
            raise ValidationError(reason, code=f"forecast_{status}",
                                  details=[f"status={status}",
                                           f"model={resolved}",
                                           f"validated_horizon={result.get('validated_horizon')}",
                                           "nothing was stored in `predictions`"])
        stored = repository.insert_prediction(document)
        audit(request, "prediction.forecast", resource_type=PREDICTION_COLLECTION,
              resource_id=str(stored.get("id")),
              metadata={"model_ref": document.get("model_ref"),
                        "horizon_steps": document.get("horizon_steps"),
                        "has_uncertainty": document.get("has_uncertainty")})
        return self.created(request, {
            "prediction": stored, "id": stored.get("id"),
            "poll": f"/api/v1/predictions/{stored.get('id')}",
            "mode": "inline", "registry_status": row.get("status"),
            "note": "Values are projections with the stated uncertainty, never guaranteed outcomes; "
                    "weather-range steps say nothing about long-term climate trends.",
        })


class PredictionListView(ApiView):
    required_capability = "prediction.view"

    def get(self, request):
        page, size, _skip = normalize_pagination(request.query_params)
        params = self.params(request)
        items, total = repository.list_predictions(params, page=page, page_size=size)
        return paginated(items, total=total, page=page, page_size=size,
                         extra={"counts": repository.predictions_overview(),
                                "note": "series arrays are omitted from the list; open one prediction "
                                        "for the full per-step values and bands",
                                "filters": ["region", "variable", "model", "granularity", "status",
                                            "horizon", "min_horizon", "max_horizon", "uncertainty=true",
                                            "created_by", "from", "to"]},
                         request=request)


class PredictionLatestView(ApiView):
    required_capability = "prediction.view"

    def get(self, request):
        params = self.params(request)
        document, reason = repository.latest_prediction(params)
        if document is None:
            raise NotFound("No stored forecast matches this request.", details=[reason])
        return self.ok(request, document,
                       meta={"filters": params,
                             "stale_warning": "this is the newest stored prediction, not a live run; "
                                              "POST /api/v1/predictions/forecast for a fresh one"})


class PredictionDetailView(ApiView):
    required_capability = "prediction.view"

    def get(self, request, prediction_id: str):
        return self.ok(request, repository.get_prediction(prediction_id))


# --------------------------------------------------------------------------- anomalies
class AnomalyScanView(ApiView):
    """Detect deviations from a location's own recorded climatology."""

    required_capability = "model.train"
    throttle_scope = "processing"

    def post(self, request):
        payload = parse(AnomalyScanRequest, self.body(request))
        params = self.params(request)
        persist = _bool_param(params, "persist", True)
        granularity = params.get("granularity") or "daily"

        if not payload.region:
            run_token = jobs.new_run_token()
            job = _job_snapshot(submit(
                "anomaly.scan", jobs.run_anomaly_scan,
                kwargs={"variable": payload.variable, "method": payload.method, "region": None,
                        "window_days": payload.window_days, "sigma": payload.sigma,
                        "granularity": granularity, "persist_events": persist,
                        "actor_id": _actor(request), "run_token": run_token},
                created_by=_actor(request),
                payload={"run_token": run_token, "variable": payload.variable,
                         "method": payload.method, "window_days": payload.window_days,
                         "sigma": payload.sigma, "fan_out": True},
                label=f"Anomaly scan {payload.variable} ({payload.method})"))
            audit(request, "anomaly.scan_submit", resource_type=EVENT_COLLECTION,
                  resource_id=str(job["id"]),
                  metadata={"method": payload.method, "variable": payload.variable, "fan_out": True})
            return self.ok(request, {
                "job": job, "poll": f"/api/v1/models/jobs/{job['id']}", "mode": "background",
                "note": "no region given: the scan fans out over every region whose recorded history "
                        "for this variable clears the package's row gate",
            }, http_status=202)

        result = bridge.scan_anomalies(region=payload.region, variable=payload.variable,
                                       method=payload.method, window_days=payload.window_days,
                                       sigma=payload.sigma, granularity=granularity)
        rows = bridge.detection_rows(result)
        created = deduped = 0
        if persist and rows:
            documents = bridge.anomaly_event_documents(result, actor_id=_actor(request))
            written = repository.insert_alert_events(documents)
            created = len(written.get("created") or [])
            deduped = int(written.get("deduped") or 0)
        summary_id = None
        if persist:
            features = bridge.anomaly_summary_document(result, job_id=None)
            if features:
                summary_id = str(repository.publish_anomaly_summary(features))
        summary = bridge.anomaly_scan_summary(
            result, events_created=created, events_deduped=deduped,
            events_skipped=max(0, len(rows) - created - deduped))
        audit(request, "anomaly.scan", resource_type=EVENT_COLLECTION, resource_id=None,
              outcome="success" if summary.get("ran") else "failed",
              metadata={"region": payload.region, "variable": payload.variable,
                        "method": payload.method, "status": summary.get("status"),
                        "flagged": summary.get("flagged"), "events_created": created,
                        "summary_id": summary_id})
        stamps = sorted(str(row.get("timestamp")) for row in rows if row.get("timestamp"))
        return self.ok(request, {
            "scan": summary, "persisted": persist, "summary_id": summary_id,
            "events": f"/api/v1/anomaly/events?origin=ml&region={payload.region}",
            "detected_at_range": [stamps[0], stamps[-1]] if stamps else None,
        })


class AnomalyIndexView(ApiView):
    """What exists under /api/v1/anomaly/ plus the honest state of the stored detections."""

    required_capability = "anomaly.view"

    def get(self, request):
        params = self.params(request)
        methods = bridge.anomaly_methods()
        return self.ok(request, {
            "endpoints": {
                "POST /api/v1/anomaly/scan": "run a detector over one region (or fan out as a job)",
                "GET /api/v1/anomaly/events": "stored anomaly alert events",
                "GET /api/v1/anomaly/methods": "what each detector assumes and how it grades severity",
                "GET /api/v1/anomaly/baselines": "the recorded statistics each baseline came from",
            },
            "methods": [row["method"] for row in methods["methods"]],
            "stored_events": repository.anomaly_event_counts(dict(params, severity="")),
            "severity_mapping": methods["severity_mapping"],
            "note": methods["note"],
        })


class AnomalyEventListView(ApiView):
    required_capability = "anomaly.view"

    def get(self, request):
        page, size, _skip = normalize_pagination(request.query_params)
        params = self.params(request)
        items, total = repository.list_anomaly_events(params, page=page, page_size=size)
        return paginated(items, total=total, page=page, page_size=size,
                         extra={"counts": repository.anomaly_event_counts(params),
                                "note": "anomaly rows written by the ml detectors share the alert "
                                        "pipeline; acknowledge them through /api/v1/alerts/events",
                                "filters": ["origin=ml|alerts|all", "severity", "region", "variable",
                                            "status", "method", "from", "to"]},
                         request=request)


class AnomalyMethodsView(ApiView):
    required_capability = "anomaly.view"

    def get(self, request):
        return self.ok(request, bridge.anomaly_methods())


class AnomalyBaselineView(ApiView):
    """The recorded statistics the anomaly detector measured its baselines from."""

    required_capability = "anomaly.view"

    def get(self, request):
        params = self.params(request)
        limit = _int_param(params, "limit") or 10
        rows = repository.anomaly_summaries(region=params.get("region"),
                                            variable=params.get("variable"),
                                            limit=max(1, min(limit, 50)))
        if not rows:
            return self.ok(request, {"summaries": [], "note": "no anomaly scan has published features "
                                                              "yet; POST /api/v1/anomaly/scan with a "
                                                              "region"})
        return self.ok(request, {"summaries": rows,
                                 "note": "mean/std/median come from the recorded observations at that "
                                         "location over the scan window, not from a model"})
