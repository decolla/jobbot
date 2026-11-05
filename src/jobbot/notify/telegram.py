"""Notificação pelo Telegram, com o token do .env."""

from __future__ import annotations

import httpx

API_URL = "https://api.telegram.org"
MAX_CHARS = 4000  # o Telegram recusa mensagens acima de 4096 caracteres


class NotifyError(Exception):
    """Falha ao notificar. A mensagem nunca traz o token."""


def send_telegram(env: dict[str, str], text: str, transport: httpx.BaseTransport | None = None) -> None:
    token, chat_id = env.get("TELEGRAM_BOT_TOKEN", ""), env.get("TELEGRAM_CHAT_ID", "")
    if not (token and chat_id):
        raise NotifyError("Telegram não configurado: preencha TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID no .env")
    if len(text) > MAX_CHARS:
        text = text[: MAX_CHARS - 10] + "\n[...]"
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    try:
        with httpx.Client(timeout=30, transport=transport) as client:
            response = client.post(f"{API_URL}/bot{token}/sendMessage", json=payload)
    except httpx.HTTPError as exc:
        # O erro do httpx traz a URL, e a URL traz o token: só o tipo do erro segue adiante.
        raise NotifyError(f"falha de conexão com o Telegram ({type(exc).__name__})") from None
    if response.status_code != 200:
        raise NotifyError(f"Telegram respondeu HTTP {response.status_code}")
