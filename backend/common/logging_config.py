from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

SECRET_PATTERNS = [
    re.compile(r"(?i)(password|passwd|secret|token|authorization|api[_-]?key|credential)[\"']?\s*[:=]\s*[\"']?[^\s,;\"'}]+"),
    re.compile(r"mongodb(?:\+srv)?://[^\s'\"]+"),
]

REDACTED_FIELDS = {
    "password", "current_password", "new_password", "confirm_password", "token",
    "refresh_token", "access_token", "authorization", "api_key", "apikey", "noaa_api_token",
    "mongodb_uri", "secret", "secret_key", "password_hash",
}


def redact(value):
    if isinstance(value, dict):
        return {k: ("[redacted]" if k.lower() in REDACTED_FIELDS else redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        text = value
        for pattern in SECRET_PATTERNS:
            text = pattern.sub(lambda m: m.group(0).split("=")[0].split(":")[0] + "=[redacted]", text)
        return text[:2000]
    return value


class StructuredFormatter(logging.Formatter):
    converts = (datetime,)

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("correlation_id", "user_id", "role", "path", "method", "status", "duration_ms", "event"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info and record.exc_info[0]:
            payload["exception"] = f"{record.exc_info[0].__name__}: {str(record.exc_info[1])[:500]}"
        return json.dumps(redact(payload), default=str, ensure_ascii=False)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
