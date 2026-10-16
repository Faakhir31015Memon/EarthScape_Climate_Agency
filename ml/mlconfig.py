"""Configuration for the ml package, mirroring backend/common/env.py semantics.

The backend's ``common.env`` layers process environment over a repo ``.env`` file
and resolves relative paths against the repository root. This module applies the
exact same rules for the keys the ML layer consumes, so both sides agree on where
the data lake and model artifacts live without the ml package importing Django.

Keys:
- DATA_LAKE_ROOT        default: <repo>/artifacts/datalake
- MODEL_ARTIFACT_ROOT   default: <repo>/artifacts/models
- MIN_TRAINING_ROWS     default: 240
- MIN_BASELINE_SAMPLE   default: 30   (anomaly climatology minimum)
- GAP_FILL_LIMIT        default: 0    (0 = missing stays missing)
"""

from __future__ import annotations

import os
import re
from pathlib import Path

ML_DIR = Path(__file__).resolve().parent
REPO_ROOT = ML_DIR.parent

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
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
    """Process env wins over file values (same contract as backend.common.env)."""

    def __init__(self, files: list[Path]) -> None:
        self._file_values: dict[str, str] = {}
        for path in files:
            loaded = _parse_env_file(path)
            if loaded:
                self._file_values.update({k: v for k, v in loaded.items() if k not in self._file_values})

    def raw(self, key: str, default: str = "") -> str:
        value = os.environ.get(key)
        if value is None or value == "":
            value = self._file_values.get(key, default)
        return (value or default).strip()

    def integer(self, key: str, default: int) -> int:
        raw = self.raw(key)
        if raw == "":
            return default
        try:
            return int(float(raw))
        except ValueError:
            return default

    def number(self, key: str, default: float) -> float:
        raw = self.raw(key)
        if raw == "":
            return default
        try:
            return float(raw)
        except ValueError:
            return default


ENV = EnvFile([REPO_ROOT / ".env", REPO_ROOT / "backend" / ".env"])


def _resolved_dir(key: str, default: Path, create: bool = True) -> Path:
    configured = ENV.raw(key, str(default))
    path = Path(configured)
    if not path.is_absolute():
        path = REPO_ROOT / path
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def storage_root() -> Path:
    """Data lake root: <repo>/artifacts/datalake unless DATA_LAKE_ROOT is set."""
    return _resolved_dir("DATA_LAKE_ROOT", REPO_ROOT / "artifacts" / "datalake")


def model_artifact_root() -> Path:
    """Model artifacts: <repo>/artifacts/models unless MODEL_ARTIFACT_ROOT is set."""
    return _resolved_dir("MODEL_ARTIFACT_ROOT", REPO_ROOT / "artifacts" / "models")


def sample_data_root() -> Path:
    return REPO_ROOT / "sample-data"


def min_training_rows() -> int:
    return ENV.integer("MIN_TRAINING_ROWS", 240)


def min_baseline_sample() -> int:
    return ENV.integer("MIN_BASELINE_SAMPLE", 30)


def gap_fill_limit() -> int:
    return ENV.integer("GAP_FILL_LIMIT", 0)
