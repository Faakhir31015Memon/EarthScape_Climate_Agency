"""Bounded scheduler thread for alert-rule evaluation.

Design constraints:

* exactly one daemon thread per process, started at most once (``start`` is
  idempotent and thread-safe);
* the interval comes from ``ALERT_EVALUATION_SECONDS`` (default 300, clamped to
  30-3600) and is validated rather than trusted;
* shutdown is graceful - ``stop()`` sets an event and joins with a bounded timeout;
* everything the thread knows (runs, last counts, last error, next run) is exposed
  through :func:`status`, which the health and system endpoints report verbatim.
  Nothing here claims the engine ran if it did not.
"""
from __future__ import annotations

import atexit
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from apps.monitoring import import_compat

import_compat.ensure_common_api_importable()

from apps.alerts import notifications, rules  # noqa: E402
from common.repositories import alerts as alerts_repository  # noqa: E402
from config import env  # noqa: E402

logger = logging.getLogger(__name__)

MIN_INTERVAL = 30
MAX_INTERVAL = 3600
DEFAULT_INTERVAL = 300
JOIN_TIMEOUT_SECONDS = 10.0

_LOCK = threading.Lock()
_THREAD: Optional[threading.Thread] = None
_STOP = threading.Event()
_STATE: Dict[str, Any] = {
    "running": False,
    "started_at": None,
    "interval_seconds": DEFAULT_INTERVAL,
    "runs": 0,
    "errors": 0,
    "consecutive_errors": 0,
    "last_run_at": None,
    "next_run_at": None,
    "last_result": None,
    "last_error": None,
    "manual_runs": 0,
    "stop_requested_at": None,
    "thread_name": None,
}


def interval_seconds() -> int:
    raw = env.get_int("ALERT_EVALUATION_SECONDS", DEFAULT_INTERVAL)
    if raw <= 0:
        return 0
    return max(MIN_INTERVAL, min(raw, MAX_INTERVAL))


def autostart_enabled() -> bool:
    if env.get_raw("ALERT_EVALUATION_SECONDS") is not None and interval_seconds() == 0:
        return False
    return env.get_bool("ALERT_SCHEDULER_ENABLED", True)


def status() -> Dict[str, Any]:
    """Honest runtime state of the scheduler and the evaluation counters."""
    with _LOCK:
        snapshot = dict(_STATE)
    alive = bool(_THREAD and _THREAD.is_alive())
    snapshot["thread_alive"] = alive
    snapshot["configured_interval_seconds"] = env.get_int("ALERT_EVALUATION_SECONDS",
                                                          DEFAULT_INTERVAL)
    snapshot["effective_interval_seconds"] = interval_seconds()
    snapshot["autostart_enabled"] = autostart_enabled()
    snapshot["uptime_seconds"] = (
        round(time.time() - snapshot["started_monotonic"], 2)
        if snapshot.get("started_monotonic") else None)
    snapshot.pop("started_monotonic", None)
    snapshot["enabled_rule_count"] = _safe_enabled_rule_count()
    return snapshot


def _safe_enabled_rule_count() -> Optional[int]:
    try:
        return rules.alerts.count({"document_type": rules.RULE_MARKER, "is_enabled": True})
    except Exception:  # noqa: BLE001 - status must never raise
        return None


def start(*, reason: str = "explicit") -> Dict[str, Any]:
    """Start the scheduler thread if it is not already running."""
    global _THREAD
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return status()
        interval = interval_seconds()
        if interval <= 0:
            _STATE.update({"running": False, "last_error": "scheduler disabled "
                                                              "(ALERT_EVALUATION_SECONDS<=0)"})
            return status()
        _STOP.clear()
        _STATE.update({
            "running": True,
            "started_at": datetime.now(timezone.utc),
            "started_monotonic": time.monotonic(),
            "interval_seconds": interval,
            "last_error": None,
            "consecutive_errors": 0,
            "stop_requested_at": None,
            "reason": reason,
        })
        _STATE["next_run_at"] = datetime.now(timezone.utc)
        thread = threading.Thread(target=_loop, name="earthscape-alert-monitor", daemon=True)
        _THREAD = thread
        _STATE["thread_name"] = thread.name
        thread.start()
    atexit.register(shutdown)
    logger.info("Alert scheduler started interval_seconds=%s reason=%s", interval, reason)
    return status()


def ensure_started() -> Dict[str, Any]:
    """Start on import of the alerts URLs when the deployment asks for it."""
    if not autostart_enabled():
        with _LOCK:
            _STATE["autostart_skipped"] = True
        return status()
    return start(reason="alerts urls import")


def stop(timeout: float = JOIN_TIMEOUT_SECONDS) -> Dict[str, Any]:
    """Signal the loop and join it, with a bounded wait."""
    global _THREAD
    with _LOCK:
        thread = _THREAD
        _STATE["stop_requested_at"] = datetime.now(timezone.utc)
        _STATE["running"] = False
    _STOP.set()
    joined = True
    if thread is not None and thread.is_alive():
        thread.join(timeout=timeout)
        joined = not thread.is_alive()
    with _LOCK:
        _STATE["stopped_at"] = datetime.now(timezone.utc) if joined else None
        _STATE["running"] = not joined
        _STATE["stop_error"] = None if joined else f"thread still alive after {timeout}s"
    logger.info("Alert scheduler stopped cleanly=%s", joined)
    return status()


def shutdown() -> None:
    """``atexit`` hook: graceful stop without hanging interpreter exit."""
    try:
        stop(timeout=3.0)
    except Exception as exc:  # noqa: BLE001 - interpreter teardown must not raise
        logger.warning("Alert scheduler shutdown failed: %s", exc)


def run_once(*, source: str = "manual") -> Dict[str, Any]:
    """Evaluate now and record the real result (used by the API and the loop)."""
    started = time.monotonic()
    result = rules.evaluate_enabled_rules()
    notification_outcome = notifications.retry_due_notifications(limit=50)
    duration_ms = round((time.monotonic() - started) * 1000, 2)
    with _LOCK:
        _STATE["runs"] = int(_STATE["runs"]) + 1
        _STATE["last_run_at"] = datetime.now(timezone.utc)
        _STATE["last_result"] = {
            "rules_evaluated": result.get("rules_evaluated", 0),
            "alerts_created": result.get("alerts_created", 0),
            "notifications_queued": result.get("notifications_queued", 0),
            "suppressed_by_dedupe": result.get("suppressed_by_dedupe", 0),
            "rules_with_errors": result.get("rules_with_errors", 0),
            "retry_outcome": notification_outcome,
            "duration_ms": duration_ms,
            "source": source,
        }
        _STATE["consecutive_errors"] = 0
        _STATE["last_error"] = None
        if source == "manual":
            _STATE["manual_runs"] = int(_STATE["manual_runs"]) + 1
    return dict(_STATE["last_result"])


def _loop() -> None:
    while not _STOP.is_set():
        interval = interval_seconds() or DEFAULT_INTERVAL
        _set_next_run(interval)
        if _STOP.wait(timeout=interval):
            break
        try:
            run_once(source="scheduler")
        except Exception as exc:  # noqa: BLE001 - the loop must survive evaluation errors
            with _LOCK:
                _STATE["errors"] = int(_STATE["errors"]) + 1
                _STATE["consecutive_errors"] = int(_STATE["consecutive_errors"]) + 1
                _STATE["last_error"] = f"{type(exc).__name__}: {str(exc)[:180]}"
            backoff = min(interval * 2, 900)
            logger.error("Alert evaluation failed (%s); retrying in %ss",
                         type(exc).__name__, backoff)
            if _STOP.wait(timeout=backoff):
                break
    with _LOCK:
        _STATE["running"] = False
        _STATE["next_run_at"] = None
        _STATE["loop_exited_at"] = datetime.now(timezone.utc)


def _set_next_run(interval: int) -> None:
    with _LOCK:
        _STATE["next_run_at"] = datetime.now(timezone.utc).replace(
            microsecond=0) + timedelta_seconds(interval)


def timedelta_seconds(value: int):
    from datetime import timedelta

    return timedelta(seconds=int(value))
