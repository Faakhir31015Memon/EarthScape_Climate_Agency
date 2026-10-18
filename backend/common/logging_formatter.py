"""Single-line structured log formatter.

Values that look like credentials are redacted before they can reach a log sink.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

SECRET_PATTERNS = [
    re.compile(r"(mongodb(\+srv)?://)[^\s'\"]+", re.IGNORECASE),
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE),
    re.compile(r'("(?:password|password_hash|token|refresh|access_token|api_key|secret)"\s*:\s*)"[^"]*"',
               re.IGNORECASE),
]


class StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001
            message = str(record.msg)
        for pattern in SECRET_PATTERNS:
            message = pattern.sub(r"\1***redacted***", message)

        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
        }
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception_type"] = record.exc_info[0].__name__
            payload["exception"] = self.formatException(record.exc_info)[:2000]
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        return json.dumps(payload, ensure_ascii=False, default=str)
