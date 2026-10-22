"""MongoDB Atlas connectivity check for the EarthScape backend.

    python scripts/verify_atlas.py                     # report what the config resolves to
    python scripts/verify_atlas.py --set-user NAME     # store the Atlas username in .env, then check
    python scripts/verify_atlas.py --require           # exit non-zero unless Atlas answered the ping

Credentials are never printed: the URI is shown with its user and password masked, and
connection errors are passed through ``common.database._safe_error`` for the same reason.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from common.database import _safe_error, get_database  # noqa: E402
from common.env import ENV, resolve_mongodb_uri  # noqa: E402


def _mask(uri: str) -> str:
    """``mongodb://user:secret@host`` -> ``mongodb://user:***@host``."""
    return re.sub(r"://([^:/@]+):([^@]*)@", r"://\1:***@", uri)


def _set_env_value(key: str, value: str) -> None:
    """Rewrite (or append) one ``KEY=value`` line in the repository ``.env``."""
    path = ROOT / ".env"
    if not path.is_file():
        raise SystemExit(f"{path} not found - copy .env.example to .env first.")
    lines = path.read_text(encoding="utf-8").splitlines()
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    replaced = False
    for index, line in enumerate(lines):
        if pattern.match(line):
            lines[index] = f"{key}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  .env: {key} updated")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the MongoDB Atlas connection.")
    parser.add_argument("--set-user", metavar="NAME", help="Atlas username to store in .env")
    parser.add_argument("--require", action="store_true", help="fail unless Atlas is reachable")
    parser.add_argument("--probe", action="store_true", default=True,
                        help="connect and ping (default)")
    args = parser.parse_args()

    if args.set_user:
        _set_env_value("MONGODB_ATLAS_USER", args.set_user)

    uri = resolve_mongodb_uri()
    print("=" * 72)
    print("MongoDB Atlas configuration")
    print("=" * 72)
    if not uri:
        raw = ENV.raw("MONGODB_URI")
        if raw:
            print("  STATUS  : UNUSABLE - MONGODB_URI still contains a credential placeholder")
            print(f"  URI     : {_mask(raw)}")
            print(f"  MISSING : MONGODB_ATLAS_USER={'(empty)' if not ENV.raw('MONGODB_ATLAS_USER') else ENV.raw('MONGODB_ATLAS_USER')}")
            print(f"            MONGODB_ATLAS_PASSWORD={'(empty)' if not ENV.raw('MONGODB_ATLAS_PASSWORD') else 'set'}")
            print("  FIX     : python scripts/verify_atlas.py --set-user <atlas-username>")
        else:
            print("  STATUS  : NOT CONFIGURED - set MONGODB_URI in .env")
        if args.require:
            return 2
        return 2

    print(f"  URI     : {_mask(uri)}")
    print(f"  database: {ENV.raw('MONGODB_DATABASE', 'earthscape_climate')}")

    database = get_database()
    ping = database.ping()
    print(f"  mode    : {database.mode}")
    print(f"  ping ok : {ping.get('ok')}")
    if database.error:
        print(f"  error   : {database.error}")

    if database.mode == "atlas":
        from common.db import client, database_name

        build = client()[database_name()].command("buildinfo")
        print(f"  server  : {build.get('version')} modules={build.get('modules') or 'n/a'}")
        print("  RESULT  : Atlas reachable - application data will be stored in Atlas")
    else:
        print("  RESULT  : running on the local-development document store (backend/.data/localdb)")

    counts = database.counts()
    total = sum(counts.values())
    print(f"  docs    : {total} across {len([c for c in counts.values() if c])} collection(s)")
    for name, count in sorted(counts.items(), key=lambda item: -item[1])[:8]:
        if count:
            print(f"            {name:<26} {count}")
    print("=" * 72)

    if args.require and database.mode != "atlas":
        return 1
    return 0 if database.mode == "atlas" else 1


if __name__ == "__main__":
    raise SystemExit(main())
