#!/usr/bin/env python
"""Django management entry point (document database - no ORM migrations)."""
import os
import sys


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Django is not available in the active interpreter. Create the virtual "
            "environment and install backend/requirements.txt first."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
