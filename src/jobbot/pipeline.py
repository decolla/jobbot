"""Etapas do fluxo diário, usadas pela CLI."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from jobbot import db
from jobbot.llm import ClaudeClient, LLMError
from jobbot.models import Job
from jobbot.profile import Profile, Settings, resume_text_for
from jobbot.scoring.llm_scorer import score_job
from jobbot.scoring.rules import evaluate
from jobbot.sources import build_sources
from jobbot.sources.base import PoliteClient, Source, SourceError

MAX_CONSECUTIVE_LLM_FAILURES = 3
# Abaixo disso a descrição é só o resumo da listagem, e vale buscar a página da vaga.
MIN_FULL_DESCRIPTION_CHARS = 400


def build_http(settings: Settings) -> PoliteClient:
    return PoliteClient(settings.http.min_delay_seconds, settings.http.max_delay_seconds)


def build_enabled_sources(settings: Settings) -> list[Source]:
    return build_sources(settings.sources, build_http(settings))


def build_llm(settings: Settings) -> ClaudeClient:
    return ClaudeClient(timeout=settings.llm.timeout_seconds)


@dataclass
class SearchSummary:
    # Fonte -> (vagas encontradas, vagas novas)
    per_source: dict[str, tuple[int, int]] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)

    @property
    def new_jobs(self) -> int:
        return sum(new for _, new in self.per_source.values())


def run_search(conn: sqlite3.Connection, sources: list[Source]) -> SearchSummary:
    summary = SearchSummary()
    for source in sources:
        try:
            jobs = source.fetch_jobs()
        except (SourceError, httpx.HTTPError, ValueError) as exc:
            summary.failures[source.name] = str(exc)
            db.log(conn, "error", f"search.{source.name}", str(exc))
            continue
        for warning in source.warnings:
            db.log(conn, "warning", f"search.{source.name}", warning)
        with db.transaction(conn):
            new = sum(1 for job in jobs if db.upsert_job(conn, job)[1])
        summary.per_source[source.name] = (len(jobs), new)
    return summary


@dataclass
class ScoreSummary:
    discarded: int = 0
    approved: int = 0
    low_score: int = 0
    errors: int = 0


def _apply_rules(conn: sqlite3.Connection, profile: Profile, summary: ScoreSummary) -> None:
    with db.transaction(conn):
        for row in db.jobs_by_status(conn, "new"):
            result = evaluate(Job.from_row(row), profile)
            if result.passed:
                db.set_job_status(conn, row["id"], "filtered")
            else:
                db.set_job_status(conn, row["id"], "discarded", result.reason)
                summary.discarded += 1


def _enrich(conn: sqlite3.Connection, row: sqlite3.Row, sources: dict[str, Source]) -> Job:
    job = Job.from_row(row)
    source = sources.get(job.source)
    if source is None or len(job.description) >= MIN_FULL_DESCRIPTION_CHARS:
        return job
    try:
        description = source.enrich(job.url)
    except (SourceError, httpx.HTTPError) as exc:
        db.log(conn, "warning", f"score.{job.source}", f"não foi possível detalhar {job.url}: {exc}")
        return job
    if description:
        db.set_job_description(conn, row["id"], description)
        job.description = description
    return job


def run_score(conn: sqlite3.Connection, settings: Settings, profile: Profile, root: Path, llm,
              sources: list[Source]) -> ScoreSummary:
    summary = ScoreSummary()
    _apply_rules(conn, profile, summary)
    pending = db.jobs_by_status(conn, "filtered", limit=settings.max_scores_per_run)
    if not pending:
        return summary
    resumes: dict[str, str] = {}
    by_name = {source.name: source for source in sources}
    consecutive_failures = 0
    for row in pending:
        job = _enrich(conn, row, by_name)
        try:
            resume_text = resume_text_for(root, profile, job, resumes)
            result = score_job(job, profile, resume_text, llm, settings.llm.scoring_model)
        except LLMError as exc:
            summary.errors += 1
            consecutive_failures += 1
            db.log(conn, "error", "score", f"{job.title}: {exc}")
            if consecutive_failures >= MAX_CONSECUTIVE_LLM_FAILURES:
                db.log(conn, "error", "score", "muitas falhas seguidas do Claude; o restante fica para a próxima execução")
                break
            continue
        consecutive_failures = 0
        approved = result.score >= settings.min_score
        db.set_score(conn, row["id"], result.score, result.reason, "scored" if approved else "low_score")
        if approved:
            summary.approved += 1
        else:
            summary.low_score += 1
    return summary
