"""Data-lake access: local filesystem layout by default, HDFS (WebHDFS) when configured.

The layout is identical in both engines - `raw/`, `validated/`, `processed/`,
`analytics/`, `ml-features/`, `exports/` - so processing jobs address the same logical
paths either way. Availability is always reported honestly: this module never claims an
HDFS write succeeded unless the NameNode answered.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from common.env import ENV, storage_root
from common.logging_config import get_logger

logger = get_logger("earthscape.datalake")

LAYERS = ("raw", "validated", "processed", "analytics", "ml-features", "exports")
PARTITION_LAYERS = {"processed", "analytics", "ml-features"}


def slug(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in (value or "").strip())
    return cleaned.strip("._")[:64] or "dataset"


def engine() -> str:
    return "webhdfs" if (ENV.exists("HDFS_WEBHDFS_URL") and ENV.raw("DATA_LAKE_ENGINE", "local") == "webhdfs") else "local"


def local_layer(layer: str) -> Path:
    root = storage_root()
    path = root / layer
    path.mkdir(parents=True, exist_ok=True)
    return path


def partition_path(layer: str, dataset: str, *, year: int | None = None,
                   month: int | None = None, region: str | None = None) -> str:
    parts = [layer, f"dataset={slug(dataset)}"]
    if year:
        parts.append(f"year={year}")
    if month:
        parts.append(f"month={month:02d}")
    if region:
        parts.append(f"region={slug(region)}")
    return "/".join(parts)


def _webhdfs_url(object_path: str, op: str, **params: Any) -> str:
    base = ENV.raw("HDFS_WEBHDFS_URL").rstrip("/")
    root = ENV.raw("HDFS_ROOT", "/earthscape").rstrip("/")
    url = f"{base}{root}{object_path}?op={op}"
    for key, value in params.items():
        if value is not None:
            url += f"&{key}={value}"
    return url


def _webhdfs_headers() -> dict[str, str]:
    headers = {}
    user = ENV.raw("HADOOP_USER")
    if user:
        headers["user.name"] = user
    return headers


class HdfsUnavailable(Exception):
    """Raised when the configured NameNode cannot be reached."""


def hdfs_request(op: str, object_path: str, *, method: str = "GET", data: bytes | None = None,
                 params: dict | None = None, timeout: int = 30):
    import requests

    url = _webhdfs_url(object_path, op, **(params or {}))
    try:
        response = requests.request(method, url, data=data, headers=_webhdfs_headers(),
                                    timeout=timeout, allow_redirects=True)
    except requests.RequestException as exc:
        raise HdfsUnavailable(f"WebHDFS {op} failed: {type(exc).__name__}") from exc
    if response.status_code in (200, 201, 204):
        return response
    raise HdfsUnavailable(f"WebHDFS {op} returned HTTP {response.status_code}")


def status() -> dict[str, Any]:
    """Report which engine is live right now, verified rather than assumed."""
    active = engine()
    if active == "webhdfs":
        try:
            response = hdfs_request("GETFILESTATUS", "/", params={"status": "json"}, timeout=8)
            file_info = response.json().get("FileStatus", {})
            return {"engine": "webhdfs", "reachable": True, "root": ENV.raw("HDFS_ROOT", "/earthscape"),
                   "path_type": file_info.get("type"),
                   "detail": "NameNode answered GETFILESTATUS"}
        except HdfsUnavailable as exc:
            return {"engine": "webhdfs", "configured_engine": active, "reachable": False,
                    "detail": str(exc)[:200],
                    "fallback": "local", "local_root": str(storage_root())}
    root = storage_root()
    return {"engine": "local", "reachable": root.is_dir(), "root": str(root),
            "layers_present": {layer: (root / layer).is_dir() for layer in LAYERS}}


def ensure_layout() -> dict[str, bool]:
    root = storage_root()
    created: dict[str, bool] = {}
    for layer in LAYERS:
        path = root / layer
        existed = path.is_dir()
        path.mkdir(parents=True, exist_ok=True)
        created[layer] = not existed
    return {"created": created, "root": str(root)}


def write_local(relative_path: str, content: bytes | str) -> dict[str, Any]:
    root = storage_root()
    target = (root / relative_path).resolve()
    if root.resolve() not in target.parents and target != root.resolve():
        raise ValueError("Refusing to write outside the data lake root.")
    target.parent.mkdir(parents=True, exist_ok=True)
    data = content.encode("utf-8") if isinstance(content, str) else content
    target.write_bytes(data)
    return {"path": str(target), "relative_path": relative_path, "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(), "engine": "local",
            "written_at": datetime.now(timezone.utc)}


def read_local(relative_path: str) -> bytes:
    root = storage_root()
    target = (root / relative_path).resolve()
    if root.resolve() not in target.parents:
        raise ValueError("Refusing to read outside the data lake root.")
    return target.read_bytes()


def write(relative_path: str, content: bytes | str) -> dict[str, Any]:
    """Write through the active engine, falling back to local when HDFS is down."""
    if engine() == "webhdfs":
        try:
            hdfs_request("CREATE", f"/{relative_path.lstrip('/')}", method="PUT",
                         params={"overwrite": "true", "permission": "644"},
                         data=content.encode("utf-8") if isinstance(content, str) else content)
            return {"path": f"hdfs://{relative_path}", "relative_path": relative_path,
                    "engine": "webhdfs", "written_at": datetime.now(timezone.utc),
                    "bytes": len(content)}
        except HdfsUnavailable as exc:
            logger.warning("HDFS write failed, using local mirror: %s", exc)
            result = write_local(relative_path, content)
            result["hdfs_error"] = str(exc)[:200]
            result["engine_attempted"] = "webhdfs"
            return result
    return write_local(relative_path, content)


def publish_raw_file(source: Path, *, dataset_name: str, original_name: str) -> dict[str, Any]:
    """Copy an uploaded raw file into `raw/dataset=.../` with a provenance manifest."""
    relative_dir = f"raw/{slug(dataset_name)}"
    target_relative = f"{relative_dir}/{slug(original_name)}"
    if engine() == "webhdfs":
        try:
            hdfs_request("CREATE", f"/{target_relative}", method="PUT",
                         params={"overwrite": "true", "permission": "644"},
                         data=source.read_bytes())
            uri = f"hdfs://{target_relative}"
            engine_used = "webhdfs"
        except HdfsUnavailable as exc:
            moved = write_local(target_relative, source.read_bytes())
            uri = moved["path"]
            engine_used = "local"
            logger.warning("raw publish fell back to local: %s", exc)
    else:
        moved = write_local(target_relative, source.read_bytes())
        uri = moved["path"]
        engine_used = "local"

    manifest = {
        "dataset": dataset_name,
        "original_name": original_name,
        "storage_uri": uri,
        "engine": engine_used,
        "size_bytes": source.stat().st_size,
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "published_at": datetime.now(timezone.utc).isoformat(),
    }
    write(f"{relative_dir}/_manifest.json", json.dumps(manifest, indent=2))
    return manifest


def list_layer(layer: str, *, dataset: str = "") -> list[dict[str, Any]]:
    root = storage_root()
    base = root / layer / (f"dataset={slug(dataset)}" if dataset else "")
    if not base.is_dir():
        return []
    entries: list[dict[str, Any]] = []
    for path in sorted(base.rglob("*")):
        if path.is_file() and not path.name.startswith("_"):
            stat = path.stat()
            entries.append({
                "relative_path": str(path.relative_to(root)).replace("\\", "/"),
                "name": path.name,
                "bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                "partition": _parse_partition(path.relative_to(base)),
            })
    return entries[:500]


def _parse_partition(relative: Path) -> dict[str, str]:
    parts: dict[str, str] = {}
    for segment in relative.parts[:-1]:
        if "=" in segment:
            key, _, value = segment.partition("=")
            parts[key] = value
    return parts


def write_jsonl(object_path: str, records: Iterable[dict]) -> dict[str, Any]:
    lines = "\n".join(json.dumps(record, default=str) for record in records)
    return write(object_path, (lines + "\n") if lines else "")


def dataset_uri_for(dataset_name: str, layer: str, filename: str, *, year: int | None = None,
                    month: int | None = None, region: str | None = None) -> str:
    return f"{partition_path(layer, dataset_name, year=year, month=month, region=region)}/{filename}"
