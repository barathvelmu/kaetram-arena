#!/usr/bin/env python3
"""
notifications.py — lightweight SMTP email notifications for Kaetram workflows.

Repo-safe by design:
- no hardcoded recipients
- no hardcoded credentials
- no-op when env vars are missing

Env vars:
    KAETRAM_NOTIFY_TO=alerts@example.com[,other@example.com]
    KAETRAM_NOTIFY_FROM=bot@example.com
    KAETRAM_SMTP_HOST=smtp.example.com
    KAETRAM_SMTP_PORT=587
    KAETRAM_SMTP_USERNAME=bot@example.com
    KAETRAM_SMTP_PASSWORD=app-password
    KAETRAM_SMTP_TLS=true
"""

from __future__ import annotations

import os
import smtplib
import socket
from email.message import EmailMessage


SMTP_ENV_KEYS = (
    "KAETRAM_NOTIFY_TO",
    "KAETRAM_NOTIFY_FROM",
    "KAETRAM_SMTP_HOST",
    "KAETRAM_SMTP_PORT",
    "KAETRAM_SMTP_USERNAME",
    "KAETRAM_SMTP_PASSWORD",
    "KAETRAM_SMTP_TLS",
)


def notification_env() -> dict[str, str]:
    return {k: os.environ[k] for k in SMTP_ENV_KEYS if os.environ.get(k)}


def notifications_enabled() -> bool:
    required = (
        "KAETRAM_NOTIFY_TO",
        "KAETRAM_NOTIFY_FROM",
        "KAETRAM_SMTP_HOST",
        "KAETRAM_SMTP_USERNAME",
        "KAETRAM_SMTP_PASSWORD",
    )
    return all(os.environ.get(k) for k in required)


def send_email_notification(subject: str, body: str) -> bool:
    """Send an email if SMTP env vars are configured. Never raises."""
    if not notifications_enabled():
        return False

    host = os.environ["KAETRAM_SMTP_HOST"]
    port = int(os.environ.get("KAETRAM_SMTP_PORT", "587"))
    username = os.environ["KAETRAM_SMTP_USERNAME"]
    password = os.environ["KAETRAM_SMTP_PASSWORD"]
    sender = os.environ["KAETRAM_NOTIFY_FROM"]
    recipients = [x.strip() for x in os.environ["KAETRAM_NOTIFY_TO"].split(",") if x.strip()]
    use_tls = os.environ.get("KAETRAM_SMTP_TLS", "true").lower() not in {"0", "false", "no"}

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            smtp.ehlo()
            if use_tls:
                smtp.starttls()
                smtp.ehlo()
            smtp.login(username, password)
            smtp.send_message(msg)
        return True
    except Exception as e:
        print(f"[notify] email send failed: {e}")
        return False


def format_notification(prefix: str, lines: list[str]) -> tuple[str, str]:
    host = socket.gethostname()
    subject = f"{prefix} [{host}]"
    body = "\n".join(lines)
    return subject, body
