"""Airflow DAG for the EarthScape daily synchronisation cycle.

This DAG is the *production-oriented* scheduling path. The platform does not require it:
``AIRFLOW_ENABLED=false`` (the default) leaves ingestion in the hands of the API's own
background executor, and the admin UI can trigger the same sync manually.

The DAG talks to the platform over its REST API only — it never opens a database
connection, so a scheduling failure can never corrupt application data. Every call below
matches the measured contract of the running API:

* ``POST /api/v1/ingestion/jobs``  body ``{"source": <connector name>, "parameters": {…}}``
  → ``202 {"job_id", "status", "executor", "dataset_id", "data_period"}``
* ``GET  /api/v1/ingestion/jobs/<id>`` → the job document (``id``, ``status``,
  ``records_accepted``, ``records_rejected``, ``duplicate_count``, ``error_message``)
* ``POST /api/v1/processing/``  body ``{"workflow": <registry id>, "parameters": {…}}``
  → ``202`` with the job document; poll ``GET /api/v1/processing/<id>``
* ``POST /api/v1/alerts/scan`` → ``{"matched", "detail", "evaluation", "scheduler"}``

Success and error envelopes are flat (there is no ``data`` wrapper); failures are
``{"error": {"code", "message", "details?", "request_id"}}``.

Enable by pointing ``AIRFLOW_BASE_URL`` at an Airflow 2.x webserver with this file in
``$AIRFLOW_HOME/dags`` and ``EARTHSCAPE_API_TOKEN`` set to a bearer token for a service
account with the climate_analyst (or administrator) role — both the ingestion and the
processing write endpoints reject every other role. Get the token from the real login
route, not a stored password: ``POST /api/v1/auth/login`` returns ``access_token``.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import requests
from airflow import DAG
from airflow.operators.python import PythonOperator

API_BASE = os.environ.get("EARTHSCAPE_API_BASE", "http://backend:8000/api/v1")
API_TOKEN = os.environ.get("EARTHSCAPE_API_TOKEN", "")
TIMEOUT = int(os.environ.get("EARTHSCAPE_SYNC_TIMEOUT", "900"))
POLL_SECONDS = int(os.environ.get("EARTHSCAPE_SYNC_POLL_SECONDS", "20"))
LOOKBACK_DAYS = int(os.environ.get("EARTHSCAPE_SYNC_LOOKBACK_DAYS", "3"))

INGESTION_TERMINAL = {"completed", "completed_with_errors", "failed", "cancelled"}
PROCESSING_TERMINAL = {"completed", "failed", "cancelled"}

DEFAULT_ARGS = {
    "owner": "earthscape",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "email_on_failure": False,
}


def _headers() -> dict:
    if not API_TOKEN:
        raise RuntimeError("EARTHSCAPE_API_TOKEN is not set; the DAG cannot authenticate.")
    return {"Authorization": f"Bearer {API_TOKEN}", "Content-Type": "application/json"}


def _failure_context(response: requests.Response) -> str:
    """Render the platform's error envelope so Airflow logs carry the real reason."""
    try:
        error = response.json().get("error") or {}
    except ValueError:
        return f"HTTP {response.status_code} (non-JSON body)"
    detail = error.get("message") or response.reason
    request_id = error.get("request_id")
    return f"HTTP {response.status_code}: {detail}" + (f" [request_id={request_id}]" if request_id else "")


def _sync_period(days: int) -> Dict[str, str]:
    """Explicit ``start``/``end`` dates: the API's own default window is 7 days."""
    end = datetime.now(timezone.utc) - timedelta(days=1)
    start = end - timedelta(days=max(days, 1) - 1)
    return {"start": start.strftime("%Y-%m-%d"), "end": end.strftime("%Y-%m-%d")}


def _submit(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    response = requests.post(f"{API_BASE}{path}", json=payload, headers=_headers(), timeout=60)
    if response.status_code >= 400:
        raise RuntimeError(f"POST {path} rejected — {_failure_context(response)}")
    return response.json()


def _await(path: str, job_id: str, terminal: set, failure_field: str = "error_message") -> Dict[str, Any]:
    """Poll a job document until it reaches a documented terminal state."""
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        response = requests.get(f"{API_BASE}{path}{job_id}", headers=_headers(), timeout=60)
        if response.status_code >= 400:
            raise RuntimeError(f"GET {path}{job_id} failed — {_failure_context(response)}")
        document = response.json()
        state = document.get("status")
        if state in terminal:
            print(json.dumps({"job": job_id, "status": state}, default=str))
            if state == "failed":
                raise RuntimeError(f"Job {job_id} failed: {document.get(failure_field) or 'no reason reported'}")
            return document
        time.sleep(POLL_SECONDS)
    raise TimeoutError(f"Job {job_id} did not reach a terminal state within {TIMEOUT}s")


def run_ingestion(source: str, days: int) -> Dict[str, Any]:
    created = _submit("/ingestion/jobs", {"source": source, "parameters": _sync_period(days),
                                          "triggered_by": "schedule"})
    print(json.dumps({"submitted": created.get("job_id"), "window": created.get("data_period")},
                     default=str))
    job = _await("/ingestion/jobs/", created["job_id"], INGESTION_TERMINAL)
    print(json.dumps({
        "job": job.get("id"),
        "source": job.get("source_name"),
        "status": job.get("status"),
        "accepted": job.get("records_accepted"),
        "rejected": job.get("records_rejected"),
        "duplicates": job.get("duplicate_count"),
    }, default=str))
    return job


def run_workflow(workflow_id: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
    job = _submit("/processing/", {"workflow": workflow_id, "parameters": parameters})
    print(json.dumps({"submitted": job.get("id"), "workflow": workflow_id}, default=str))
    finished = _await("/processing/", job["id"], PROCESSING_TERMINAL)
    print(json.dumps({"job": finished.get("id"), "status": finished.get("status"),
                      "metrics": finished.get("metrics")}, default=str))
    return finished


def sync_nasa_power(**_: Any) -> None:
    run_ingestion("nasa_power", LOOKBACK_DAYS)


def sync_live_provider(**_: Any) -> None:
    run_ingestion("open_meteo", 1)


def rebuild_analytics(**_: Any) -> None:
    # Engine-free rollup: it runs on every deployment, with or without Hadoop/Spark.
    run_workflow("mongodb.analytics_summary_rebuild", {"metric": "temperature_c"})


def evaluate_alerts(**_: Any) -> None:
    payload = _submit("/alerts/scan", {})
    print(json.dumps({"alerts_created": payload.get("matched"), "detail": payload.get("detail")},
                     default=str))


with DAG(
    dag_id="earthscape_daily_sync",
    description="Nightly climate ingestion, analytics rebuild and alert evaluation",
    start_date=datetime(2024, 1, 1),
    schedule_interval=os.environ.get("EARTHSCAPE_SYNC_SCHEDULE", "30 2 * * *"),
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["earthscape", "climate", "ingestion"],
) as dag:
    nasa = PythonOperator(task_id="sync_nasa_power", python_callable=sync_nasa_power)
    live = PythonOperator(task_id="sync_live_provider", python_callable=sync_live_provider)
    analytics = PythonOperator(task_id="rebuild_analytics", python_callable=rebuild_analytics)
    alerts = PythonOperator(task_id="evaluate_alerts", python_callable=evaluate_alerts)

    [nasa, live] >> analytics >> alerts
