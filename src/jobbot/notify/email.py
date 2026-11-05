"""Envio de e-mail por SMTP, com as credenciais do .env."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from jobbot.profile import ConfigError


def send_email(env: dict[str, str], to: str, subject: str, body: str, in_reply_to: str | None = None,
               smtp_factory=None) -> None:
    host, user, password = (env.get(key, "") for key in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"))
    if not (host and user and password):
        raise ConfigError("SMTP não configurado: preencha SMTP_HOST, SMTP_USER e SMTP_PASSWORD no .env")
    port = int(env.get("SMTP_PORT") or 587)
    message = EmailMessage()
    message["From"] = user
    message["To"] = to
    message["Subject"] = subject
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
        message["References"] = in_reply_to
    message.set_content(body)
    factory = smtp_factory or (smtplib.SMTP_SSL if port == 465 else smtplib.SMTP)
    with factory(host, port, timeout=30) as smtp:
        if port != 465:
            smtp.starttls()
        smtp.login(user, password)
        smtp.send_message(message)
