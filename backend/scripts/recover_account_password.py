"""Recover sign-in for an operator account that already exists.

``bootstrap_platform.py`` generates a password when ``BOOTSTRAP_ADMIN_PASSWORD`` is not
set, writes it to a git-ignored file and never re-emits it - so a data directory copied
to another machine has accounts nobody can open. This resets one account to a freshly
generated password, prints it once, and revokes that account's existing sessions.
Nothing else changes and no secret is written to disk.

Run from the ``backend`` directory::

    python scripts/recover_account_password.py --role administrator
    python scripts/recover_account_password.py --email earthscape@admin.com

On the local-development store the running server keeps its own copy of the
documents in memory, so stop ``runserver`` before running this and start it again
afterwards. Against MongoDB Atlas the server sees the change immediately.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Optional

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(BACKEND_DIR / "scripts"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from bootstrap_platform import DEFAULT_ACCOUNTS, generate_password  # noqa: E402


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--role", choices=sorted(DEFAULT_ACCOUNTS),
                        help="Reset the bootstrap account for this role.")
    target.add_argument("--email", help="Reset the account with this email address.")
    args = parser.parse_args(argv)

    import django

    django.setup()
    from django.conf import settings

    from common import security
    from common.database import get_database
    from common.repositories import users, utcnow

    email = args.email or (os.environ.get(DEFAULT_ACCOUNTS[args.role]["env_email"])
                           or DEFAULT_ACCOUNTS[args.role]["email"])
    email = email.strip().lower()

    document = users.find_one({"email_normalized": email})
    if document is None:
        print(f"No account with email {email}. Bootstrap has not been run, or the "
              "address differs.", file=sys.stderr)
        return 1

    password = generate_password()
    problems = security.password_problems(password)
    if problems:  # pragma: no cover - generate_password satisfies the policy by construction
        print("Refusing to set a password that fails the platform policy: "
              + " ".join(problems), file=sys.stderr)
        return 1

    from apps.authentication import services as authentication

    users.update_by_id(str(document["_id"]), {
        "password_hash": security.hash_password(password),
        "password_changed_at": utcnow(),
        "updated_at": utcnow(),
        "failed_login_count": 0,
        "locked_until": None,
    })
    revoked = authentication.revoke_all_for_user(str(document["_id"]),
                                                 reason="local_password_recovery")
    get_database().flush_if_dirty()

    print(f"Account            : {email} ({document.get('role')})")
    print(f"Data store         : {get_database().mode} "
          f"({Path(settings.MEDIA_ROOT).name})")
    print(f"Sessions revoked   : {revoked}")
    print(f"\nNew password (shown once, not stored): {password}")
    print("Sign in with it, then set your own through the profile page.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
