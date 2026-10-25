"""Periodic platform maintenance.

This is the single scheduled entry point for the recurring work the platform promises:
alert-rule sweeps, scheduled provider ingestion, abandoned-job reconciliation and email
delivery retries. In a cluster deployment Airflow (see ``backend/infrastructure/airflow``) calls the
same command on a schedule; running it here needs nothing but the backend and MongoDB, so
the behaviour is identical with or without the scheduler service.
"""

from __future__ import annotations

import json
import time

from django.core.management.base import BaseCommand

from common.db import collection, now_utc
from common.tasks import sweep_stale_jobs

TASKS = ("alerts", "ingestion", "jobs", "notifications")
NOTIFICATION_RETRY_LIMIT = 5


class Command(BaseCommand):
    help = (__doc__ or "").strip().splitlines()[0]

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true",
                            help="run every task a single time and exit (for cron/Airflow)")
        parser.add_argument("--interval", type=int, default=300,
                            help="seconds between passes when running as a loop (default 300)")
        parser.add_argument("--tasks", default=",".join(TASKS),
                            help="comma separated subset of: " + ", ".join(TASKS))
        parser.add_argument("--include-disabled", action="store_true",
                            help="alert sweep also evaluates disabled rules")
        parser.add_argument("--force-ingestion", action="store_true",
                            help="ignore the ingestion schedule's due window and pull now")
        parser.add_argument("--async-ingestion", action="store_true",
                            help="submit provider pulls to the background engine instead of "
                                 "running them inside this process")
        parser.add_argument("--max-age-minutes", type=int, default=120,
                            help="jobs still 'running' after this long are marked stale")
        parser.add_argument("--json", action="store_true", dest="as_json",
                            help="print one JSON object per pass instead of text")

    def handle(self, *args, **options):
        selected = [item.strip() for item in options["tasks"].split(",") if item.strip()]
        unknown = [item for item in selected if item not in TASKS]
        if unknown:
            self.stderr.write(f"Unknown task(s): {', '.join(unknown)}. Valid: {', '.join(TASKS)}")
            return 2
        if options["interval"] < 5 and not options["once"]:
            self.stderr.write("--interval must be at least 5 seconds")
            return 2

        loop = not options["once"]
        if loop:
            self.stdout.write(f"Scheduler loop started: every {options['interval']}s "
                              f"[{', '.join(selected)}]. Ctrl-C to stop.")
        while True:
            summary = self.run_pass(selected, options)
            self.report(summary, as_json=options["as_json"])
            if not loop:
                return 1 if summary["failures"] else 0
            time.sleep(options["interval"])

    def run_pass(self, selected: list[str], options) -> dict:
        moment = now_utc()
        passes: dict[str, dict] = {}
        if "jobs" in selected:
            passes["jobs"] = self.reconcile_jobs(options["max_age_minutes"])
        if "alerts" in selected:
            passes["alerts"] = self.sweep_alerts(options["include_disabled"])
        if "ingestion" in selected:
            passes["ingestion"] = self.scheduled_ingestion(options)
        if "notifications" in selected:
            passes["notifications"] = self.retry_notifications()
        failures = [f"{name}: {data['error']}" for name, data in passes.items() if data.get("error")]
        return {"ran_at": moment.isoformat(), "passes": passes, "failures": failures}

    def report(self, summary: dict, *, as_json: bool) -> None:
        if as_json:
            self.stdout.write(json.dumps(summary, default=str))
            return
        self.stdout.write(f"\n=== scheduler pass {summary['ran_at']} ===")
        for name, data in summary["passes"].items():
            self.stdout.write(f"  {name:<14} {_brief(data)}")
        for failure in summary["failures"]:
            self.stderr.write(f"  FAILED {failure}")

    # --- tasks ---------------------------------------------------------------

    def reconcile_jobs(self, max_age_minutes: int) -> dict:
        """Mark jobs abandoned by a crashed worker as failed, in both job collections."""
        from common.tasks import TASK_COLLECTION

        swept = {name: sweep_stale_jobs(max_age_minutes, collection_name=name)
                 for name in (TASK_COLLECTION, "ingestion_jobs")}
        return {"marked_stale": swept, "threshold": f"{max_age_minutes} minutes",
                "collections": list(swept)}

    def sweep_alerts(self, include_disabled: bool) -> dict:
        from apps.alerts import services as alerts

        result = alerts.evaluate_sweep(include_disabled=include_disabled)
        return {"rules_evaluated": result["rules_evaluated"],
                "events_created": result["events_created"],
                "status_counts": result["status_counts"]}

    def scheduled_ingestion(self, options) -> dict:
        from apps.ingestion import scheduling

        result = scheduling.run_due_pulls(wait=not options["async_ingestion"],
                                          force=options["force_ingestion"])
        started = [item for item in result.get("results", []) if item.get("started")]
        counts = [item.get("outcome", {}).get("records_accepted") for item in started
                  if isinstance(item.get("outcome"), dict)]
        return {k: v for k, v in result.items() if k != "results"} | {
            "detail": result.get("detail", ""),
            "accepted": sum(int(value or 0) for value in counts),
        }

    def retry_notifications(self) -> dict:
        from apps.alerts import notifier

        pending = {"channel": "email",
                   "delivery_status": {"$in": [notifier.FAILED, notifier.PENDING_EMAIL_DISABLED]},
                   "retry_count": {"$lt": NOTIFICATION_RETRY_LIMIT}}
        rows = list(collection("notifications").find(pending, {"_id": 1},
                                                     sort=[("created_at", 1)], limit=20))
        statuses: dict[str, int] = {}
        for row in rows:
            collection("notifications").update_one(
                {"_id": row["_id"]}, {"$inc": {"retry_count": 1},
                                      "$set": {"last_retry_at": now_utc()}})
            outcome = notifier.deliver(str(row["_id"]))
            key = str(outcome.get("delivery_status"))
            statuses[key] = statuses.get(key, 0) + 1
        exhausted = collection("notifications").count_documents(
            {"channel": "email", "delivery_status": {"$ne": notifier.DELIVERED},
             "retry_count": {"$gte": NOTIFICATION_RETRY_LIMIT}})
        return {"attempted": len(rows), "delivery_statuses": statuses,
                "gave_up_after_retries": exhausted, "retry_limit": NOTIFICATION_RETRY_LIMIT}


def _brief(data: dict) -> str:
    if data.get("error"):
        return f"ERROR {data['error']}"
    parts = []
    for key, value in data.items():
        if isinstance(value, dict):
            parts.append(f"{key}={_brief(value)}")
        elif isinstance(value, list):
            parts.append(f"{key}={len(value)}")
        elif value not in ("", None):
            parts.append(f"{key}={value}")
    return ", ".join(parts) or "nothing to do"
