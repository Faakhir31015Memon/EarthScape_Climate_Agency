"""Outbound email. Disabled unless SMTP is explicitly configured.

No message is ever sent when ``ENABLE_EMAIL_NOTIFICATIONS`` is false, and failures
are recorded rather than presented as success.
"""
from __future__ import annotations

import logging
import re
import smtplib
from email.message import EmailMessage
from typing import Optional

from common import audit
from config import env

logger = logging.getLogger(__name__)

_ADDRESS = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


def is_configured() -> bool:
    return bool(env.get_raw("SMTP_HOST")) and env.get_bool("ENABLE_EMAIL_NOTIFICATIONS")


def configuration_status() -> dict:
    return {
        "enabled": env.get_bool("ENABLE_EMAIL_NOTIFICATIONS"),
        "host_configured": bool(env.get_raw("SMTP_HOST")),
        "sender": env.get_str("EMAIL_FROM_ADDRESS") or None,
        "ready": is_configured(),
    }


def send_email(*, to: str, subject: str, body: str, user_id: Optional[str] = None) -> bool:
    if not _ADDRESS.match(to or ""):
        logger.warning("Refused to send email to an invalid address")
        return False
    if not is_configured():
        logger.info("Email suppressed - SMTP is not configured (recipient domain=%s)",
                    _domain(to))
        return False

    message = EmailMessage()
    message["From"] = env.get_str("EMAIL_FROM_ADDRESS", "no-reply@earthscape.invalid")
    message["To"] = to
    message["Subject"] = subject[:200]
    message.set_content(body[:20000])

    host = env.get_str("SMTP_HOST")
    port = env.get_int("SMTP_PORT", 587)
    username = env.get_raw("SMTP_USER")
    password = env.get_raw("SMTP_PASSWORD")
    use_tls = env.get_bool("SMTP_USE_TLS", True)
    timeout = env.get_int("SMTP_TIMEOUT_SECONDS", 15)

    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=timeout) as server:
                server.ehlo()
                if username:
                    server.login(username, password or "")
                server.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=timeout) as server:
                server.ehlo()
                if use_tls:
                    server.starttls()
                    server.ehlo()
                if username:
                    server.login(username, password or "")
                server.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        logger.error("Email delivery failed: %s", str(exc)[:200])
        audit.record(action="email.delivery_failed", resource_type="notification",
                     actor_user_id=user_id, outcome="failure",
                     metadata={"reason": str(exc)[:180], "recipient_domain": _domain(to)})
        return False

    logger.info("Email delivered (recipient domain=%s)", _domain(to))
    return True


def _domain(email: str) -> str:
    return (email or "").split("@")[-1].lower()
