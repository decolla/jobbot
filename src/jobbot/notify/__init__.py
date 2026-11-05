"""Notificações opcionais (Telegram e e-mail), desligadas por padrão."""

from __future__ import annotations

from jobbot.notify.email import send_email
from jobbot.notify.telegram import NotifyError, send_telegram
from jobbot.profile import ConfigError, Settings


def notify(settings: Settings, env: dict[str, str], subject: str, text: str, telegram=send_telegram,
           email=send_email) -> list[str]:
    """Envia pelos canais ligados. Devolve os erros; a falha de um canal não impede o outro."""
    errors = []
    if settings.notifications.telegram:
        try:
            telegram(env, text)
        except (NotifyError, ConfigError) as exc:
            errors.append(f"telegram: {exc}")
    if settings.notifications.email:
        to = env.get("NOTIFY_EMAIL_TO") or env.get("SMTP_USER", "")
        try:
            if not to:
                raise ConfigError("preencha NOTIFY_EMAIL_TO no .env")
            email(env, to, subject, text)
        except (OSError, ConfigError) as exc:
            errors.append(f"e-mail: {exc}")
    return errors
