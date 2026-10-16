"""Background ML tasks: model training, long-horizon forecasts, anomaly fan-outs.

``common.tasks.submit`` runs these functions off the request thread. The runner never hands the
task its own job id, so each function locates its ``processing_jobs`` row through the
``run_token`` the view stored in that row's payload and writes every real step - the ladder it
tried, the chronological split dates, the metrics, whether the leakage gate fired - into the
job log where an operator can read it back.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from apps.machine_learning import bridge, repository
from common.db import find_one
from common.logging_config import get_logger
from common.security import sanitize_text
from common.serialization import jsonable
from common.tasks import append_log

logger = get_logger("earthscape.machine_learning.jobs")

LEAKAGE_MARKER = "leakage check failed"
LEAKAGE_DETAIL = "refusing to train: "
MAX_FANOUT_LOGGED = 12
DEFAULT_ANOMALY_GRANULARITY = "daily"


def new_run_token() -> str:
    """Opaque token tying a submitted task to the processing_jobs row created with it."""
    return uuid.uuid4().hex


def _job_id(run_token: Optional[str], kind: str) -> Any:
    token = sanitize_text(run_token or "", max_length=64)
    job = find_one(repository.JOBS, {"payload.run_token": token}) if token else None
    if job is None:
        raise RuntimeError(f"{kind} job row is missing for run_token {token[:8] or '(none)'}")
    return job["_id"]


def _log(job_id: Any, level: str, message: str) -> None:
    """Append to the job's own log; a broken log write must never hide the real outcome."""
    text = str(message)[:1800]
    logger.info("job %s [%s] %s", str(job_id), level, text)
    try:
        append_log(job_id, level, text)
    except Exception as exc:
        logger.warning("job log write failed (%s): %s", type(exc).__name__, text[:200])


def _leakage_detail(text: str) -> str:
    return text.split(LEAKAGE_DETAIL, 1)[1] if LEAKAGE_DETAIL in text else text


# --------------------------------------------------------------------------- model training
def run_training(*, target_variable: str, region: str, granularity: str = "daily",
                 horizon_steps: int = 30, folds: int = 5, models: Optional[list[str]] = None,
                 date_range: Optional[dict] = None, station_id: Optional[str] = None,
                 register_as_candidate: bool = True, auto_promote: bool = False,
                 allow_register: bool = False, actor_id: Optional[str] = None,
                 run_token: Optional[str] = None) -> dict[str, Any]:
    """Fit the model ladder on one slice, log every step, register only what really trained."""
    job_id = _job_id(run_token, "model.train")
    ladder, _rejected = bridge.resolve_ladder(models)
    ladder_text = ", ".join(ladder) if ladder else \
        "package default (naive -> seasonal_naive -> linear_ridge -> random_forest -> xgboost if importable)"
    window = "all loaded history"
    if date_range:
        window = f"{date_range.get('start')}..{date_range.get('end')}"
    requested = {"target_variable": target_variable, "region": region, "granularity": granularity,
                 "horizon_steps": int(horizon_steps), "folds": int(folds),
                 "ladder_requested": ladder, "window": window, "station_id": station_id,
                 "register_as_candidate": bool(register_as_candidate),
                 "auto_promote": bool(auto_promote)}
    _log(job_id, "info",
         f"train request: target={target_variable} region={region} granularity={granularity} "
         f"horizon={horizon_steps} folds={folds} window={window} station={station_id or '-'} "
         f"ladder=[{ladder_text}] save={bool(register_as_candidate)}")

    try:
        report = bridge.train(target_variable=target_variable, region=region,
                              granularity=granularity, horizon_steps=horizon_steps, folds=folds,
                              models=models, date_range=date_range, station_id=station_id,
                              save=bool(register_as_candidate))
    except RuntimeError as exc:
        if LEAKAGE_MARKER not in str(exc):
            raise
        text = str(exc)[:500]
        detail = _leakage_detail(text)
        _log(job_id, "error", f"leakage gate FIRED before any model was fitted: {text}")
        _log(job_id, "error", "no artifact was written, nothing was registered and no metric exists "
                              "for this run: ml/training/forecast.py refuses the whole slice instead "
                              "of quietly dropping the offending feature")
        return jsonable({
            "status": "refused_leakage_gate", "trained": False, "registered": False,
            "reason": text, "leakage_detail": detail, "requested": requested, "ladder_tried": [],
            "leakage_gate": {"ran": True, "fired": True,
                             "policy": "a feature that can see values at or after its own target "
                                       "window aborts the training run (RuntimeError); it is never "
                                       "silently dropped"},
            "metrics": {"models_trained": 0, "models_registered": 0, "leakage_gate_fired": 1},
        })

    status = str(report.get("status") or "unknown")
    if status != "trained":
        availability = report.get("availability") or {}
        reason = str(report.get("message") or availability.get("reason")
                     or "the ml package declined to train this slice")
        _log(job_id, "warning", f"training declined by the package: status={status} - {reason}")
        _log(job_id, "warning",
             f"row gate: rows={availability.get('rows')} min_required={availability.get('min_required')}"
             f" - no model was fitted, so no metric is reported and nothing was registered")
        return jsonable({
            "status": status, "trained": False, "registered": False, "reason": reason,
            "availability": availability, "requested": requested, "ladder_tried": [],
            "cache": report.get("cache"),
            "metrics": {"models_trained": 0, "models_registered": 0,
                        "rows_available": int(availability.get("rows") or 0),
                        "min_rows_required": int(availability.get("min_required") or 0)},
        })

    comparison = list(report.get("comparison") or [])
    split = report.get("split") or {}
    entry = report.get("registry") or {}
    metrics_row = entry.get("evaluation_metrics") or {}
    backtest = report.get("backtest") or {}
    uncertainty = report.get("uncertainty") or {}
    xgboost = report.get("xgboost") or {}

    _log(job_id, "info", "leakage gate: ran before the split and passed (the package raises instead "
                         "of training when any feature can see its own target window)")
    _log(job_id, "info",
         f"chronological split: {split.get('train_rows')} train rows up to {split.get('train_end')} "
         f"-> {split.get('test_rows')} held-out rows from {split.get('test_start')} "
         f"[{split.get('method')}]")
    _log(job_id, "info",
         f"rows used={entry.get('rows_used')} | training period "
         f"{(entry.get('training_period') or {}).get('start')} -> "
         f"{(entry.get('training_period') or {}).get('end')} | test period "
         f"{(entry.get('test_period') or {}).get('start')} -> {(entry.get('test_period') or {}).get('end')}")
    for row in comparison:
        if row.get("mae") is None:
            _log(job_id, "info", f"ladder {row.get('algorithm')}: "
                                 f"{row.get('note') or 'no metrics produced'}")
            continue
        _log(job_id, "info",
             f"ladder {row.get('algorithm')} [{row.get('role')}]: mae={row.get('mae')} "
             f"rmse={row.get('rmse')} r2={row.get('r2')} mape={row.get('mape')} "
             f"skill_vs_naive={row.get('skill_vs_naive')} "
             f"beats_naive_by_5pct={row.get('beats_naive_by_5pct')}"
             + (f" | {row.get('note')}" if row.get("note") else ""))
    if not xgboost.get("available"):
        _log(job_id, "info", f"xgboost tier skipped by the package: {xgboost.get('reason')}")
    if backtest.get("available"):
        head = (backtest.get("aggregate") or {}).get(str(report.get("chosen_algorithm"))) or {}
        _log(job_id, "info",
             f"walk-forward backtest: folds={backtest.get('folds')} "
             f"warmup_rows={backtest.get('warmup_train_rows')} chosen="
             f"{report.get('chosen_algorithm')} mean_fold_mae={head.get('mean_fold_mae')} "
             f"std_fold_mae={head.get('std_fold_mae')} mean_fold_rmse={head.get('mean_fold_rmse')} "
             f"skill_vs_baseline={head.get('skill_vs_baseline')}")
    else:
        _log(job_id, "warning", f"walk-forward backtest unavailable: {backtest.get('reason')}")
    if uncertainty.get("supported"):
        _log(job_id, "info",
             f"uncertainty supported: method={uncertainty.get('method')} "
             f"sigma_train_residual={uncertainty.get('sigma_train_residual')} "
             f"nominal_coverage={uncertainty.get('coverage_nominal')}")
    else:
        _log(job_id, "info", f"uncertainty NOT supported: {uncertainty.get('reason')}")
    for note in report.get("limitations") or []:
        _log(job_id, "warning", f"limitation: {note}")

    document = report.get("registry_document")
    stored_row: Optional[dict[str, Any]] = None
    model_ref: Optional[str] = None
    if document and register_as_candidate:
        model_ref = str(document["model_ref"])
        stored_row = repository.upsert_registry_document(document)
        repository.register_job_refs(job_id, [model_ref])
        _log(job_id, "info",
             f"registered in model_registry: {model_ref} (mongo status={stored_row.get('status')}, "
             f"package status={document.get('registry_status')}, artifact="
             f"{document.get('artifact_uri')})")
    elif not register_as_candidate:
        _log(job_id, "info", "register_as_candidate=false: the ladder was fitted in memory only - no "
                             "artifact was written and no registry row created. Metrics below describe "
                             "that unsaved run.")

    promotion: Optional[dict[str, Any]] = None
    if auto_promote:
        if not model_ref:
            promotion = {"promoted": False, "reason": "nothing was registered, so there is nothing to "
                                                      "promote (register_as_candidate was false)"}
            _log(job_id, "warning", "auto_promote ignored: no registry row exists for this run")
        elif not allow_register:
            promotion = {"promoted": False,
                         "reason": "auto_promote needs the 'model.register' capability "
                                   "(administrator role); the model stays a candidate for manual review"}
            _log(job_id, "warning", str(promotion["reason"]))
        elif str(document.get("registry_status")) != "registered":
            promotion = {"promoted": False,
                         "reason": f"the package graded this run '{document.get('registry_status')}' "
                                   f"(it did not clear the 5% skill bar over the naive baseline), so "
                                   f"auto-promotion was refused"}
            _log(job_id, "warning", str(promotion["reason"]))
        else:
            outcome = repository.promote(model_ref, actor_id or "system")
            promotion = {"promoted": True, "previous_active_retired": outcome["previous_active_retired"],
                         "retired_count": outcome["retired_count"], "policy": outcome["policy"]}
            _log(job_id, "info", f"promoted {model_ref} to active; "
                                 f"{outcome['retired_count']} previous active model(s) retired")

    headline = {
        "models_trained": len([row for row in comparison if row.get("mae") is not None]),
        "models_registered": 1 if stored_row else 0,
        "leakage_gate_fired": 0,
        "chosen_algorithm": report.get("chosen_algorithm"),
        "registry_status": report.get("registry_status"),
        "mae": metrics_row.get("mae"), "rmse": metrics_row.get("rmse"), "r2": metrics_row.get("r2"),
        "mape": metrics_row.get("mape"), "baseline_mae": metrics_row.get("baseline_mae"),
        "skill_vs_baseline": metrics_row.get("skill_vs_baseline"),
        "test_n": metrics_row.get("test_n"), "train_rows": split.get("train_rows"),
        "test_rows": split.get("test_rows"), "duration_seconds": report.get("duration_seconds"),
    }
    return jsonable({
        "status": "trained", "trained": True, "registered": bool(stored_row),
        "model_ref": model_ref, "model_status": (stored_row or {}).get("status"),
        "chosen_algorithm": report.get("chosen_algorithm"),
        "registry_status": report.get("registry_status"), "artifact_uri": report.get("artifact_uri"),
        "requested": requested, "ladder_requested": report.get("ladder_requested"),
        "ladder_tried": report.get("ladder_tried"), "window": report.get("window"), "split": split,
        "comparison": comparison, "backtest": backtest, "uncertainty": uncertainty,
        "availability": report.get("availability"), "xgboost": xgboost,
        "limitations": report.get("limitations"), "metrics": headline, "registry_row": stored_row,
        "promotion": promotion, "duration_seconds": report.get("duration_seconds"),
        "cache": report.get("cache"),
        "model_detail": f"/api/v1/models/{model_ref}" if model_ref else None,
        "honesty": "metrics are from this run's chronological holdout only; the out-of-sample numbers "
                   "are backtest.aggregate (walk-forward). A model the package graded 'candidate' did "
                   "not clear the 5% skill bar and is not presented as an improvement.",
    })


# --------------------------------------------------------------------------- forecasts
def run_forecast(*, model_ref: str, target_variable: str, region: Optional[str] = None,
                 station_id: Optional[str] = None, horizon_steps: int = 30,
                 origin_date: Optional[str] = None, actor_id: Optional[str] = None,
                 run_token: Optional[str] = None) -> dict[str, Any]:
    """Long-horizon forecast path: run the model, store it, and log the refusal if there is one."""
    job_id = _job_id(run_token, "prediction.forecast")
    requested = {"model_ref": model_ref, "target_variable": target_variable, "region": region,
                 "station_id": station_id, "horizon_steps": int(horizon_steps),
                 "origin_date": origin_date}
    _log(job_id, "info",
         f"forecast request: ref={model_ref} target={target_variable} "
         f"region={region or '(stored region)'} station={station_id or '-'} "
         f"steps={horizon_steps} origin={origin_date or '(latest observation)'}")

    result = bridge.forecast(model_ref=model_ref, target_variable=target_variable, region=region,
                             station_id=station_id, horizon_steps=horizon_steps,
                             origin_date=origin_date)
    status = str(result.get("status") or "error")
    document = bridge.prediction_document(result, requested=requested, actor_id=actor_id,
                                          job_id=str(job_id))
    if document is None:
        reason = str(result.get("reason") or "the ml package refused to produce a forecast")
        _log(job_id, "warning", f"forecast refused with status={status}: {reason}")
        _log(job_id, "warning", "nothing was written to `predictions`: a refusal is stored on this "
                                "job only, never as a forecast value")
        return jsonable({
            "status": status, "honest_status": result.get("honest_status") or status,
            "reason": reason, "persisted": False, "requested": requested,
            "requested_ref": result.get("requested_ref"), "resolved_ref": result.get("resolved_ref"),
            "validated_horizon": result.get("validated_horizon"),
            "availability": result.get("availability"),
            "metrics": {"horizon_steps_requested": int(horizon_steps), "values_returned": 0,
                        "predictions_stored": 0, "duration_seconds": result.get("duration_seconds")},
            "duration_seconds": result.get("duration_seconds"), "cache": result.get("cache"),
        })

    stored = repository.insert_prediction(document)
    prediction_id = str(stored.get("id"))
    summary = document.get("summary") or {}
    bands = document.get("uncertainty") or {}
    _log(job_id, "info",
         f"forecast produced by {document.get('model_ref')} "
         f"({document.get('algorithm')}): {len(document.get('values') or [])} steps in "
         f"{document.get('granularity')} units of {document.get('units')}; "
         f"validated_horizon={document.get('validated_horizon')}")
    _log(job_id, "info",
         f"range: first={summary.get('first_value')} last={summary.get('last_value')} "
         f"min={summary.get('min_value')} max={summary.get('max_value')} "
         f"mean={summary.get('mean_value')} {document.get('units')} "
         f"(projection, not a guaranteed outcome)")
    if document.get("has_uncertainty"):
        _log(job_id, "info", f"uncertainty bands stored: method={bands.get('method')} "
                             f"nominal coverage={bands.get('coverage_nominal')}")
    else:
        _log(job_id, "info", f"no uncertainty bands: {document.get('uncertainty_reason')}")
    _log(job_id, "info", f"stored in predictions as {prediction_id}")
    for note in (document.get("warnings") or [])[:8]:
        _log(job_id, "warning", f"model warning: {note}")

    return jsonable({
        "status": "forecast", "persisted": True, "prediction_id": prediction_id,
        "model_ref": document.get("model_ref"), "resolved_ref": result.get("resolved_ref"),
        "algorithm": document.get("algorithm"), "target_variable": document.get("target_variable"),
        "region": document.get("region"), "granularity": document.get("granularity"),
        "horizon_steps": document.get("horizon_steps"),
        "validated_horizon": document.get("validated_horizon"), "units": document.get("units"),
        "summary": summary, "series": document.get("series"),
        "has_uncertainty": bool(document.get("has_uncertainty")),
        "uncertainty_method": bands.get("method") if document.get("has_uncertainty") else None,
        "uncertainty_reason": document.get("uncertainty_reason"),
        "data_freshness": document.get("data_freshness"), "metrics": document.get("metrics"),
        "backtest_aggregate": document.get("backtest_aggregate"),
        "warnings": document.get("warnings"),
        "requested": requested,
        "poll": f"/api/v1/predictions/{prediction_id}",
        "duration_seconds": result.get("duration_seconds"), "cache": result.get("cache"),
        "note": "These are projections from a fitted statistical model, not guaranteed outcomes.",
    })


# --------------------------------------------------------------------------- anomaly scans
def run_anomaly_scan(*, variable: str = "temperature", method: str = "statistical",
                     region: Optional[str] = None, window_days: int = 90, sigma: float = 3.0,
                     granularity: str = DEFAULT_ANOMALY_GRANULARITY,
                     persist_events: bool = True, publish_summaries: bool = True,
                     actor_id: Optional[str] = None,
                     run_token: Optional[str] = None) -> dict[str, Any]:
    """Scan one region, or fan out over every region that has enough recorded history."""
    job_id = _job_id(run_token, "anomaly.scan")
    if region:
        regions = [sanitize_text(region, max_length=80)]
        fan_out = False
    else:
        regions = bridge.trainable_regions(variable, granularity)
        fan_out = True
        _log(job_id, "info",
             f"no region given: fanning out over the {len(regions)} region(s) whose {variable} "
             f"{granularity} slice clears the package's training row gate: {', '.join(regions[:20])}")
    if not regions:
        _log(job_id, "warning", f"no region has enough recorded {variable} history to scan; nothing run")
        return jsonable({"status": "no_regions_to_scan", "variable": variable, "method": method,
                         "metrics": {"regions_scanned": 0, "events_created": 0, "events_deduped": 0},
                         "note": "the detector refuses rather than inventing a baseline; see "
                                 "GET /api/v1/models/available-data for the per-slice verdicts"})

    _log(job_id, "info",
         f"anomaly scan: variable={variable} method={method} granularity={granularity} "
         f"window_days={window_days} sigma={sigma} regions={len(regions)}")

    per_region: list[dict[str, Any]] = []
    severity_totals: dict[str, int] = {}
    flagged_total = 0
    created_total: list[str] = []
    deduped_total = 0
    summaries_written = 0
    failures = 0

    for name in regions:
        try:
            result = bridge.scan_anomalies(region=name, variable=variable, method=method,
                                           window_days=window_days, sigma=sigma,
                                           granularity=granularity)
        except Exception as exc:
            failures += 1
            reason = f"{type(exc).__name__}: {str(exc)[:200]}"
            _log(job_id, "error", f"{name}: scan failed - {reason}")
            per_region.append({"region": name, "status": "error", "reason": reason})
            continue

        status = str(result.get("status") or "unknown")
        rows = bridge.detection_rows(result)
        counts: dict[str, int] = {}
        for row in rows:
            key = str(row.get("severity") or "unknown")
            counts[key] = counts.get(key, 0) + 1
            severity_totals[key] = severity_totals.get(key, 0) + 1
        flagged = int(result.get("flagged") if result.get("flagged") is not None else len(rows))
        flagged_total += flagged
        summary = bridge.anomaly_scan_summary(result, events_created=0, events_deduped=0,
                                              events_skipped=0, job_id=str(job_id))
        entry: dict[str, Any] = {
            "region": name, "status": status, "checked": result.get("checked"),
            "flagged": flagged, "count_by_severity": counts,
            "notes": summary.get("notes"), "reason": result.get("reason"),
            "top_events": summary.get("top_events"),
        }

        if persist_events and rows:
            written = repository.insert_alert_events(
                bridge.anomaly_event_documents(result, job_id=str(job_id), actor_id=actor_id))
            created = [str(item) for item in written.get("created") or []]
            created_total.extend(created)
            deduped_total += int(written.get("deduped") or 0)
            entry["alert_events_created"] = len(created)
            entry["alert_events_deduped"] = int(written.get("deduped") or 0)
            _log(job_id, "info",
                 f"{name}: status={status} checked={entry.get('checked')} flagged={flagged} "
                 f"severities={counts or 'none'} -> alert_events +{len(created)} "
                 f"(deduped {written.get('deduped')})")
        else:
            _log(job_id, "info",
                 f"{name}: status={status} checked={entry.get('checked')} flagged={flagged} "
                 f"severities={counts or 'none'}"
                 + ("" if persist_events else " (persist_events=false, nothing written)"))
        for note in (summary.get("notes") or [])[:3]:
            _log(job_id, "warning", f"{name}: {note}")
        if status in ("insufficient_baseline", "no_data", "insufficient_data", "unavailable", "error"):
            _log(job_id, "warning", f"{name}: {result.get('reason') or result.get('note') or status} "
                                    f"- reported as a refusal, not as an anomaly")

        if publish_summaries and status in ("ok", "no_data", "insufficient_baseline"):
            features = bridge.anomaly_summary_document(result, job_id=str(job_id))
            if features:
                entry["summary_id"] = str(repository.publish_anomaly_summary(features))
                entry["features"] = features.get("features")
                summaries_written += 1
        per_region.append(entry)

    logged = per_region[:MAX_FANOUT_LOGGED] if fan_out else per_region
    return jsonable({
        "status": "completed" if failures < len(regions) else "all_regions_failed",
        "variable": variable, "method": method, "granularity": granularity,
        "fan_out": fan_out, "regions_requested": len(regions),
        "regions_scanned": len(regions) - failures, "regions_failed": failures,
        "flagged_total": flagged_total, "count_by_severity": severity_totals,
        "events_created": len(created_total), "events_deduped": deduped_total,
        "summaries_written": summaries_written,
        "regions": [row["region"] for row in per_region[:40]],
        "per_region": logged,
        "per_region_truncated": max(0, len(per_region) - len(logged)),
        "tuning": {"window_days": int(window_days), "sigma": float(sigma),
                   "note": "sigma is only a reporting floor for the season-aware detector; severity "
                           "tiers stay the package's fixed 2/3/4 robust-z mapping"},
        "events_poll": "/api/v1/anomaly/events?origin=ml",
        "metrics": {"regions_scanned": len(regions) - failures, "regions_failed": failures,
                    "detections": flagged_total, "events_created": len(created_total),
                    "events_deduped": deduped_total, "summaries_written": summaries_written},
        "note": "Every detection is a deviation from recorded data at that location; none of them "
                "attributes a cause.",
    })
