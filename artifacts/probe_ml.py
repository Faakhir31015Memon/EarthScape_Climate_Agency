"""Temporary probe: timing + response shapes for training, forecast and anomaly scan."""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000/api/v1"


def call(method, path, token="", body=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8") or "{}")


def main() -> int:
    password = os.environ.get("SMOKE_ANALYST_PASSWORD", "")
    if not password:
        print("SMOKE_ANALYST_PASSWORD not set")
        return 2
    _code, payload = call("POST", "/auth/login", body={"email": "analyst@earthscape.pk",
                                                       "password": password})
    token = (payload.get("data") or {}).get("access_token", "")

    code, payload = call("GET", "/models/", token)
    rows = payload.get("data") or []
    print("registry", code, len(rows))
    for row in rows[:4]:
        print("   ", row.get("model_ref"), row.get("status"), row.get("algorithm"),
              "metrics=", bool(row.get("metrics")))
    ref = str(rows[0].get("model_ref")) if rows else ""

    code, payload = call("GET", f"/models/{ref}", token)
    detail = payload.get("data") or {}
    print("detail", code, sorted(detail)[:14])

    started = time.time()
    code, payload = call("POST", "/models/train", token,
                         {"target_variable": "temperature", "region": "Lahore",
                          "granularity": "daily", "horizon_steps": 30,
                          "models": ["baseline", "linear"], "register_as_candidate": True})
    job = (payload.get("data") or {}).get("job") or {}
    print("train", code, job.get("id"), str(payload.get("error"))[:200])
    status = job.get("status")
    while time.time() - started < 300:
        code, payload = call("GET", f"/models/jobs/{job.get('id')}", token)
        data = payload.get("data") or {}
        status = data.get("status")
        if status in ("completed", "failed"):
            break
        time.sleep(4)
    print("train final", status, f"{time.time() - started:.0f}s",
          str((payload.get("data") or {}).get("error"))[:200])
    print("train log tail", json.dumps((payload.get("data") or {}).get("log", [])[-3:],
                                        default=str)[:500])

    code, payload = call("POST", "/predictions/forecast", token,
                         {"model_ref": ref, "target_variable": "temperature",
                          "region": "Lahore", "horizon_steps": 3})
    pred = payload.get("data") or {}
    print("forecast", code, pred.get("id"), pred.get("mode"),
          str(payload.get("error"))[:220])
    series = (pred.get("prediction") or {}).get("series") or (pred.get("prediction") or {})
    print("   series:", json.dumps(series, default=str)[:300])

    code, payload = call("GET", "/predictions/latest", token)
    print("latest", code, json.dumps(payload.get("data"), default=str)[:200])

    code, payload = call("POST", "/anomaly/scan", token,
                         {"variable": "temperature", "method": "statistical",
                          "region": "Lahore", "window_days": 365})
    print("scan", code, json.dumps(payload.get("data") or payload.get("error"), default=str)[:500])

    code, payload = call("GET", "/anomaly/baselines", token)
    print("baselines", code, json.dumps(payload.get("data"), default=str)[:250])
    return 0


if __name__ == "__main__":
    sys.exit(main())
