"""A dependency-free, daemon-threaded ingestion scheduler.

APScheduler is deliberately not used (it is not a project dependency).  This is a
plain ``threading`` implementation: a single daemon wakes on a configurable interval
and calls :func:`apps.ingestion.services.sync_all_scheduled`, which queues a bounded
recent-window sync for every *configured* live source.  Shutdown is graceful - the
worker observes an :class:`threading.Event` and exits within one poll cycle.

Enable it with ``INGESTION_SCHEDULER_ENABLED=true`` and tune the interval with
``INGESTION_SCHEDULER_INTERVAL_SECONDS`` (default 6 hours).
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from config import env

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 6 * 60 * 60  # 6 hours


class IngestionScheduler:
    def __init__(self, *, interval_seconds: Optional[int] = None, initial_delay_seconds: int = 30):
        self.interval = int(interval_seconds if interval_seconds is not None
                            else env.get_int("INGESTION_SCHEDULER_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS))
        self.interval = max(60, self.interval)
        self.initial_delay = max(0, initial_delay_seconds)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.last_run_at: Optional[datetime] = None
        self.last_result: List[Dict[str, Any]] = []
        self.run_count = 0
        self.error_count = 0

    def start(self) -> bool:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="earthscape-scheduler",
                                            daemon=True)
            self._thread.start()
        logger.info("Ingestion scheduler started (interval=%ss)", self.interval)
        return True

    def stop(self, timeout: float = 15.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        logger.info("Ingestion scheduler stopped")

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        # A short initial delay lets the process finish booting / DB connect first.
        if self._stop.wait(self.initial_delay):
            return
        while not self._stop.is_set():
            self.run_once()
            if self._stop.wait(self.interval):
                break

    def run_once(self) -> List[Dict[str, Any]]:
        from apps.ingestion import services

        try:
            results = services.sync_all_scheduled(triggered_by="schedule", requested_by=None)
            self.last_result = results
            self.run_count += 1
            queued = sum(1 for item in results if item.get("queued"))
            logger.info("Scheduler cycle queued %s job(s) across %s source(s)",
                        queued, len(results))
            return results
        except Exception as exc:  # noqa: BLE001 - the daemon must never die
            self.error_count += 1
            logger.error("Scheduler cycle failed: %s", str(exc)[:200])
            self.last_result = []
            return []
        finally:
            self.last_run_at = datetime.now(timezone.utc)

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": env.get_bool("INGESTION_SCHEDULER_ENABLED"),
            "running": self.running,
            "interval_seconds": self.interval,
            "run_count": self.run_count,
            "error_count": self.error_count,
            "last_run_at": self.last_run_at,
            "last_result": self.last_result,
        }


_SCHEDULER: Optional[IngestionScheduler] = None


def get_scheduler() -> IngestionScheduler:
    global _SCHEDULER
    if _SCHEDULER is None:
        _SCHEDULER = IngestionScheduler()
    return _SCHEDULER


def start_if_enabled() -> bool:
    """Start the background scheduler only when configured to be on."""
    if not env.get_bool("INGESTION_SCHEDULER_ENABLED"):
        return False
    return get_scheduler().start()


def shutdown() -> None:
    if _SCHEDULER is not None:
        _SCHEDULER.stop()
