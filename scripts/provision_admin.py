"""Provision platform accounts against MongoDB.

Self-registration is disabled by default in this deployment, so this script is the
intended way to create the first administrator. Generated passwords are printed once
and are never stored in plaintext; the account is flagged to change it at first login.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from apps.users import repository  # noqa: E402
from common.exceptions import Conflict, ValidationError  # noqa: E402

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
ROLES = ("administrator", "climate_analyst", "viewer")


def generated_password() -> str:
    # 4 groups of 4 characters satisfy length, upper, lower and digit rules.
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    digits = "23456789"
    groups = [
        "".join(secrets.choice(alphabet) for _ in range(4)),
        "".join(secrets.choice(alphabet.lower()) for _ in range(4)),
        "".join(secrets.choice(digits) for _ in range(4)),
        secrets.token_urlsafe(4).rstrip("-_")[:4] or "Kx7m",
    ]
    return "".join(groups) + "a1"


def main() -> int:
    parser = argparse.ArgumentParser(description="Create or update a platform account.")
    parser.add_argument("--email", required=True, help="Account email address.")
    parser.add_argument("--name", default="", help="Display name of the person or service.")
    parser.add_argument("--role", default="administrator", choices=ROLES)
    parser.add_argument("--password", default="",
                        help="Omit to generate a strong one-time password (recommended).")
    parser.add_argument("--keep-temporary", action="store_true",
                        help="Do not force a password change at first login.")
    parser.add_argument("--reset", action="store_true",
                        help="Replace the password of an existing account instead of failing.")
    parser.add_argument("--promote", action="store_true",
                        help="Change an existing account to --role instead of failing.")
    parser.add_argument("--verified", action="store_true",
                        help="Mark the email as already verified by the institution.")
    args = parser.parse_args()

    normalized = args.email.strip().lower()
    if not EMAIL_RE.match(normalized):
        print(f"REFUSED: '{args.email}' is not a valid email address.")
        return 2

    existing = repository.get_by_email(normalized)
    password = args.password or generated_password()
    generated = not args.password

    if existing:
        user_id = str(existing["_id"])
        changed = []
        if args.promote and existing.get("role") != args.role:
            repository.set_role(user_id, args.role)
            changed.append(f"role -> {args.role}")
        if args.reset:
            repository.set_password(user_id, password)
            changed.append("password")
        if not changed:
            print(f"REFUSED: an account already exists for {normalized} "
                  f"(id {user_id}, role {existing.get('role')}).")
            print("Use --reset to replace its password or --promote to change its role.")
            return 3
        print(f"UPDATED {normalized}: " + ", ".join(changed))
        if generated and "password" in changed:
            _emit_password(password, args.role, normalized)
        return 0

    try:
        document = repository.create_user(
            full_name=args.name or normalized.split("@")[0].replace(".", " ").title(),
            email=normalized, password=password, role=args.role,
            email_verified=args.verified,
            must_change_password=not args.keep_temporary,
            created_by="provision_admin.py",
        )
    except (ValidationError, Conflict) as exc:
        print(f"REFUSED: {exc}")
        return 2

    print(f"CREATED {args.role} account {normalized} (id {document['_id']}).")
    if generated:
        _emit_password(password, args.role, normalized)
    else:
        print("Password was supplied on the command line; it is not repeated here. "
              "Rotate it if the shell history is shared.")
    print("Sign in at POST /api/v1/auth/login with this email and password.")
    return 0


def _emit_password(password: str, role: str, email: str) -> None:
    print("")
    print("Generated one-time password (shown once, never stored):")
    print(f"  {password}")
    print(f"Sign in as {email} (role {role}) and change this password immediately.")


if __name__ == "__main__":
    raise SystemExit(main())
