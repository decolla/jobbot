import json

import httpx
import pytest

from jobbot.notify import notify
from jobbot.notify.telegram import NotifyError, send_telegram
from jobbot.profile import load_settings

TOKEN = "123:segredo"
ENV = {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": "42"}


def _transport(status=200):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"ok": status == 200})

    return httpx.MockTransport(handler), calls


def test_telegram_posts_message_to_chat():
    transport, calls = _transport()
    send_telegram(ENV, "Olá", transport=transport)
    request = calls[0]
    assert request.url.path == f"/bot{TOKEN}/sendMessage"
    payload = json.loads(request.content)
    assert (payload["chat_id"], payload["text"]) == ("42", "Olá")


def test_telegram_truncates_long_messages():
    transport, calls = _transport()
    send_telegram(ENV, "x" * 10000, transport=transport)
    assert len(calls[0].content) < 4500


def test_telegram_error_never_shows_the_token():
    transport, _ = _transport(status=401)
    with pytest.raises(NotifyError) as error:
        send_telegram(ENV, "Olá", transport=transport)
    assert TOKEN not in str(error.value)
    assert "401" in str(error.value)


def test_telegram_without_settings_is_error():
    with pytest.raises(NotifyError, match="TELEGRAM"):
        send_telegram({}, "Olá")


def _settings(project, telegram=False, email=False):
    settings = load_settings(project)
    settings.notifications.telegram = telegram
    settings.notifications.email = email
    return settings


def test_notify_does_nothing_when_disabled(project):
    sent = []
    errors = notify(_settings(project), ENV, "Assunto", "Texto", telegram=lambda *a: sent.append(a),
                    email=lambda *a, **k: sent.append(a))
    assert sent == [] and errors == []


def test_notify_sends_to_enabled_channels(project):
    sent = []
    env = {**ENV, "NOTIFY_EMAIL_TO": "eu@real.com"}
    errors = notify(_settings(project, telegram=True, email=True), env, "Assunto", "Texto",
                    telegram=lambda env, text: sent.append(("telegram", text)),
                    email=lambda env, to, subject, body: sent.append(("email", to, subject)))
    assert errors == []
    assert sent == [("telegram", "Texto"), ("email", "eu@real.com", "Assunto")]


def test_notify_failure_in_one_channel_does_not_stop_the_other(project):
    sent = []

    def broken(env, text):
        raise NotifyError("Telegram respondeu HTTP 500")

    errors = notify(_settings(project, telegram=True, email=True), {"SMTP_USER": "eu@real.com"}, "Assunto", "Texto",
                    telegram=broken, email=lambda env, to, subject, body: sent.append(to))
    assert sent == ["eu@real.com"]
    assert errors == ["telegram: Telegram respondeu HTTP 500"]
