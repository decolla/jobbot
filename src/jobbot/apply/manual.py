"""Vagas de fontes sem automação (LinkedIn, Indeed...): o usuário copia a página e o jobbot prepara o resto."""

from __future__ import annotations

import platform
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel

from jobbot import db
from jobbot.apply.base import Applier
from jobbot.models import Job, Modality, normalize_url
from jobbot.profile import ConfigError, Profile, Settings, resume_text_for
from jobbot.scoring.llm_scorer import MAX_JOB_CHARS, neutralize_tags, score_job

# Texto curto não é uma página de vaga e pode ser uma senha copiada: nem vai ao Claude nem é gravado.
MIN_CLIPBOARD_CHARS = 200
KNOWN_SOURCES = ("linkedin", "indeed", "gupy", "vagas", "programathor")
CLIPBOARD_COMMANDS = {
    "Windows": ["powershell", "-NoProfile", "-Command", "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-Clipboard -Raw"],
    "Darwin": ["pbpaste"],
}
LINUX_CLIPBOARD_COMMAND = ["xclip", "-selection", "clipboard", "-o"]


class JobExtract(BaseModel):
    title: str
    company: str
    location: str = ""
    modality: Modality = "unknown"


@dataclass
class AddResult:
    created: bool
    job_id: int
    score: int | None = None
    reason: str = ""
    pack_path: Path | None = None


def read_clipboard() -> str:
    command = CLIPBOARD_COMMANDS.get(platform.system(), LINUX_CLIPBOARD_COMMAND)
    try:
        result = subprocess.run(command, capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ConfigError(f"não foi possível ler a área de transferência: {exc}") from exc
    return result.stdout.decode("utf-8", errors="replace").strip()


def source_from_url(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    return next((name for name in KNOWN_SOURCES if name in host), "manual")


def _extract_prompt(text: str) -> str:
    return (
        "Extraia título, empresa, local e modalidade da vaga no texto copiado de uma página.\n\n"
        f"<pagina>\n{neutralize_tags(text[:MAX_JOB_CHARS], 'pagina')}\n</pagina>"
    )


def add_manual_job(conn: sqlite3.Connection, settings: Settings, profile: Profile, root: Path, llm, url: str) -> AddResult:
    text = read_clipboard()
    if len(text) < MIN_CLIPBOARD_CHARS:
        raise ConfigError("copie o texto da página da vaga (Ctrl+A, Ctrl+C) e rode o comando de novo")
    clean_url = normalize_url(url)
    existing = conn.execute("SELECT id FROM jobs WHERE url = ?", (clean_url,)).fetchone()
    if existing:
        return AddResult(False, existing["id"])
    extract = llm.ask(_extract_prompt(text), JobExtract, model=settings.llm.scoring_model)
    job = Job(**extract.model_dump(), description=text, url=clean_url, source=source_from_url(clean_url), apply_path="manual")
    job_id, created = db.upsert_job(conn, job)
    if not created:
        return AddResult(False, job_id)
    # Foi o usuário quem escolheu a vaga: a nota informa, mas não impede de preparar o material.
    result = score_job(job, profile, resume_text_for(root, profile, job), llm, settings.llm.scoring_model)
    db.set_score(conn, job_id, result.score, result.reason, "scored")
    pack = Applier(conn, settings, profile, root, llm).prepare_manual(job_id)
    return AddResult(True, job_id, result.score, result.reason, pack)
