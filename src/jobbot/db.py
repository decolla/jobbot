"""Banco SQLite: vagas, candidaturas, etapas, eventos, itens de ação e logs."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any

from jobbot.models import Job

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    hash TEXT NOT NULL UNIQUE,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '',
    modality TEXT NOT NULL DEFAULT 'unknown',
    description TEXT NOT NULL DEFAULT '',
    posted_at TEXT,
    source TEXT NOT NULL,
    apply_path TEXT NOT NULL DEFAULT 'form',
    status TEXT NOT NULL DEFAULT 'new',
    score INTEGER,
    score_reason TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_url ON jobs(url);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

-- UNIQUE(job_id) é a garantia final contra candidatura repetida.
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL UNIQUE REFERENCES jobs(id),
    status TEXT NOT NULL,
    mode TEXT NOT NULL,
    reason TEXT,
    cover_letter TEXT,
    plan_json TEXT,
    submitted_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stages (
    id INTEGER PRIMARY KEY,
    application_id INTEGER NOT NULL REFERENCES applications(id),
    stage TEXT NOT NULL,
    source TEXT NOT NULL,
    detail TEXT,
    occurred_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS action_items (
    id INTEGER PRIMARY KEY,
    application_id INTEGER NOT NULL REFERENCES applications(id),
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    link TEXT,
    due_date TEXT,
    prep_path TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    application_id INTEGER,
    job_id INTEGER,
    payload_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY,
    level TEXT NOT NULL,
    component TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS seen_messages (
    message_id TEXT PRIMARY KEY,
    seen_at TEXT NOT NULL
);
"""

# De onde uma candidatura pode seguir para envio. "submit_uncertain" fica de fora de propósito:
# se não sabemos se o envio aconteceu, repetir pode gerar candidatura duplicada.
CLAIMABLE = ("draft", "dry_run", "pending_approval", "approved", "blocked", "ready_manual")


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None: cada comando confirma sozinho; o UPDATE de claim é atômico por si só.
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """Agrupa várias gravações: muito mais rápido do que confirmar uma a uma, e tudo ou nada."""
    conn.execute("BEGIN")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def upsert_job(conn: sqlite3.Connection, job: Job) -> tuple[int, bool]:
    """Insere a vaga ou atualiza a existente. Retorna (id, criada)."""
    now = _now()
    row = conn.execute("SELECT id, description FROM jobs WHERE hash = ? OR url = ?", (job.hash, job.url)).fetchone()
    if row:
        description = job.description if len(job.description) > len(row["description"]) else row["description"]
        conn.execute("UPDATE jobs SET last_seen_at = ?, description = ? WHERE id = ?", (now, description, row["id"]))
        return row["id"], False
    cursor = conn.execute(
        """INSERT INTO jobs (hash, url, title, company, location, modality, description, posted_at, source,
                             apply_path, first_seen_at, last_seen_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (job.hash, job.url, job.title, job.company, job.location, job.modality, job.description, job.posted_at,
         job.source, job.apply_path, now, now),
    )
    return cursor.lastrowid, True


def get_job(conn: sqlite3.Connection, job_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def jobs_by_status(conn: sqlite3.Connection, status: str, limit: int | None = None) -> list[sqlite3.Row]:
    sql = "SELECT * FROM jobs WHERE status = ? ORDER BY id"
    params: tuple[Any, ...] = (status,)
    if limit is not None:
        sql += " LIMIT ?"
        params += (limit,)
    return conn.execute(sql, params).fetchall()


def set_job_status(conn: sqlite3.Connection, job_id: int, status: str, reason: str | None = None) -> None:
    conn.execute("UPDATE jobs SET status = ?, score_reason = COALESCE(?, score_reason) WHERE id = ?", (status, reason, job_id))


def set_job_description(conn: sqlite3.Connection, job_id: int, description: str) -> None:
    conn.execute("UPDATE jobs SET description = ? WHERE id = ?", (description, job_id))


def set_score(conn: sqlite3.Connection, job_id: int, score: int, reason: str, status: str) -> None:
    conn.execute("UPDATE jobs SET score = ?, score_reason = ?, status = ? WHERE id = ?", (score, reason, status, job_id))


def create_application(conn: sqlite3.Connection, job_id: int, mode: str) -> int:
    now = _now()
    conn.execute(
        "INSERT OR IGNORE INTO applications (job_id, status, mode, created_at, updated_at) VALUES (?, 'draft', ?, ?, ?)",
        (job_id, mode, now, now),
    )
    return conn.execute("SELECT id FROM applications WHERE job_id = ?", (job_id,)).fetchone()["id"]


def get_application(conn: sqlite3.Connection, app_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()


def get_application_by_job(conn: sqlite3.Connection, job_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM applications WHERE job_id = ?", (job_id,)).fetchone()


def applications_by_status(conn: sqlite3.Connection, *statuses: str) -> list[sqlite3.Row]:
    marks = ",".join("?" * len(statuses))
    return conn.execute(
        f"""SELECT a.*, j.title, j.company, j.url, j.score, j.source, j.apply_path
            FROM applications a JOIN jobs j ON j.id = a.job_id
            WHERE a.status IN ({marks}) ORDER BY a.id""",
        statuses,
    ).fetchall()


def trackable_applications(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Candidaturas que podem receber e-mail do processo: enviadas ou talvez enviadas.

    As já recusadas continuam na lista (uma recusa detectada por engano não pode esconder o resto do
    processo), marcadas em `rejected` para perderem o desempate para as ativas."""
    return conn.execute(
        """SELECT a.*, j.title, j.company, j.url, j.score, j.source, j.apply_path,
                  EXISTS (SELECT 1 FROM stages s WHERE s.application_id = a.id AND s.stage = 'rejected') AS rejected
           FROM applications a JOIN jobs j ON j.id = a.job_id
           WHERE a.status IN ('submitted', 'submit_uncertain', 'ready_manual')
           ORDER BY a.id"""
    ).fetchall()


def set_application_status(conn: sqlite3.Connection, app_id: int, status: str, reason: str | None = None) -> None:
    conn.execute(
        "UPDATE applications SET status = ?, reason = ?, updated_at = ? WHERE id = ?", (status, reason, _now(), app_id)
    )


def save_application_content(conn: sqlite3.Connection, app_id: int, mode: str, cover_letter: str, plan: dict) -> None:
    conn.execute(
        "UPDATE applications SET mode = ?, cover_letter = ?, plan_json = ?, updated_at = ? WHERE id = ?",
        (mode, cover_letter, json.dumps(plan, ensure_ascii=False), _now(), app_id),
    )


def claim_for_submission(conn: sqlite3.Connection, app_id: int) -> bool:
    """Reserva a candidatura para envio. Só um chamador consegue, e nunca depois de enviada."""
    marks = ",".join("?" * len(CLAIMABLE))
    cursor = conn.execute(
        f"UPDATE applications SET status = 'submitting', updated_at = ? WHERE id = ? AND status IN ({marks})",
        (_now(), app_id, *CLAIMABLE),
    )
    return cursor.rowcount == 1


def mark_submitted(conn: sqlite3.Connection, app_id: int, when: datetime | None = None, mode: str | None = None) -> None:
    stamp = when.isoformat(timespec="seconds") if when else _now()
    conn.execute(
        """UPDATE applications SET status = 'submitted', reason = NULL, submitted_at = ?, updated_at = ?,
                  mode = COALESCE(?, mode) WHERE id = ?""",
        (stamp, _now(), mode, app_id),
    )
    add_stage(conn, app_id, "applied", "jobbot", occurred_at=stamp)


def submissions_on(conn: sqlite3.Connection, day: date) -> int:
    """Envios feitos pelo jobbot no dia, incluindo tentativas de resultado incerto, que podem ter chegado.
    O que o usuário enviou à mão não consome o limite."""
    row = conn.execute(
        """SELECT COUNT(*) FROM applications a
           WHERE (substr(a.submitted_at, 1, 10) = ? AND a.mode != 'manual')
              OR EXISTS (SELECT 1 FROM events e WHERE e.application_id = a.id
                         AND e.kind = 'application_submit_attempt' AND substr(e.created_at, 1, 10) = ?)""",
        (day.isoformat(), day.isoformat()),
    ).fetchone()
    return row[0]


def jobs_ready_to_apply(conn: sqlite3.Connection, min_score: int, retryable: list[str]) -> list[sqlite3.Row]:
    marks = ",".join("?" * len(retryable))
    return conn.execute(
        f"""SELECT j.* FROM jobs j LEFT JOIN applications a ON a.job_id = j.id
            WHERE j.status = 'scored' AND j.score >= ? AND (a.id IS NULL OR a.status IN ({marks}))
            ORDER BY j.score DESC, j.id""",
        (min_score, *retryable),
    ).fetchall()


def add_stage(conn: sqlite3.Connection, app_id: int, stage: str, source: str, detail: str | None = None,
              occurred_at: str | None = None) -> None:
    conn.execute(
        "INSERT INTO stages (application_id, stage, source, detail, occurred_at) VALUES (?, ?, ?, ?, ?)",
        (app_id, stage, source, detail, occurred_at or _now()),
    )


def get_stages(conn: sqlite3.Connection, app_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM stages WHERE application_id = ? ORDER BY id", (app_id,)).fetchall()


def add_action_item(conn: sqlite3.Connection, app_id: int, kind: str, title: str, link: str | None = None,
                    due_date: str | None = None) -> int:
    """Cria o item de ação, ou atualiza prazo e link do que já está aberto para a mesma etapa."""
    row = conn.execute(
        "SELECT id FROM action_items WHERE application_id = ? AND kind = ? AND status = 'open'", (app_id, kind)
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE action_items SET link = COALESCE(?, link), due_date = COALESCE(?, due_date) WHERE id = ?",
            (link, due_date, row["id"]),
        )
        return row["id"]
    cursor = conn.execute(
        "INSERT INTO action_items (application_id, kind, title, link, due_date, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (app_id, kind, title, link, due_date, _now()),
    )
    return cursor.lastrowid


def get_action_item(conn: sqlite3.Connection, item_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM action_items WHERE id = ?", (item_id,)).fetchone()


def set_action_prep(conn: sqlite3.Connection, item_id: int, prep_path: str) -> None:
    conn.execute("UPDATE action_items SET prep_path = ? WHERE id = ?", (prep_path, item_id))


def complete_action_item(conn: sqlite3.Connection, item_id: int) -> bool:
    return conn.execute("UPDATE action_items SET status = 'done' WHERE id = ? AND status = 'open'", (item_id,)).rowcount == 1


def complete_action_items(conn: sqlite3.Connection, app_id: int, kinds: list[str]) -> None:
    marks = ",".join("?" * len(kinds))
    conn.execute(
        f"UPDATE action_items SET status = 'done' WHERE application_id = ? AND status = 'open' AND kind IN ({marks})",
        (app_id, *kinds),
    )


def open_action_items(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT i.*, j.title AS job_title, j.company, j.url AS job_url
           FROM action_items i
           JOIN applications a ON a.id = i.application_id
           JOIN jobs j ON j.id = a.job_id
           WHERE i.status = 'open'
           ORDER BY i.due_date IS NULL, i.due_date, i.id"""
    ).fetchall()


def log_event(conn: sqlite3.Connection, kind: str, application_id: int | None = None, job_id: int | None = None,
              payload: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO events (kind, application_id, job_id, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
        (kind, application_id, job_id, json.dumps(payload, ensure_ascii=False) if payload is not None else None, _now()),
    )


def get_events(conn: sqlite3.Connection, kind: str | None = None) -> list[dict]:
    sql, params = ("SELECT * FROM events WHERE kind = ? ORDER BY id", (kind,)) if kind else ("SELECT * FROM events ORDER BY id", ())
    return [
        {**dict(row), "payload": json.loads(row["payload_json"]) if row["payload_json"] else None}
        for row in conn.execute(sql, params)
    ]


def log(conn: sqlite3.Connection, level: str, component: str, message: str) -> None:
    conn.execute(
        "INSERT INTO logs (level, component, message, created_at) VALUES (?, ?, ?, ?)", (level, component, message, _now())
    )


def recent_logs(conn: sqlite3.Connection, level: str, day: date) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM logs WHERE level = ? AND substr(created_at, 1, 10) = ? ORDER BY id", (level, day.isoformat())
    ).fetchall()


def message_seen(conn: sqlite3.Connection, message_id: str) -> bool:
    return conn.execute("SELECT 1 FROM seen_messages WHERE message_id = ?", (message_id,)).fetchone() is not None


def mark_message_seen(conn: sqlite3.Connection, message_id: str) -> bool:
    """Retorna True na primeira vez que a mensagem aparece."""
    cursor = conn.execute(
        "INSERT OR IGNORE INTO seen_messages (message_id, seen_at) VALUES (?, ?)", (message_id, _now())
    )
    return cursor.rowcount == 1
