"""DRF serializers for the processing API surface.

Validation is deliberately strict: a client may only send a registry workflow id
and free-form parameters that are checked against the workflow's parameter spec
by :func:`validate_parameters`. There is never a raw command, module path or
callable in a request payload.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from rest_framework import serializers

from apps.monitoring import import_compat

import_compat.ensure_common_api_importable()

from apps.processing import workflows  # noqa: E402
from apps.processing.jobs import sanitize_error  # noqa: E402
from common.api import ApiError  # noqa: E402


class StartProcessingJobSerializer(serializers.Serializer):
    workflow = serializers.CharField(max_length=80)
    dataset_id = serializers.CharField(required=False, allow_blank=True, allow_null=True,
                                              max_length=24)
    parameters = serializers.DictField(required=False, default=dict)

    def validate_workflow(self, value: str) -> str:
        value = str(value or "").strip()
        if value not in workflows.WORKFLOWS:
            raise serializers.ValidationError(
                {"workflow": f"Unknown processing workflow '{value}'. "
                              f"Allowed: {', '.join(workflows.workflow_ids())}."})
        return value

    def validate_dataset_id(self, value: Optional[str]) -> Optional[str]:
        if value in (None, ""):
            return None
        value = str(value).strip()
        if len(value) != 24 or not all(c in "0123456789abcdefABCDEF" for c in value):
            raise serializers.ValidationError("dataset_id must be a 24-character hex identifier.")
        return value

    def validate_parameters(self, value: Dict[str, Any]) -> Dict[str, Any]:
        return value if isinstance(value, dict) else {}


def validate_parameters(workflow_id: str, provided: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce + check client parameters against the registry spec.

    Rejects unknown keys, so nothing but the documented parameter names can
    reach the executor - and every value is bounded by the spec.
    """
    entry = workflows.require_workflow(workflow_id)
    spec: Dict[str, Any] = entry.get("parameter_spec") or {}
    if not isinstance(provided, dict):
        raise ApiError("validation_failed", "parameters must be an object.")
    unknown = sorted(str(key) for key in provided if key not in spec)
    if unknown:
        raise ApiError("validation_failed",
                       "parameters contain unknown keys.",
                       details={"unknown": unknown[:20], "allowed": sorted(spec.keys())})
    out: Dict[str, Any] = {}
    errors: Dict[str, str] = {}
    for key, definition in spec.items():
        raw = provided.get(key)
        if raw in (None, "") and not definition.get("required"):
            default = definition.get("default")
            if default is not None:
                out[key] = default
            continue
        try:
            out[key] = _coerce(key, raw, definition)
        except ValueError as exc:
            errors[key] = str(exc)
    if errors:
        raise ApiError("validation_failed", "parameters failed validation.", details=errors)
    return out


def _coerce(key: str, raw: Any, definition: Dict[str, Any]) -> Any:
    kind = definition.get("type")
    if kind == "int":
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} must be an integer") from exc
        minimum = definition.get("min")
        maximum = definition.get("max")
        if minimum is not None and value < int(minimum):
            raise ValueError(f"{key} must be >= {minimum}")
        if maximum is not None and value > int(maximum):
            raise ValueError(f"{key} must be <= {maximum}")
        return value
    if kind == "float":
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} must be a number") from exc
        minimum = definition.get("min")
        maximum = definition.get("max")
        if minimum is not None and value < float(minimum):
            raise ValueError(f"{key} must be >= {minimum}")
        if maximum is not None and value > float(maximum):
            raise ValueError(f"{key} must be <= {maximum}")
        return value
    if kind == "bool":
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"{key} must be true or false")
    if kind == "choice":
        choices = tuple(definition.get("choices") or ())
        value = str(raw).strip()
        if value not in choices:
            raise ValueError(f"{key} must be one of: {', '.join(choices)}")
        return value
    if kind == "date":
        text = str(raw).strip()
        try:
            datetime.strptime(text, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(f"{key} must use the YYYY-MM-DD format") from exc
        return text
    # 'str' and unknown fall through to a bounded string. Never join this to a
    # filesystem path unsanitized - the executors only accept server-side paths.
    text = str(raw).strip()
    if len(text) > 500:
        raise ValueError(f"{key} must be at most 500 characters")
    if not text:
        raise ValueError(f"{key} must not be empty")
    return text


def _present_log_lines(raw_lines: Any) -> list:
    """Normalise the stored ``log_lines`` array into the API shape.

    Historical documents predate the structured logger and may hold a bare
    string per line, so both dict and str entries are accepted (a str is
    surfaced as an unlabelled ``info`` message). Anything else is dropped.
    """
    if not isinstance(raw_lines, list):
        return []
    out: list = []
    for line in raw_lines[-200:]:
        if isinstance(line, dict):
            out.append(
                {
                    "at": line.get("at"),
                    "level": line.get("level") or "info",
                    "message": sanitize_error(str(line.get("message") or "")),
                }
            )
        elif isinstance(line, str):
            out.append({"at": None, "level": "info", "message": sanitize_error(line)})
    return out


def present_workflow(entry: Dict[str, Any], availability: Dict[str, Any]) -> Dict[str, Any]:
    """Shape for ProcessingWorkflow in frontend/src/types/api.ts."""
    return {
        "id": entry["id"],
        "label": entry["label"],
        "description": entry["description"],
        "engine": entry["engine"],
        "requires": list(entry.get("requires") or []),
        "available": bool(availability.get("available")),
        "unavailable_reason": availability.get("reason"),
        "parameter_spec": entry.get("parameter_spec") or {},
    }


def present_job(document: Dict[str, Any]) -> Dict[str, Any]:
    """Shape for ProcessingJob in the TypeScript contract."""
    if document is None:
        return {}
    job_id = str(document.get("id") or document.get("_id") or "")
    log_lines = document.get("log_lines") or []
    return {
        "id": job_id,
        "workflow": document.get("workflow"),
        "workflow_label": document.get("workflow_label"),
        "engine": document.get("engine"),
        "dataset_id": document.get("dataset_id"),
        "dataset_name": document.get("dataset_name"),
        "status": document.get("status"),
        "submitted_at": document.get("submitted_at"),
        "started_at": document.get("started_at"),
        "finished_at": document.get("finished_at"),
        "duration_seconds": document.get("duration_seconds"),
        "submitted_by": document.get("submitted_by"),
        "submitted_by_name": document.get("submitted_by_name"),
        "parameters": document.get("parameters") or {},
        "metrics": document.get("metrics"),
        "output": document.get("output"),
        "log_lines": _present_log_lines(log_lines),
        "error_message": sanitize_error(document.get("error_message")) if document.get("error_message") else None,
        "processing_mode": document.get("processing_mode"),
        "retry_of": document.get("retry_of"),
        "attempt": document.get("attempt") or 1,
    }
