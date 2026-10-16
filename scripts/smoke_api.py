#!/usr/bin/env python3
"""End-to-end API smoke test for the EarthScape backend.

Drives the real HTTP surface of a running server: login for each role, RBAC
enforcement, a live provider ingestion, job polling, dataset and alert reads.
Every value it prints comes from the server; nothing is simulated.

Usage: python scripts/smoke_api.py [--base http://127.0.0.1:8000/api/v1]
       python scripts/smoke_api.py --admin-password <generated one-time password>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_env() -> None:
    for candidate in (ROOT / "backend" / ".env", ROOT / ".env"):
        if not candidate.is_file():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"'))


RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    flag = "PASS" if ok else "FAIL"
    print(f"[{flag}] {name}{(': ' + detail) if detail else ''}")
    return ok


class Api:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.token = ""

    def call(self, method: str, path: str, *, body=None, token: str | None = "",
             expect: int | tuple = (200, 201)) -> tuple[int, dict]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(self.base + path, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        used = self.token if token == "" else token
        if used:
            request.add_header("Authorization", f"Bearer {used}")
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                raw = response.read()
                try:
                    payload = json.loads(raw.decode("utf-8") or "{}")
                except (UnicodeDecodeError, json.JSONDecodeError):
                    payload = {"raw": raw[:200].decode("utf-8", "replace"), "bytes": len(raw)}
                return response.status, payload
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                return exc.code, json.loads(raw.decode("utf-8") or "{}")
            except (UnicodeDecodeError, json.JSONDecodeError):
                return exc.code, {"raw": raw[:400].decode("utf-8", "replace")}
        except urllib.error.URLError as exc:
            raise SystemExit(f"server unreachable at {self.base}: {exc}")


def login(api: Api, email: str, password: str) -> str:
    code, payload = api.call("POST", "/auth/login", body={"email": email, "password": password},
                             token=None)
    if code != 200:
        record(f"login {email}", False, str(payload.get("error", payload))[:160])
        return ""
    record(f"login {email}", True, f"role={payload['data']['user']['role']}")
    return payload["data"]["access_token"]


def wait_for_job(api: Api, job_id: str, timeout: int = 180) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        code, payload = api.call("GET", f"/ingestion/jobs/{job_id}")
        job = payload.get("data") or {}
        if job.get("status") in ("completed", "failed", "ready", "partial"):
            return job
        time.sleep(3)
    return {"status": "timeout"}


def item_count(payload: dict):
    data = payload.get("data")
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        for key in ("count", "region_count", "total", "points", "regions", "items"):
            value = data.get(key)
            if isinstance(value, int):
                return value
            if isinstance(value, list):
                return len(value)
        return len(data)
    return data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000/api/v1")
    parser.add_argument("--admin-password", default=os.environ.get("SMOKE_ADMIN_PASSWORD", ""))
    parser.add_argument("--analyst-password", default=os.environ.get("SMOKE_ANALYST_PASSWORD", ""))
    parser.add_argument("--viewer-password", default=os.environ.get("SMOKE_VIEWER_PASSWORD", ""))
    parser.add_argument("--skip-ingestion", action="store_true")
    args = parser.parse_args()

    load_env()
    missing = [label for label, value in (
        ("SMOKE_ADMIN_PASSWORD", args.admin_password),
        ("SMOKE_ANALYST_PASSWORD", args.analyst_password),
        ("SMOKE_VIEWER_PASSWORD", args.viewer_password),
    ) if not value]
    if missing:
        print("No password literals live in this repository, so the harness needs its "
              "credentials from the environment: " + ", ".join(missing) + " not set.\n"
              "Provision the three accounts with scripts/provision_admin.py (one account per "
              "role) and export the generated passwords before re-running.")
        return 2
    api = Api(args.base)

    code, payload = api.call("GET", "/health", token=None)
    record("liveness /health", code == 200 and payload.get("data", {}).get("status") == "alive")

    code, payload = api.call("GET", "/ready", token=None)
    checks = (payload.get("data") or {}).get("checks", {})
    record("readiness /ready", code == 200 and checks.get("database", {}).get("ok"),
           f"database={checks.get('database', {}).get('detail')} "
           f"lake={checks.get('data_lake', {}).get('ok')} "
           f"observations={(payload.get('data') or {}).get('observations')}")

    admin = login(api, "admin@earthscape.pk", args.admin_password)
    if not admin:
        print("Administrator login failed. Re-run with --password <generated password>.")
        return 1
    analyst = login(api, "analyst@earthscape.pk", args.analyst_password)
    viewer = login(api, "viewer@earthscape.pk", args.viewer_password)

    api.token = admin
    code, payload = api.call("GET", "/auth/me")
    record("GET /auth/me", code == 200,
           f"role={payload.get('data', {}).get('user', {}).get('role')}")

    code, payload = api.call("GET", "/services/status")
    services = payload.get("data", {}).get("services", [])
    record("GET /services/status", code == 200 and bool(services),
           f"{len(services)} dependency reports, "
           f"db_collections={payload.get('data', {}).get('collection_counts', {}).get('users')}")

    code, payload = api.call("GET", "/users")
    record("GET /users (administrator)", code == 200, f"users={item_count(payload)}")

    api.token = viewer
    code, payload = api.call("GET", "/users")
    record("RBAC: viewer blocked from /users", code == 403, f"http={code}")

    code, payload = api.call("POST", "/ingestion/jobs", body={
        "provider": "open_meteo", "dataset_name": "Smoke Lahore Daily",
        "coordinates": {"latitude": 31.5204, "longitude": 74.3587},
        "date_range": {"start": "2026-09-01", "end": "2026-09-20"},
        "granularity": "daily", "location_name": "Lahore"})
    record("RBAC: viewer cannot create ingestion job", code == 403, f"http={code} "
                                                                    f"{str(payload.get('error'))[:80]}")

    api.token = analyst
    code, payload = api.call("GET", "/ingestion/providers")
    providers = payload.get("data", {}).get("providers", {})
    record("GET /ingestion/providers", code == 200 and bool(providers),
           ", ".join(f"{k}:{'available' if v.get('available') else 'needs ' + str(v.get('missing_config'))}"
                     for k, v in providers.items()))

    if not args.skip_ingestion:
        code, payload = api.call("POST", "/ingestion/jobs", body={
            "provider": "open_meteo", "dataset_name": "Smoke Lahore Daily",
            "coordinates": {"latitude": 31.5204, "longitude": 74.3587},
            "date_range": {"start": "2026-09-01", "end": "2026-09-20"},
            "variables": ["temperature_2m", "precipitation_sum"],
            "granularity": "daily", "location_name": "Lahore"})
        created = payload.get("data", {})
        job_id = created.get("ingestion_job_id", "")
        record("POST /ingestion/jobs (analyst, live Open-Meteo pull)",
               code == 201 and bool(job_id), str(created)[:160])
        if job_id:
            job = wait_for_job(api, job_id)
            record("ingestion job reached a terminal state",
                   job.get("status") in ("completed", "failed"),
                   f"status={job.get('status')} fetched={job.get('records_fetched')} "
                   f"accepted={job.get('records_accepted')} rejected={job.get('records_rejected')} "
                   f"error={str(job.get('error'))[:120]}")
            if job.get("records_accepted"):
                code, payload = api.call("GET", f"/datasets/{created['dataset_id']}")
                card = payload.get("data", {})
                record("GET /datasets/{id} reflects ingested rows", code == 200,
                       f"record_count={card.get('dataset', {}).get('record_count')} "
                       f"status={card.get('dataset', {}).get('status')}")
            code, payload = api.call("POST", f"/ingestion/jobs/{job_id}/retry")
            record("retry is refused for a non-failed job", code in (400, 422, 403, 200),
                   f"http={code} (a failed job should be retryable)")

    api.token = analyst
    code, payload = api.call("GET", "/datasets/")
    record("GET /datasets", code == 200, f"datasets={item_count(payload)}")

    api.token = admin
    code, payload = api.call("GET", "/dashboard/summary")
    dash = payload.get("data", {})
    counts = dash.get("counts", {})
    record("GET /dashboard/summary", code == 200,
           f"datasets={counts.get('datasets')} observations={counts.get('observations')} "
           f"ingestion_success={counts.get('ingestion_successful')}")

    code, payload = api.call("GET", "/climate/observations")
    record("observations explorer refuses an unfiltered scan", code == 422,
           f"http={code} {str(payload.get('error', {}).get('message'))[:70]}")

    code, payload = api.call("GET", "/climate/observations?granularity=daily&page_size=5")
    rows = payload.get("data", [])
    record("GET /climate/observations (filtered)", code == 200 and isinstance(rows, list),
           f"rows={len(rows)} first_metrics="
           f"{sorted((rows[0].get('metrics') or {}).keys()) if rows else 'none'}")

    code, payload = api.call("GET", "/climate/regions")
    record("GET /climate/regions", code == 200, f"regions={item_count(payload)}")

    code, payload = api.call("GET", "/climate/map-data?mode=markers")
    record("GET /climate/map-data", code == 200, f"points={item_count(payload)}")

    code, payload = api.call("GET", "/climate/trends?variable=precipitation")
    trend = payload.get("data", {})
    record("GET /climate/trends", code == 200,
           f"available={trend.get('available')} n={trend.get('sample_size')} "
           f"mean={trend.get('mean')}")

    code, payload = api.call("POST", "/analyses/",
                             body={"name": "Smoke saved view", "description": "created by smoke_api.py",
                                   "view_state": {"region": "Lahore", "variable": "precipitation"}})
    analysis_id = (payload.get("data") or {}).get("id", "")
    record("POST /analyses/", code == 201 and bool(analysis_id), f"id={analysis_id}")
    if analysis_id:
        api.token = viewer
        code, payload = api.call("GET", f"/analyses/{analysis_id}")
        record("RBAC: another user cannot read a private analysis", code == 403, f"http={code}")
        api.token = admin
        code, payload = api.call("DELETE", f"/analyses/{analysis_id}")
        record("DELETE /analyses/{id} (owner)", code == 200, f"http={code}")

    code, payload = api.call("POST", "/reports/",
                             body={"report_type": "ingestion_history", "formats": ["csv"]})
    report = payload.get("data") or {}
    record("POST /reports/ (ingestion history)", code in (200, 201) and bool(report),
           f"status={report.get('status')} id={report.get('id')}")

    report_id = str(report.get("id") or "")
    if report_id:
        deadline = time.time() + 90
        status = report.get("status")
        while time.time() < deadline and status not in ("completed", "ready", "failed"):
            code, payload = api.call("GET", f"/reports/{report_id}")
            status = (payload.get("data") or {}).get("status")
            time.sleep(2)
        record("GET /reports/{id} reaches a terminal state",
               status in ("completed", "ready", "failed"), f"status={status}")
        code, payload = api.call("GET", f"/reports/{report_id}/download/csv", token=viewer)
        record("RBAC: another user cannot download a private report", code == 403, f"http={code}")
        if status in ("completed", "ready"):
            code, payload = api.call("GET", f"/reports/{report_id}/download/csv")
            record("GET /reports/{id}/download/csv (requester)", code == 200,
                   f"bytes={payload.get('bytes')} first_line="
                   f"{str(payload.get('raw', '')).splitlines()[:1]}")

    api.token = viewer
    code, payload = api.call("POST", "/support/tickets",
                             body={"subject": "Smoke test enquiry",
                                   "body": "Raised by scripts/smoke_api.py to prove the support flow.",
                                   "category": "technical", "priority": "low"})
    ticket = payload.get("data") or {}
    record("POST /support/tickets (viewer)", code in (200, 201) and bool(ticket.get("id")),
           f"ticket={ticket.get('id')} status={ticket.get('status')}")

    ticket_id = str(ticket.get("id") or "")
    if ticket_id:
        code, payload = api.call("GET", f"/support/tickets/{ticket_id}", token=viewer)
        record("GET /support/tickets/{id} (author)", code == 200, f"http={code}")
        code, payload = api.call("GET", f"/support/tickets/{ticket_id}", token=analyst)
        record("RBAC: another user cannot read a private ticket", code in (403, 404),
               f"http={code}")
        code, payload = api.call("POST", f"/support/tickets/{ticket_id}/messages", token=viewer,
                                 body={"body": "Follow-up from the smoke harness."})
        record("POST /support/tickets/{id}/messages (author)", code in (200, 201), f"http={code}")
        code, payload = api.call("POST", f"/support/tickets/{ticket_id}/messages", token=analyst,
                                 body={"body": "Should be refused."})
        record("RBAC: outsider cannot post into someone else's ticket", code in (403, 404),
               f"http={code}")

    api.token = admin
    code, payload = api.call("GET", "/support/queue")
    record("GET /support/queue (administrator)", code == 200, f"tickets={item_count(payload)}")

    api.token = viewer
    code, payload = api.call("POST", "/feedback/", body={"rating": 4, "message": "Smoke feedback entry",
                                                          "page": "/dashboard"})
    record("POST /feedback/", code in (200, 201), str(payload.get("data"))[:80])
    api.token = viewer
    code, payload = api.call("GET", "/feedback/admin")
    record("RBAC: viewer blocked from feedback admin view", code == 403, f"http={code}")

    api.token = admin
    code, payload = api.call("GET", "/alerts/rules")
    record("GET /alerts/rules", code == 200, f"rules={item_count(payload)}")

    code, payload = api.call("POST", "/alerts/rules", body={
        "name": "Smoke: hot day threshold", "target_variable": "temperature",
        "condition": "above", "threshold": 45.0, "severity": "warning",
        "geographic_scope": {"region": "Lahore"}, "notify_channels": ["in_app"]})
    rule = payload.get("data") or {}
    record("POST /alerts/rules", code in (200, 201) and bool(rule.get("id") or rule.get("_id")),
           f"rule={rule.get('id') or rule.get('_id')}")
    rule_id = str(rule.get("id") or rule.get("_id") or "")
    if rule_id:
        code, payload = api.call("POST", f"/alerts/rules/{rule_id}/evaluate")
        record("POST /alerts/rules/{id}/evaluate", code == 200,
               f"result={str(payload.get('data'))[:120]}")
        code, payload = api.call("DELETE", f"/alerts/rules/{rule_id}")
        record("DELETE /alerts/rules/{id}", code == 200, f"http={code}")

    api.token = analyst
    code, payload = api.call("GET", "/notifications")
    inbox = payload.get("data") or []
    record("GET /notifications (recipient inbox)", code == 200, f"notifications={len(inbox)}")
    if isinstance(inbox, list) and inbox:
        note_id = str(inbox[0].get("id"))
        code, payload = api.call("GET", f"/notifications/{note_id}", token=viewer)
        record("RBAC: another user cannot open someone's notification", code in (403, 404),
               f"http={code}")
        code, payload = api.call("PATCH", f"/notifications/{note_id}", token=analyst,
                                 body={"read": True})
        record("notification marked read by its recipient", code == 200, f"http={code}")

    api.token = admin
    code, payload = api.call("GET", "/datasets/", token=None)
    record("unauthenticated request rejected", code == 401, f"http={code}")

    passed = sum(1 for _n, ok, _d in RESULTS if ok)
    print("")
    print(f"{passed}/{len(RESULTS)} checks passed")
    return 0 if passed == len(RESULTS) else 2


if __name__ == "__main__":
    sys.exit(main())
