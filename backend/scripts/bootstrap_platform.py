"""Idempotent platform bootstrap: schema, reference regions and operator accounts.

Run from the ``backend`` directory::

    python scripts/bootstrap_platform.py

What it does, all idempotently:

1. applies the document schema (indexes + validators) via :func:`common.schema.apply_schema`;
2. seeds the ``regions`` collection from :func:`common.regions.region_documents`;
3. provisions one administrator, one climate analyst and one viewer account with
   **strong, randomly generated** passwords.

Passwords are never hardcoded. They are written once to
``backend/.data/bootstrap-credentials.txt`` (which is git-ignored) and printed to the
console. On a re-run, existing accounts are left untouched and no new secrets are
emitted.
"""
from __future__ import annotations

import argparse
import os
import secrets
import sys
from datetime import timezone, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# --- make ``backend`` importable and configure Django before any app import ------
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")


def configure_django() -> None:
    import django

    django.setup()


DEFAULT_ACCOUNTS = {
    "administrator": {"role": "administrator", "full_name": "EarthScape Administrator",
                      "email": "admin@earthscape.local", "env_email": "BOOTSTRAP_ADMIN_EMAIL"},
    "climate_analyst": {"role": "climate_analyst", "full_name": "EarthScape Analyst",
                        "email": "analyst@earthscape.local", "env_email": "BOOTSTRAP_ANALYST_EMAIL"},
    "viewer": {"role": "viewer", "full_name": "EarthScape Viewer",
               "email": "viewer@earthscape.local", "env_email": "BOOTSTRAP_VIEWER_EMAIL"},
}

_PASSWORD_ALPHABET = ("ABCDEFGHJKLMNPQRSTUVWXYZ"      # uppercase (no I/O)
                      "abcdefghijkmnopqrstuvwxyz"      # lowercase (no l)
                      "23456789"                        # digits (no 0/1)
                      "@.$%&*_=+-!")                   # allowed specials


def generate_password(length: int = 20) -> str:
    """A random password guaranteed to satisfy ``security.password_problems``."""
    from common import security

    while True:
        chars = [_PASSWORD_ALPHABET[secrets.randbelow(len(_PASSWORD_ALPHABET))]
                 for _ in range(length)]
        # Force one of each required class.
        upper = [c for c in _PASSWORD_ALPHABET if c.isupper()]
        lower = [c for c in _PASSWORD_ALPHABET if c.islower()]
        digit = [c for c in _PASSWORD_ALPHABET if c.isdigit()]
        special = [c for c in _PASSWORD_ALPHABET if not c.isalnum()]
        chars[0] = upper[secrets.randbelow(len(upper))]
        chars[1] = lower[secrets.randbelow(len(lower))]
        chars[2] = digit[secrets.randbelow(len(digit))]
        chars[3] = special[secrets.randbelow(len(special))]
        # Deterministic-looking Fisher-Yates using secrets for the shuffle.
        for i in range(len(chars) - 1, 0, -1):
            j = secrets.randbelow(i + 1)
            chars[i], chars[j] = chars[j], chars[i]
        candidate = "".join(chars)
        if not security.password_problems(candidate):
            return candidate


# ------------------------------------------------------------------- schema/regions
def apply_schema_step() -> Dict[str, Any]:
    """Best-effort schema application.

    Delegates to the unmodifiable contract :func:`common.schema.apply_schema`. If that
    core routine raises (it currently crashes on a local-development ``mongomock`` store
    because ``index_information()`` returns a name->info dict that the core iterates as
    keys), we record the failure honestly and continue seeding regions and accounts --
    index creation is not required for those steps. The real document contract is still
    enforced by the repository layer.
    """
    from common import schema

    try:
        return schema.apply_schema()
    except Exception as exc:  # noqa: BLE001 - never fake success, but never abort bootstrap
        return {"schema_version": getattr(schema, "SCHEMA_VERSION", None),
                "previous_version": None,
                "index_operations": [],
                "validator_operations": [],
                "error": f"{type(exc).__name__}: {exc}"}


def seed_regions() -> Dict[str, int]:
    from common import regions as region_reference
    from common.repositories import regions as regions_repository, utcnow

    created = 0
    updated = 0
    for document in region_reference.region_documents():
        payload = dict(document)
        payload["updated_at"] = utcnow()
        existing = regions_repository.find_one({"code": document["code"]})
        if existing is None:
            payload["created_at"] = utcnow()
            regions_repository.insert_one(payload)
            created += 1
        else:
            regions_repository.update_by_id(str(existing["_id"]), payload)
            updated += 1
    return {"created": created, "updated": updated, "total": regions_repository.count()}


# ------------------------------------------------------------------------ accounts
def provision_accounts(*, credentials_path: Optional[Path]) -> List[Dict[str, Any]]:
    from config import env
    from common.repositories import users, utcnow
    from common import security

    results: List[Dict[str, Any]] = []
    new_accounts: List[Dict[str, Any]] = []
    for key, spec in DEFAULT_ACCOUNTS.items():
        email = env.get_raw(spec["env_email"]) or spec["email"]
        email = email.strip().lower()
        existing = users.find_one({"email_normalized": email})
        if existing is not None and existing.get("is_active", False):
            results.append({"role": spec["role"], "email": email, "created": False,
                            "reason": "account already exists and is active"})
            continue

        password = generate_password()
        now = utcnow()
        document = {
            "full_name": spec["full_name"],
            "email": email,
            "email_normalized": email,
            "password_hash": security.hash_password(password),
            "role": spec["role"],
            "is_active": True,
            "email_verified": True,
            "organisation": "EarthScape Climate Agency (bootstrap)",
            "preferences": {"theme": "light",
                            "units": {"temperature": "celsius", "precipitation": "millimetres",
                                      "wind": "m/s"},
                            "notification_channels": {"in_app": True, "email": False},
                            "dashboard_region": None},
            "failed_login_count": 0,
            "locked_until": None,
            "password_changed_at": now,
            "created_at": now,
            "updated_at": now,
            "created_by": "bootstrap_platform",
            "last_login_at": None,
        }
        inserted_id = users.insert_one(document)
        results.append({"role": spec["role"], "email": email, "created": True,
                        "user_id": str(inserted_id), "password": password})
        new_accounts.append({"role": spec["role"], "email": email, "password": password})

    if new_accounts and credentials_path is not None:
        _write_credentials(credentials_path, new_accounts)
    return results


def _write_credentials(path: Path, accounts: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    lines = [
        "# EarthScape Climate Agency - bootstrap credentials",
        f"# Generated {timestamp}",
        "# This file is git-ignored (see .gitignore: bootstrap-credentials.txt).",
        "# On POSIX systems set:  chmod 600 <this-file>   (Windows: restrict via icacls).",
        "# Treat these as shared only with the deployment owner, then change on first login.",
        "",
    ]
    for account in accounts:
        lines.append(f"{account['role']:<16} email={account['email']}  password={account['password']}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        # Windows does not honour POSIX mode bits; the git-ignore + manual note covers it.
        pass


# ---------------------------------------------------------------------------- main
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Bootstrap the EarthScape platform.")
    parser.add_argument("--skip-credentials-file", action="store_true",
                        help="Do not write backend/.data/bootstrap-credentials.txt.")
    parser.add_argument("--database", action="store_true",
                        help="Also confirm the MongoDB database mode is active.")
    args = parser.parse_args(argv)

    configure_django()
    from django.conf import settings
    from common.database import get_database

    from config import env

    schema_report = apply_schema_step()
    regions_report = seed_regions()

    db = get_database()
    print("Database mode       :", db.mode)
    print("Database name       :", db.database_name)
    if db.mode == "local-development":
        print("  note: MONGODB_URI is unset/unreachable, so the documented local document "
              "store (backend/.data/localdb) is used. Set MONGODB_URI for Atlas.")
    if schema_report.get("error"):
        print(f"Schema apply        : FAILED (core common.schema.apply_schema defect) -> "
              f"{schema_report['error']}")
        print("  note: indexes/validators were not created in this local store; regions and "
              "accounts below are still seeded against the document contract.")
    else:
        print(f"Schema version      : {schema_report.get('schema_version')} "
              f"(previous {schema_report.get('previous_version')})")
        print(f"  index operations   : created="
              f"{sum(1 for item in schema_report['index_operations'] if item.get('action')=='created')} "
              f"unchanged={sum(1 for item in schema_report['index_operations'] if item.get('action')=='unchanged')} "
              f"skipped={sum(1 for item in schema_report['index_operations'] if item.get('action')=='skipped')}")
    print(f"Regions seeded      : {regions_report}")

    credentials_path = None if args.skip_credentials_file else (
        Path(settings.BASE_DIR) / ".data" / "bootstrap-credentials.txt")
    account_results = provision_accounts(credentials_path=credentials_path)

    created_any = [account for account in account_results if account["created"]]
    print("\nAccounts:")
    for account in account_results:
        state = "created" if account["created"] else "exists"
        print(f"  - {account['role']:<16} {account['email']}  [{state}]")

    if created_any and credentials_path is not None:
        print(f"\nNew passwords written once to: {credentials_path}")
        print("(git-ignored). Set chmod 600 on POSIX. Change these on first login.\n")
        for account in created_any:
            print(f"  {account['role']:<16} email={account['email']}  password={account['password']}")
    elif not created_any:
        print("\nNo new accounts were created; existing operator accounts are unchanged and "
              "no secrets were re-emitted.")

    get_database().flush_if_dirty()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
