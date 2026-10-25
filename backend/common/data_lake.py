"""Data lake gateway: one logical layout, two physical backends.

EarthScape stores batch artefacts (raw uploads, validated observations, processed
Parquet, analytics aggregates, ML features, exports) in a layered data lake
partitioned as ``<layer>/dataset=<id>/year=<YYYY>/month=<MM>/region=<code>/``.

Two backends expose that layout:

``hdfs``
    A genuine WebHDFS client (the ``hdfs`` package) used whenever ``HDFS_ENDPOINT``
    is configured *and* the NameNode answers. Nothing here ever pretends HDFS ran:
    every operation reports the mode it actually used.

``local-data-lake``
    Used when no reachable NameNode is configured. Files live under
    ``DATA_LAKE_LOCAL_ROOT`` with the *same* directory layout and the *same*
    Parquet/JSON file formats, so every downstream path (MapReduce, Spark, exports)
    is backend-agnostic.

Design rules:

* no HDFS/local failure ever raises into a user response - callers receive
  structured ``{"ok": False, "error": ...}`` results instead;
* URIs written to MongoDB come from :meth:`DataLake.storage_reference`, so a
  document always records where the bytes really live;
* connection strings / credentials are never logged.
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional

from config import env

logger = logging.getLogger(__name__)

LAYERS = ("raw", "validated", "processed", "analytics", "ml-features", "exports")
DEFAULT_FORMATS = ("parquet", "jsonl", "csv")
BACKEND_DIR = Path(__file__).resolve().parents[1]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LakeResult:
    """Structured outcome of every lake mutation - never raises to callers."""

    __slots__ = ("ok", "mode", "path", "error", "bytes_written")

    def __init__(self, *, ok: bool, mode: str, path: Optional[str] = None,
                 error: Optional[str] = None, bytes_written: int = 0):
        self.ok = ok
        self.mode = mode
        self.path = path
        self.error = error
        self.bytes_written = bytes_written

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "mode": self.mode, "path": self.path,
                "error": self.error, "bytes_written": self.bytes_written}


def _partition_dirs(partition: Optional[Dict[str, Any]]) -> str:
    if not partition:
        return ""
    parts = []
    if partition.get("dataset_id") or partition.get("dataset"):
        parts.append(f"dataset={_slug(partition.get('dataset_id') or partition.get('dataset'))}")
    if partition.get("year"):
        parts.append(f"year={int(partition['year'])}")
    if partition.get("month"):
        parts.append(f"month={int(partition['month']):02d}")
    if partition.get("region_code") or partition.get("region"):
        parts.append(f"region={_slug(partition.get('region_code') or partition.get('region'))}")
    return "/".join(parts)


def _slug(value: Any) -> str:
    text = str(value or "").strip()
    safe = "".join(ch if (ch.isalnum() or ch in "-._") else "_" for ch in text)
    return (safe or "unknown")[:120]


def _safe_join(root: Path, relative: str) -> Optional[Path]:
    """Join and refuse any path that escapes the lake root."""
    candidate = (root / PurePosixPath(relative).as_posix()).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def sha256_of_local_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class DataLake:
    """Backend-agnostic access to the EarthScape data lake."""

    def __init__(self, *, force_backend: Optional[str] = None):
        self.endpoint = (env.get_raw("HDFS_ENDPOINT") or "").strip()
        self.hdfs_root = (env.get_str("HDFS_ROOT", "/user/earthscape") or "/user/earthscape").strip("/")
        self.hdfs_user = env.get_str("HDFS_USER", "hadoop") or None
        self.local_root = Path(env.get_str("DATA_LAKE_LOCAL_ROOT",
                                           str(BACKEND_DIR / ".data-lake")))
        preference = (force_backend or env.get_str("DATA_LAKE_BACKEND", "auto")).strip().lower()
        self._client = None
        self._client_lock = threading.Lock()
        self._mode: Optional[str] = None
        self._mode_error: Optional[str] = None
        if preference == "local":
            self._mode = "local"
        elif preference == "hdfs":
            self._mode = "hdfs" if self.endpoint else "local"
            if not self.endpoint:
                self._mode_error = "DATA_LAKE_BACKEND=hdfs but HDFS_ENDPOINT is not configured"
        else:  # auto: use HDFS only when it actually answers a probe.
            self._mode = None

    # ------------------------------------------------------------------- mode
    @property
    def mode(self) -> str:
        if self._mode is None:
            if self.endpoint and self._probe_hdfs():
                self._mode = "hdfs"
            else:
                self._mode = "local"
                if self.endpoint:
                    self._mode_error = "HDFS_ENDPOINT is configured but the NameNode did not answer; " \
                                       "using the local data-lake directory instead"
        return self._mode

    @property
    def mode_label(self) -> str:
        return "hdfs" if self.mode == "hdfs" else "local-data-lake"

    def _probe_hdfs(self) -> bool:
        client = self._get_client()
        if client is None:
            return False
        try:
            client.list(f"/{self.hdfs_root}")
            return True
        except Exception as exc:  # noqa: BLE001 - probe must never raise
            logger.warning("HDFS probe failed: %s", str(exc)[:200])
            return False

    def _get_client(self):
        if not self.endpoint:
            return None
        with self._client_lock:
            if self._client is None:
                try:
                    from hdfs.client import InsecureClient

                    self._client = InsecureClient(self.endpoint, user=self.hdfs_user,
                                                  timeout=10, root="/")
                except Exception as exc:  # noqa: BLE001
                    self._mode_error = f"HDFS client unavailable: {str(exc)[:200]}"
                    return None
            return self._client

    # ------------------------------------------------------------------ paths
    def logical_path(self, logical_key: str, *, layer: Optional[str] = None,
                     partition: Optional[Dict[str, Any]] = None) -> str:
        """Normalise a logical key to ``<layer>/<partition dirs>/<file>`` under the root."""
        key = str(logical_key or "").strip("/")
        if layer and not key.startswith(f"{layer}/"):
            dirs = _partition_dirs(partition)
            key = f"{layer}/{dirs + '/' if dirs else ''}{key}"
        return key

    def storage_reference(self, logical_key: str, *, layer: Optional[str] = None,
                          partition: Optional[Dict[str, Any]] = None) -> str:
        """The URI that gets recorded - reflects the real backend, portably.

        ``lake://<layer>/<key>`` resolves against this deployment's lake root, so
        the same recorded reference works on a second machine or in a container;
        a ``file:///C:\\Users\\…`` URI would not.
        """
        key = self.logical_path(logical_key, layer=layer, partition=partition)
        if self.mode == "hdfs":
            host = self.endpoint.split("://")[-1].rstrip("/")
            return f"hdfs://{host}{'' if key.startswith(self.hdfs_root) else '/' + self.hdfs_root}/{key}"
        return f"lake://{PurePosixPath(key).as_posix()}"

    def portable_reference(self, path: Any) -> str:
        """Render a file this process read as provenance that survives a move.

        A job summary that records ``C:\\Users\\…\\.data-lake\\validated\\…`` as its
        input is meaningless on the next machine, so a lake file becomes
        ``lake://<key>`` and a repository file becomes its repo-relative key.
        Anything outside both is reduced to its file name.
        """
        text = str(path)
        if "://" in text:
            return text
        try:
            resolved = Path(text).resolve()
        except OSError:
            return PurePosixPath(Path(text).name).as_posix()
        roots = ((self.local_root, "lake://"), (BACKEND_DIR, ""))
        for root, prefix in roots:
            try:
                relative = resolved.relative_to(Path(root).resolve())
            except ValueError:
                continue
            key = relative.as_posix()
            return f"{prefix}{key}" if prefix else key
        return PurePosixPath(resolved.name).as_posix()

    def local_path(self, key: str) -> Path:
        return self.local_root / PurePosixPath(key).as_posix()

    def hdfs_path(self, key: str) -> str:
        return f"/{self.hdfs_root}/{key}" if not key.startswith(f"/{self.hdfs_root}") \
            else "/" + key

    # ---------------------------------------------------------------- layout
    def ensure_layout(self) -> Dict[str, Any]:
        if self.mode == "hdfs":
            client = self._get_client()
            created, errors = [], []
            for layer in LAYERS + ("_manifests",):
                path = self.hdfs_path(layer + "/.lake-layout")
                try:
                    client.write(path, data=b"", overwrite=True)
                    created.append(layer)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{layer}: {str(exc)[:160]}")
            return {"ok": not errors, "mode": self.mode_label, "layers": list(LAYERS),
                    "errors": errors}
        for layer in LAYERS + ("_manifests",):
            (self.local_root / layer).mkdir(parents=True, exist_ok=True)
        return {"ok": True, "mode": self.mode_label, "layers": list(LAYERS),
                "root": str(self.local_root), "errors": []}

    # ---------------------------------------------------------------- writes
    def upload(self, local_path: str, logical_key: str, *, layer: str,
               partition: Optional[Dict[str, Any]] = None,
               overwrite: bool = True) -> LakeResult:
        source = Path(local_path)
        key = self.logical_path(logical_key, layer=layer, partition=partition)
        if not source.is_file():
            return LakeResult(ok=False, mode=self.mode_label, path=key,
                              error=f"local source does not exist: {source.name}")
        size = source.stat().st_size
        if self.mode == "hdfs":
            client = self._get_client()
            if client is None:
                return LakeResult(ok=False, mode=self.mode_label, path=key,
                                  error=self._mode_error or "HDFS client unavailable")
            try:
                client.upload(self.hdfs_path(key), str(source), overwrite=overwrite,
                              chunk_size=128 * 1024 * 1024)
                return LakeResult(ok=True, mode=self.mode_label, path=key, bytes_written=size)
            except Exception as exc:  # noqa: BLE001
                return LakeResult(ok=False, mode=self.mode_label, path=key,
                                  error=str(exc)[:300])
        target = self.local_path(key)
        if target is None:
            return LakeResult(ok=False, mode=self.mode_label, path=key, error="unsafe path")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            return LakeResult(ok=True, mode=self.mode_label, path=key, bytes_written=size)
        except OSError as exc:
            return LakeResult(ok=False, mode=self.mode_label, path=key,
                              error=str(exc)[:300])

    def write_text(self, text: str, logical_key: str, *, layer: str,
                   partition: Optional[Dict[str, Any]] = None) -> LakeResult:
        key = self.logical_path(logical_key, layer=layer, partition=partition)
        payload = text.encode("utf-8")
        if self.mode == "hdfs":
            client = self._get_client()
            if client is None:
                return LakeResult(ok=False, mode=self.mode_label, path=key,
                                  error=self._mode_error or "HDFS client unavailable")
            try:
                client.write(self.hdfs_path(key), data=io_bytes(payload), overwrite=True)
                return LakeResult(ok=True, mode=self.mode_label, path=key,
                                  bytes_written=len(payload))
            except Exception as exc:  # noqa: BLE001
                return LakeResult(ok=False, mode=self.mode_label, path=key,
                                  error=str(exc)[:300])
        target = self.local_path(key)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            return LakeResult(ok=True, mode=self.mode_label, path=key,
                              bytes_written=len(payload))
        except OSError as exc:
            return LakeResult(ok=False, mode=self.mode_label, path=key, error=str(exc)[:300])

    # ----------------------------------------------------------------- reads
    def download(self, logical_key: str, local_path: str, *, layer: Optional[str] = None) -> LakeResult:
        key = self.logical_path(logical_key, layer=layer)
        target = Path(local_path)
        if self.mode == "hdfs":
            client = self._get_client()
            if client is None:
                return LakeResult(ok=False, mode=self.mode_label, path=key,
                                  error=self._mode_error or "HDFS client unavailable")
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                client.download(self.hdfs_path(key), str(target), overwrite=True)
                return LakeResult(ok=True, mode=self.mode_label, path=key,
                                  bytes_written=target.stat().st_size if target.exists() else 0)
            except Exception as exc:  # noqa: BLE001
                return LakeResult(ok=False, mode=self.mode_label, path=key,
                                  error=str(exc)[:300])
        source = self.local_path(key)
        try:
            if not source.is_file():
                return LakeResult(ok=False, mode=self.mode_label, path=key,
                                  error="object does not exist in the local data lake")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            return LakeResult(ok=True, mode=self.mode_label, path=key,
                              bytes_written=target.stat().st_size)
        except OSError as exc:
            return LakeResult(ok=False, mode=self.mode_label, path=key, error=str(exc)[:300])

    def read_text(self, logical_key: str, *, layer: Optional[str] = None) -> Dict[str, Any]:
        key = self.logical_path(logical_key, layer=layer)
        if self.mode == "hdfs":
            client = self._get_client()
            if client is None:
                return {"ok": False, "mode": self.mode_label, "error": "HDFS unavailable"}
            try:
                content = client.read(self.hdfs_path(key))
                return {"ok": content is not None, "mode": self.mode_label, "path": key,
                        "content": content.decode("utf-8", "replace") if content else ""}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "mode": self.mode_label, "path": key,
                        "error": str(exc)[:300]}
        source = self.local_path(key)
        if not source.is_file():
            return {"ok": False, "mode": self.mode_label, "path": key, "error": "not found"}
        try:
            return {"ok": True, "mode": self.mode_label, "path": key,
                    "content": source.read_text(encoding="utf-8")}
        except OSError as exc:
            return {"ok": False, "mode": self.mode_label, "path": key, "error": str(exc)[:200]}

    def list_partition(self, layer: str, partition: Optional[Dict[str, Any]] = None,
                      *, recursive: bool = False) -> Dict[str, Any]:
        prefix = self.logical_path(_partition_dirs(partition), layer=layer)
        if self.mode == "hdfs":
            client = self._get_client()
            if client is None:
                return {"ok": False, "mode": self.mode_label, "entries": [],
                        "error": self._mode_error or "HDFS unavailable"}
            try:
                listing = client.list(self.hdfs_path(prefix)) or []
                entries = [f"{prefix}/{name}" for name in listing]
                return {"ok": True, "mode": self.mode_label, "prefix": prefix,
                        "entries": entries}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "mode": self.mode_label, "prefix": prefix,
                        "entries": [], "error": str(exc)[:300]}
        base = self.local_path(prefix)
        if not base.is_dir():
            return {"ok": True, "mode": self.mode_label, "prefix": prefix, "entries": []}
        entries: List[str] = []
        walker = base.rglob("*") if recursive else base.iterdir()
        for item in walker:
            if item.is_file():
                entries.append(str(item.relative_to(self.local_root)).replace("\\", "/"))
        return {"ok": True, "mode": self.mode_label, "prefix": prefix,
                "entries": sorted(entries)}

    def delete_prefix(self, layer: str, partition: Optional[Dict[str, Any]] = None,
                      *, logical_key: Optional[str] = None) -> Dict[str, Any]:
        prefix = self.logical_path(logical_key or _partition_dirs(partition), layer=layer)
        if self.mode == "hdfs":
            client = self._get_client()
            if client is None:
                return {"ok": False, "mode": self.mode_label, "error": "HDFS unavailable"}
            try:
                client.delete(self.hdfs_path(prefix), recursive=True, skip_missing=True)
                return {"ok": True, "mode": self.mode_label, "deleted_prefix": prefix}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "mode": self.mode_label, "deleted_prefix": prefix,
                        "error": str(exc)[:300]}
        target = self.local_path(prefix)
        try:
            if target.is_dir():
                shutil.rmtree(target)
                return {"ok": True, "mode": self.mode_label, "deleted_prefix": prefix}
            if target.is_file():
                target.unlink()
                return {"ok": True, "mode": self.mode_label, "deleted_prefix": prefix}
            return {"ok": True, "mode": self.mode_label, "deleted_prefix": prefix,
                    "note": "prefix did not exist"}
        except OSError as exc:
            return {"ok": False, "mode": self.mode_label, "error": str(exc)[:200]}

    # --------------------------------------------------------------- datasets
    def read_dataset(self, layer: str, partitions: Optional[Dict[str, Any]] = None,
                     format: str = "parquet"):
        """Load a lake layer/partition set into pandas.

        ``partitions`` maps partition columns to a value or list of values, e.g.
        ``{"dataset_id": "abc", "year": [2023, 2024]}``.
        """
        import pandas as pd

        frames: List[Any] = []
        for local_file in self.materialise_files(layer, partitions, format=format):
            path = Path(local_file)
            try:
                if format == "parquet":
                    frames.append(pd.read_parquet(path))
                elif format == "jsonl":
                    frames.append(pd.read_json(path, lines=True))
                else:
                    frames.append(pd.read_csv(path))
            except Exception as exc:  # noqa: BLE001 - one bad shard must not kill the read
                logger.warning("Could not read lake shard %s: %s", path.name, str(exc)[:200])
            finally:
                if str(path).startswith(str(self._scratch_dir())):
                    try:
                        path.unlink()
                    except OSError:
                        pass
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)

    def materialise_files(self, layer: str, partitions: Optional[Dict[str, Any]] = None,
                          *, format: str = "parquet",
                          limit: int = 2000) -> List[str]:
        """Return readable local paths for a partition selection (downloads from HDFS)."""
        keys = self.select_keys(layer, partitions, format=format, limit=limit)
        out: List[str] = []
        for key in keys:
            if self.mode == "hdfs":
                scratch = self._scratch_dir() / key.replace("/", "_")
                result = self.download(key, str(scratch), layer=None)
                if result.ok:
                    out.append(str(scratch))
            else:
                path = self.local_path(key)
                if path.is_file():
                    out.append(str(path))
        return out

    def select_keys(self, layer: str, partitions: Optional[Dict[str, Any]] = None,
                    *, format: str = "parquet", limit: int = 2000) -> List[str]:
        prefix = f"{layer}/"
        listing = self.list_partition(layer, None, recursive=True)
        keys = [entry for entry in listing.get("entries", []) if entry.startswith(prefix)]
        if partitions:
            def matches(key: str) -> bool:
                for column, value in (partitions or {}).items():
                    if column == "dataset_id":
                        column = "dataset"
                    wanted = value if isinstance(value, (list, tuple, set)) else [value]
                    tokens = {f"{column}={str(w)}" for w in wanted}
                    if not any(token in key.split("/") for token in tokens):
                        return False
                return True
            keys = [key for key in keys if matches(key)]
        if format:
            keys = [key for key in keys if key.endswith(f".{format}") or key.endswith(f".{format}.tmp")]
        return keys[:limit]

    def _scratch_dir(self) -> Path:
        scratch = self.local_root / "_scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        return scratch

    # -------------------------------------------------------------- manifests
    def write_manifest(self, dataset_id: str, layer: str, files: Iterable[str],
                       *, schema_version: int, row_counts: Optional[Dict[str, int]] = None,
                       checksums: Optional[Dict[str, str]] = None,
                       extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        manifest = {
            "dataset_id": str(dataset_id),
            "layer": layer,
            "storage_backend": self.mode_label,
            "created_at": utcnow().isoformat(),
            "schema_version": schema_version,
            "files": [self.storage_reference(f) if "://" not in f else f for f in files],
            "row_counts": dict(row_counts or {}),
            "checksums": dict(checksums or {}),
            "extra": dict(extra or {}),
        }
        key = f"_manifests/{_slug(dataset_id)}/{layer}-{utcnow().strftime('%Y%m%dT%H%M%SZ')}.json"
        result = self.write_text(json.dumps(manifest, indent=2, default=str), key,
                                 layer="_manifests")
        manifest["ok"] = result.ok
        manifest["manifest_key"] = key
        manifest["manifest_uri"] = self.storage_reference(key) if result.ok else None
        if not result.ok:
            manifest["error"] = result.error
        return manifest

    # ----------------------------------------------------------------- health
    def health(self) -> Dict[str, Any]:
        mode = self.mode
        info: Dict[str, Any] = {"reachable": False, "mode": self.mode_label,
                                "configured": bool(self.endpoint), "root": None,
                                "free_bytes_or_null": None, "error": self._mode_error}
        if mode == "hdfs":
            host = self.endpoint.split("://")[-1].split("@")[-1]
            info["root"] = f"{host}/{self.hdfs_root}"
            try:
                client = self._get_client()
                contents = client.contents(self.hdfs_path(""))
                info["reachable"] = isinstance(contents, dict)
            except Exception as exc:  # noqa: BLE001
                info["error"] = str(exc)[:250]
        else:
            info["root"] = str(self.local_root)
            try:
                self.local_root.mkdir(parents=True, exist_ok=True)
                usage = shutil.disk_usage(self.local_root)
                info["reachable"] = self.local_root.is_dir()
                info["free_bytes_or_null"] = int(usage.free)
            except OSError as exc:
                info["error"] = str(exc)[:250]
        return info


def io_bytes(payload: bytes):
    import io

    return io.BytesIO(payload)


_LAKE: Optional[DataLake] = None
_LAKE_LOCK = threading.Lock()


def get_data_lake() -> DataLake:
    global _LAKE
    if _LAKE is None:
        with _LAKE_LOCK:
            if _LAKE is None:
                _LAKE = DataLake()
    return _LAKE
