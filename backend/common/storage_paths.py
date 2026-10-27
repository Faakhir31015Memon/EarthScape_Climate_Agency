"""Portable keys for files stored under MEDIA_ROOT.

Documents hold `uploads/<id>.csv` rather than an absolute machine path, so a data
directory copied or mounted elsewhere still resolves, and no developer home path
reaches the API.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Union

from django.conf import settings

__all__ = ["stored_key", "stored_path"]


def _root() -> Path:
    return Path(settings.MEDIA_ROOT).resolve()


def stored_key(path: Union[str, Path]) -> str:
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(_root()).as_posix()
    except ValueError:
        return str(resolved)


def stored_path(value: Optional[Any]) -> Optional[Path]:
    """Stored key to filesystem path. Does not check that the file exists."""
    if not value:
        return None
    path = Path(str(value))
    return path if path.is_absolute() else _root() / path
