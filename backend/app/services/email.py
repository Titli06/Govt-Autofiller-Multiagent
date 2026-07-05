"""SMTP email delivery. Dev routes to Mailpit (mailpit:1025); prod swaps in real creds.

Sent inline in Phase 0 (acceptable for a walking skeleton). SEAM: Phase 1+ can move
send onto Celery (send_email_task) for retry durability — this module stays the sender.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from pathlib import Path

from app.config import settings
from app.core.logging import logger

_TEMPLATE_DIR = Path(__file__).parent / "email_templates"


def _render_verification(verify_url: str) -> tuple[str, str]:
    """Return (html, plaintext) bodies for the verification email."""
    html = (_TEMPLATE_DIR / "verify.html").read_text(encoding="utf-8")
    html = html.replace("{{verify_url}}", verify_url)
    plaintext = (
        "Verify your GovFill account\n\n"
        f"Open this link to verify your email address:\n{verify_url}\n\n"
        f"The link expires in {settings.email_verification_expire_hours} hours.\n"
        "If you didn't create a GovFill account, you can ignore this email.\n"
    )
    return html, plaintext


def _send(to: str, subject: str, html: str, plaintext: str) -> None:
    msg = EmailMessage()
    msg["From"] = settings.mail_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(plaintext)
    msg.add_alternative(html, subtype="html")

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
        if settings.smtp_use_tls:
            server.starttls()
        if settings.smtp_user:
            server.login(settings.smtp_user, settings.smtp_password)
        server.send_message(msg)


def send_verification_email(to: str, verify_url: str) -> None:
    html, plaintext = _render_verification(verify_url)
    _send(to, "Verify your GovFill account", html, plaintext)
    # PII-safe: log the event, not the recipient address.
    logger.info("verification_email_sent")
