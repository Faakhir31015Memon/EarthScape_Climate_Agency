"""Temporary probe: what do the Phase 4/6 endpoints actually return for a run?

Reads SMOKE_ANALYST_PASSWORD from the environment; nothing is hardcoded.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8000/api/v1"


def call(method, path, token="", body=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8") or "{}")


def main() -> int:
    password = os.environ.get("SMOKE_ANALYST_PASSWORD", "")
    if not password:
        print("SMOKE_ANALYST_PASSWORD not set")
        return 2
    code, payload = call("POST", "/auth/login", body={"email": "analyst@earthscape.pk",
                                                      "password": password})
    print("login", code)
    token = (payload.get("data") or {}).get("access_token", "")
    if not token:
        print(str(payload)[:200])
        return 1

    code, payload = call("GET", "/processing/workflows", token)
    workflows = (payload.get("data") or {}).get("workflows") or []
    print("workflows", code, [w.get("id") for w in workflows][:8])
    first = workflows[0]["id"] if workflows else ""
    print("first describe:", json.dumps(workflows[0], default=str)[:600] if workflows else "none")

    code, payload = call("POST", "/processing/jobs", token,
                         {"workflow": "profile_missing", "parameters": {}})
    print("POST profile_missing", code, json.dumps(payload.get("data") or payload.get("error"),
                                                   default=str)[:600])
    job_id = str((payload.get("data") or {}).get("job_id") or "")
    deadline = time.time() + 120
    while job_id and time.time() < deadline:
        code, payload = call("GET", f"/processing/jobs/{job_id}", token)
        job = payload.get("data") or {}
        print("  poll", code, job.get("status"), str(job.get("error"))[:120])
        if job.get("status") in ("completed", "failed"):
            break
        time.sleep(3)

    code, payload = call("GET", "/models/available-data", token)
    data = payload.get("data") or {}
    print("available-data keys", list(data)[:12])
    print(json.dumps(data, default=str)[:900])
    return 0


if __name__ == "__main__":
    sys.exit(main())
