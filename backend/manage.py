#!/usr/bin/env python
"""Django management entry point (MongoDB-backed, no relational ORM)."""

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "Django is not importable. Create a virtual environment and run "
            "`pip install -r requirements.txt` inside backend/."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
