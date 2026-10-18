"""Portable keys for files stored under ``MEDIA_ROOT``.

Documents hold ``uploads/<id>.csv`` instead of ``C:\\Users\\…\\backend\\.data\\uploads\\<id>.csv``,
so a data directory copied to another machine (or mounted at a different path in a
container) still resolves its artefacts, and no developer home path is ever
published through the API.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Union

from django.conf import settings

__all__ = ["stored_key", "stored_path"]


def _root() -> Path:
    return Path(settings.MEDIA_ROOT).resolve()


def stored_key(path: Union[str, Path]) -> str:
    """Render a filesystem path as its storage key, relative to ``MEDIA_ROOT``."""
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(_root()).as_posix()
    except ValueError:
        return str(resolved)


def stored_path(value: Optional[Any]) -> Optional[Path]:
    """Resolve a stored key back to a filesystem path (existence not checked)."""
    if not value:
        return None
    path = Path(str(value))
    return path if path.is_absolute() else _root() / path
