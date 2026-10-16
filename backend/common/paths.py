"""Import-path wiring for the sibling big-data and ML packages.

`data-pipeline/` and `ml/` are standalone packages (they must stay runnable from a shell
without Django), so the backend adds them to `sys.path` instead of duplicating their code.
"""

from __future__ import annotations

import sys
from pathlib import Path

from common.env import REPO_ROOT

PIPELINE_ROOT = REPO_ROOT / "data-pipeline"
ML_ROOT = REPO_ROOT / "ml"

_ADDED: list[str] = []


def ensure_paths() -> dict[str, bool]:
    for path in (PIPELINE_ROOT, ML_ROOT):
        text = str(path)
        if path.is_dir() and text not in sys.path:
            sys.path.insert(0, text)
            _ADDED.append(text)
    return {
        "pipeline_root_present": PIPELINE_ROOT.is_dir(),
        "ml_root_present": ML_ROOT.is_dir(),
    }


def module_available(module_name: str) -> bool:
    try:
        __import__(module_name)
        return True
    except Exception:
        return False


ensure_paths()
