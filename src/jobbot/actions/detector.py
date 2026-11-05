"""Itens de ação: etapas que só o usuário pode fazer, com prazo e link tirados do e-mail."""

from __future__ import annotations

import re
import sqlite3
from datetime import date

from jobbot import db
from jobbot.models import strip_accents

ACTION_TITLES = {"test": "Fazer teste", "video": "Gravar vídeo", "interview": "Entrevista", "offer": "Responder proposta"}

MONTHS = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
    "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}
_MONTH = "|".join(MONTHS)

# Só datas logo depois de uma palavra de prazo contam; "publicado em 01/10" não é prazo.
DEADLINE = re.compile(
    r"\b(ate|prazo|deadline|due|by|until|before|limite|expira\w*|agendad[ao]|marcad[ao]|scheduled)\b[^\n]{0,25}?"
    r"(?:(?P<y1>\d{4})-(?P<m1>\d{1,2})-(?P<d1>\d{1,2})"
    r"|\b(?P<d2>\d{1,2})/(?P<m2>\d{1,2})(?:/(?P<y2>\d{2,4}))?"
    rf"|\b(?P<d3>\d{{1,2}}) (?:de )?(?P<m3>{_MONTH})(?:,? (?:de )?(?P<y3>\d{{4}}))?"
    rf"|\b(?P<m4>{_MONTH}) (?P<d4>\d{{1,2}})(?:st|nd|rd|th)?(?:,? (?P<y4>\d{{4}}))?)"
)

URL = re.compile(r"https?://[^\s<>\"')\]]+")
IGNORED_URL = re.compile(r"unsubscribe|descadastr|opt-?out|preferenc|privacy|privacidade|\.(png|jpe?g|gif|svg|webp)(\?|$)", re.I)
ASSESSMENT_HOSTS = re.compile(
    r"hackerrank|codility|coderbyte|hackerearth|codesignal|testgorilla|hirevue|calendly|meet\.google|zoom\.us|teams\.microsoft",
    re.I,
)
ACTION_PATH = re.compile(r"test|assessment|challenge|desafio|entrevista|interview|video", re.I)


def _to_date(day: str, month: str, year: str | None, today: date) -> date | None:
    month_number = int(month) if month.isdigit() else MONTHS[month]
    try:
        if year:
            full_year = int(year) + 2000 if len(year) == 2 else int(year)
            return date(full_year, month_number, int(day))
        candidate = date(today.year, month_number, int(day))
        return candidate if candidate >= today else date(today.year + 1, month_number, int(day))
    except ValueError:
        return None


def extract_deadline(text: str, today: date) -> str | None:
    for match in DEADLINE.finditer(strip_accents(text)):
        for n in "1234":
            if match[f"d{n}"]:
                found = _to_date(match[f"d{n}"], match[f"m{n}"], match[f"y{n}"], today)
                if found:
                    return found.isoformat()
    return None


def extract_link(text: str) -> str | None:
    urls = [url.rstrip(".,;:!?") for url in URL.findall(text)]
    urls = [url for url in urls if not IGNORED_URL.search(url)]
    for preferred in (ASSESSMENT_HOSTS, ACTION_PATH):
        for url in urls:
            if preferred.search(url):
                return url
    return urls[0] if urls else None


def create_action(conn: sqlite3.Connection, app_id: int, kind: str, job_title: str, text: str, today: date) -> int:
    return db.add_action_item(
        conn, app_id, kind, f"{ACTION_TITLES[kind]}: {job_title}",
        link=extract_link(text), due_date=extract_deadline(text, today),
    )
