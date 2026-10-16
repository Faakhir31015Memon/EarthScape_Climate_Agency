"""Standalone configuration for the EarthScape data pipeline.

The pipeline is plain Python and CLI-runnable: it never imports Django settings.
It reads the same ``.env`` files at the repository root using the same layering
convention as ``backend/common/env.py`` (process environment wins over file
values; later files never override keys already set by earlier ones).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Callable

PIPELINE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PIPELINE_DIR.parent

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


def _parse_env_file(path: Path) -> dict:
    values: dict = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


class EnvFile:
    """Layered configuration: process env wins over file values (same as backend)."""

    def __init__(self, files: list) -> None:
        self._file_values: dict = {}
        self.loaded_files: list = []
        for path in files:
            loaded = _parse_env_file(path)
            if loaded:
                self._file_values.update({k: v for k, v in loaded.items() if k not in self._file_values})
                self.loaded_files.append(str(path))

    def raw(self, key: str, default: str = "") -> str:
        value = os.environ.get(key)
        if value is None or value == "":
            value = self._file_values.get(key, default)
        return (value or default).strip()

    def number(self, key: str, default: float) -> float:
        raw = self.raw(key)
        if raw == "":
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    def integer(self, key: str, default: int) -> int:
        return int(self.number(key, default))

    def flag(self, key: str, default: bool = False) -> bool:
        raw = self.raw(key)
        if raw == "":
            return default
        low = raw.lower()
        if low in _TRUE:
            return True
        if low in _FALSE:
            return False
        return default

    def items(self, key: str, default: str = "") -> list:
        raw = self.raw(key, default)
        return [part.strip() for part in re.split(r"[,;]", raw) if part.strip()]

    def exists(self, key: str) -> bool:
        return bool(self.raw(key))


ENV = EnvFile([REPO_ROOT / ".env", REPO_ROOT / "backend" / ".env"])


def _rooted(key: str, default_rel: str) -> Path:
    configured = ENV.raw(key)
    path = Path(configured) if configured else (REPO_ROOT / default_rel)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path


def datalake_root() -> Path:
    path = _rooted("DATA_LAKE_ROOT", "artifacts/datalake")
    path.mkdir(parents=True, exist_ok=True)
    return path


def artifacts_root() -> Path:
    path = _rooted("ARTIFACTS_ROOT", "artifacts")
    path.mkdir(parents=True, exist_ok=True)
    return path


def checkpoints_root() -> Path:
    path = artifacts_root() / "checkpoints"
    path.mkdir(parents=True, exist_ok=True)
    return path


def sample_data_root() -> Path:
    path = REPO_ROOT / "sample-data"
    path.mkdir(parents=True, exist_ok=True)
    return path


DATALAKE_LAYERS = ("raw", "validated", "processed", "analytics", "ml-features", "exports")


def layer_path(layer: str, *parts) -> Path:
    """Return (and create) a path inside the data-lake layout."""
    if layer not in DATALAKE_LAYERS:
        raise ValueError(f"unknown data-lake layer {layer!r}; expected one of {DATALAKE_LAYERS}")
    path = datalake_root() / layer
    for p in parts:
        path = path / str(p)
    path.mkdir(parents=True, exist_ok=True)
    return path


def partition_dir(layer: str, dataset: str, year: int, month: int) -> Path:
    """dataset=<name>/year=YYYY/month=MM partition inside the given layer."""
    return layer_path(layer, f"dataset={_slug(dataset)}", f"year={int(year):04d}", f"month={int(month):02d}")


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(name)).strip("_") or "unknown"


def parquet_available() -> bool:
    try:
        import pyarrow  # noqa: F401
        import pyarrow.parquet  # noqa: F401

        return True
    except Exception:
        return False


def write_tabular(rows: list, directory: Path, base_name: str,
                  columns: list = None, note: str = "") -> dict:
    """Write analytical output as Parquet when pyarrow exists, else gzipped CSV.

    ``rows`` is a list of dicts sharing ``columns`` (order enforced when given).
    Returns a manifest dict recording the engine that actually ran, so no stage
    silently pretends to have produced Parquet.
    """
    import csv
    import gzip
    import json as _json
    from datetime import datetime, timezone

    directory.mkdir(parents=True, exist_ok=True)
    used = None
    out_path = None
    cols = columns or _union_columns(rows)
    if parquet_available():
        import pyarrow as pa
        import pyarrow.parquet as pq

        data = {c: _coerce_column(rows, c) for c in cols}
        if not rows:
            table = pa.table({c: pa.array([], type=pa.string()) for c in cols})
        else:
            try:
                table = pa.Table.from_pylist([{c: data[c][i] for c in cols} for i in range(len(rows))])
            except Exception:
                table = pa.table({c: [str(v) if v is not None else None for v in data[c]] for c in cols})
        out_path = directory / f"{base_name}.parquet"
        pq.write_table(table, str(out_path))
        used = "parquet"
    else:
        out_path = directory / f"{base_name}.csv.gz"
        with gzip.open(out_path, "wt", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            writer.writeheader()
            for r in rows:
                writer.writerow({c: _scalar(r.get(c)) for c in cols})
        used = "csv.gz"

    meta = {
        "engine": used,
        "reason": note or (
            "pyarrow available" if used == "parquet"
            else "pyarrow unavailable at runtime; gzipped CSV fallback (schema identical)"
        ),
        "path": str(out_path),
        "rows": len(rows),
        "columns": cols,
        "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (directory / f"{base_name}.meta.json").write_text(_json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def _union_columns(rows: list) -> list:
    seen: list = []
    for r in rows:
        for k in r.keys():
            if k not in seen:
                seen.append(k)
    return seen


def _coerce_column(rows: list, col: str) -> list:
    return [_scalar(r.get(col)) for r in rows]


def _scalar(value: Any):
    """Reduce exotic scalars (numpy, Decimal) to JSON-native values."""
    if value is None:
        return None
    if isinstance(value, (bool, int, float, str)) and type(value).__module__ == "builtins":
        return value
    item = getattr(value, "item", None)
    if callable(item):
        try:
            native = item()
            if isinstance(native, (bool, int, float, str)):
                return native
        except Exception:
            pass
    if isinstance(value, (int, float, str)):
        return value
    import json as _json

    return _json.dumps(value, sort_keys=True, default=str)
