"""Leitura da caixa de entrada por IMAP, só leitura: nada é apagado nem marcado como lido."""

from __future__ import annotations

import email
import hashlib
import imaplib
from dataclasses import dataclass
from datetime import date, timedelta
from email import policy
from email.utils import parseaddr, parsedate_to_datetime
from typing import Callable

from bs4 import BeautifulSoup

from jobbot.profile import ConfigError

IMAP_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
# Basta o começo da mensagem: anexos grandes ficam de fora.
FETCH_PARTS = "(BODY.PEEK[]<0.300000>)"


class MailboxError(Exception):
    """Falha ao falar com o servidor de e-mail."""


@dataclass
class Message:
    message_id: str
    subject: str
    sender: str
    sender_address: str
    text: str
    html: str
    date: str


def _content(part) -> str:
    if part is None:
        return ""
    try:
        return part.get_content()
    except (LookupError, ValueError):
        return part.get_payload(decode=True).decode("utf-8", errors="replace")


def _text_with_links(plain: str, html: str) -> str:
    """Texto da mensagem, com os links do HTML: o link do teste costuma estar só no botão."""
    if not html:
        return plain
    soup = BeautifulSoup(html, "html.parser")
    text = plain or soup.get_text("\n", strip=True)
    links = [a["href"] for a in soup.find_all("a", href=True) if a["href"].startswith("http") and a["href"] not in text]
    return "\n".join([text, *dict.fromkeys(links)])


def parse_message(raw: bytes) -> Message:
    message = email.message_from_bytes(raw, policy=policy.default)
    plain = _content(message.get_body(preferencelist=("plain",)))
    html = _content(message.get_body(preferencelist=("html",)))
    sender = str(message.get("From", ""))
    try:
        sent_at = parsedate_to_datetime(str(message.get("Date", ""))).isoformat()
    except (TypeError, ValueError):
        sent_at = ""
    message_id = str(message.get("Message-ID", "")).strip() or "sem-id:" + hashlib.sha256(raw).hexdigest()
    return Message(
        message_id=message_id,
        subject=str(message.get("Subject", "")),
        sender=sender,
        sender_address=parseaddr(sender)[1].lower(),
        text=_text_with_links(plain, html),
        html=html,
        date=sent_at,
    )


def _imap_date(day: date) -> str:
    return f"{day.day:02d}-{IMAP_MONTHS[day.month - 1]}-{day.year}"


class Mailbox:
    def __init__(self, host: str, user: str, password: str,
                 imap_factory: Callable[[str], imaplib.IMAP4] = imaplib.IMAP4_SSL):
        self._host, self._user, self._password = host, user, password
        self._imap_factory = imap_factory

    @classmethod
    def from_env(cls, env: dict[str, str]) -> "Mailbox":
        host, user, password = (env.get(key, "") for key in ("IMAP_HOST", "IMAP_USER", "IMAP_PASSWORD"))
        if not (host and user and password):
            raise ConfigError("IMAP não configurado: preencha IMAP_HOST, IMAP_USER e IMAP_PASSWORD no .env")
        return cls(host, user, password)

    def fetch_recent(self, days: int, senders: list[str] | None = None, today: date | None = None) -> list[Message]:
        since = _imap_date((today or date.today()) - timedelta(days=days))
        searches = [("SINCE", since, "FROM", sender) for sender in senders] if senders else [("SINCE", since)]
        try:
            imap = self._imap_factory(self._host)
            imap.login(self._user, self._password)
            try:
                imap.select("INBOX", readonly=True)
                messages = []
                for criteria in searches:
                    _, data = imap.search(None, *criteria)
                    for number in data[0].split():
                        _, parts = imap.fetch(number, FETCH_PARTS)
                        raw = next((part[1] for part in parts if isinstance(part, tuple)), None)
                        try:
                            if raw:
                                messages.append(parse_message(raw))
                        except (ValueError, IndexError, LookupError, TypeError, AttributeError):
                            continue  # uma mensagem malformada não impede a leitura das outras
                return messages
            finally:
                imap.logout()
        except (imaplib.IMAP4.error, OSError) as exc:
            raise MailboxError(f"falha ao ler e-mails em {self._host}: {exc}") from exc
