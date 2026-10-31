"""``/api/v1/file-analytics`` - profile an uploaded table without touching the climate schema.

A user picks one or more files, the backend reads them with the same streaming readers used
by ingestion, and the bounded summary is stored so the charts can be reopened later. The
uploads themselves are staging only: they are deleted once the summary exists, so nothing an
account analysed for fun ends up in the data lake.
"""
from __future__ import annotations

import hashlib
import logging
import re
import uuid
from pathlib import Path
from typing import Any, Dict

from django.conf import settings
from rest_framework import status
from rest_framework.response import Response

from apps.datasets import file_analytics, file_import
from common import audit
from common.api import ADMINISTRATOR, CLIMATE_ANALYST, ApiError
from common.repositories import file_analyses, maybe_object_id, serialize, utcnow
from common.views import AuthenticatedView, int_param, str_param

logger = logging.getLogger(__name__)

STAGING_DIR = "file-analytics"
SEARCH_FIELD_LIMIT = 60
MAX_LIST_PAGE_SIZE = 50


class FileAnalyticsUploadView(AuthenticatedView):
    """``POST /api/v1/file-analytics`` - multipart upload, analyse and store."""

    def post(self, request):
        user = self.current_user(request)
        uploads = request.FILES.getlist("files")
        single = request.FILES.get("file")
        if not uploads and single is not None:
            uploads = [single]
        if not uploads:
            raise ApiError("validation_failed", "Choose at least one file to analyse.")
        target_year = _target_year(request)

        allowed = {ext.lower() for ext in settings.ALLOWED_UPLOAD_EXTENSIONS}
        staged: list[Dict[str, Any]] = []
        try:
            for upload in uploads:
                extension = Path(upload.name or "").suffix.lower()
                if extension not in allowed:
                    raise ApiError("validation_failed",
                                   f"File extension '{extension or 'none'}' cannot be analysed. Allowed: "
                                   f"{', '.join(sorted(allowed))}.")
                entry = _stage_upload(upload, extension)
                entry["name"] = Path(str(upload.name or "upload")).name[:180]
                entry["format"] = file_import.sniff_format(entry["path"], extension)
                staged.append(entry)
            try:
                payload = file_analytics.analyse_files([entry["path"] for entry in staged],
                                                        [entry["format"] for entry in staged],
                                                        target_year=target_year)
            except file_analytics.FileAnalyticsError as exc:
                raise ApiError("validation_failed", str(exc))
        finally:
            for entry in staged:
                _discard(entry["path"])

        document = _document(user, staged, payload)
        analysis_id = file_analyses.insert_one(document)
        audit.for_request(request, action="file_analytics.created", resource_type="file_analysis",
                          resource_id=analysis_id,
                          metadata={"file_count": len(staged), "format": document["format"],
                                    "size_bytes": document["size_bytes"],
                                    "rows_analyzed": payload["rows_analyzed"],
                                    "column_count": payload["column_count"],
                                    "target_year": payload["forecast"].get("target_year")})
        stored = serialize(file_analyses.find_one({"_id": maybe_object_id(analysis_id)}))
        return Response({"analysis": stored}, status=status.HTTP_201_CREATED)


class FileAnalyticsListView(AuthenticatedView):
    """``GET /api/v1/file-analytics`` - history, newest first, summaries only."""

    def get(self, request):
        user = self.current_user(request)
        criteria = _visibility_criteria(user)
        search = str_param(request, "search")
        if search:
            escaped = re.sub(r"[.*+?^${}()|[\]\\]", r"\\\g<0>", search)[:SEARCH_FIELD_LIMIT]
            criteria["file_name"] = {"$regex": escaped, "$options": "i"}
        page = int_param(request, "page", 1, minimum=1, maximum=100_000)
        page_size = int_param(request, "page_size", 20, minimum=1, maximum=MAX_LIST_PAGE_SIZE)
        result = file_analyses.paginate(criteria, page=page, page_size=page_size,
                                        sort=[("created_at", -1)],
                                        projection={"payload": 0})
        return Response(result)


class FileAnalyticsDetailView(AuthenticatedView):
    """``GET``/``DELETE /api/v1/file-analytics/<id>``."""

    def get(self, request, analysis_id: str):
        user = self.current_user(request)
        document = _load(analysis_id)
        _require_access(user, document)
        return Response({"analysis": serialize(document)})

    def delete(self, request, analysis_id: str):
        user = self.current_user(request)
        document = _load(analysis_id)
        _require_access(user, document)
        file_analyses.delete_by_id(analysis_id)
        audit.for_request(request, action="file_analytics.deleted", resource_type="file_analysis",
                          resource_id=analysis_id,
                          metadata={"file_name": document.get("file_name")})
        return Response({"deleted": True, "id": analysis_id})


def _stage_upload(upload, extension: str) -> Dict[str, Any]:
    target_dir = Path(settings.UPLOAD_ROOT) / STAGING_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{uuid.uuid4().hex}{extension}"
    limit = settings.MAX_UPLOAD_BYTES
    digest = hashlib.sha256()
    size = 0
    with open(path, "wb") as handle:
        for chunk in upload.chunks():
            size += len(chunk)
            if size > limit:
                handle.close()
                _discard(path)
                raise ApiError("validation_failed",
                               f"File exceeds the maximum analysed size ({limit} bytes).")
            digest.update(chunk)
            handle.write(chunk)
    return {"path": path, "size": size, "sha256": digest.hexdigest()}


def _discard(path: Path) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not remove the staged analysis file")


def _document(user, staged: list, payload: Dict[str, Any]) -> Dict[str, Any]:
    now = utcnow()
    formats = {entry["format"] for entry in staged}
    combined = hashlib.sha256("".join(entry["sha256"] for entry in staged).encode())
    return {
        "file_name": staged[0]["name"],
        "files": [{"name": entry["name"], "format": entry["format"],
                   "size_bytes": int(entry["size"]), "sha256": entry["sha256"]} for entry in staged],
        "file_count": len(staged),
        "format": next(iter(formats)) if len(formats) == 1 else "mixed",
        "size_bytes": sum(int(entry["size"]) for entry in staged),
        "sha256": combined.hexdigest(),
        "uploaded_by": str(user.id),
        "uploader_role": user.role,
        "rows_analyzed": payload["rows_analyzed"],
        "column_count": payload["column_count"],
        "truncated": payload["truncated"],
        "summary": {
            "measures": payload["measures"],
            "dimensions": payload["dimensions"],
            "time_column": payload["time_column"],
            "target_year": payload["forecast"].get("target_year"),
            "forecast_columns": [entry["column"] for entry in payload["forecast"].get("columns", [])],
            "has_geo": bool(payload["geo"]),
            "column_types": [{"column": column["column"], "type": column["type"]}
                             for column in payload["columns"]],
        },
        "payload": payload,
        "created_at": now,
        "updated_at": now,
    }


def _target_year(request) -> int | None:
    raw = request.data.get("target_year") if isinstance(request.data, dict) else None
    if raw in (None, ""):
        return None
    try:
        return int(str(raw))
    except ValueError:
        raise ApiError("validation_failed", "The target year must be a whole year, for example 2027.")


def _visibility_criteria(user) -> Dict[str, Any]:
    if user.role in (ADMINISTRATOR, CLIMATE_ANALYST):
        return {}
    return {"uploaded_by": str(user.id)}


def _load(analysis_id: str) -> Dict[str, Any]:
    try:
        identifier = maybe_object_id(analysis_id)
    except Exception:
        raise ApiError("validation_failed", "Invalid analysis identifier.")
    document = file_analyses.find_one({"_id": identifier})
    if document is None:
        raise ApiError("not_found", "No such analysis.", status=404)
    return document


def _require_access(user, document: Dict[str, Any]) -> None:
    if user.role in (ADMINISTRATOR, CLIMATE_ANALYST):
        return
    if str(document.get("uploaded_by")) == str(user.id):
        return
    raise ApiError("permission_denied", "You do not have access to this analysis.", status=403)
