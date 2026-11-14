"""Follow-up de candidaturas sem resposta. Gera pendência e rascunho; quem envia é o usuário.

Cadência (a mesma do career-ops): o primeiro uma semana depois do envio, o segundo uma semana depois do
primeiro feito. Sem resposta depois disso, a candidatura é marcada "sem resposta" e não há mais follow-ups.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from jobbot import db
from jobbot.models import detect_language
from jobbot.profile import Profile, Settings

KIND = "follow_up"
# Etapas que não são resposta da empresa: o envio em si e o "recebemos sua candidatura" automático.
NOT_AN_ANSWER = {"applied", "screening"}

DRAFTS = {
    "pt": (
        "Assunto: Candidatura para {title}\n\n"
        "Olá, tudo bem?\n\n"
        "Me candidatei à vaga de {title} na {company} em {applied}. Continuo com muito interesse na oportunidade "
        "e fico à disposição para conversar ou enviar qualquer informação.\n\n"
        "Agradeço a atenção,\n{signature}",
        "Assunto: Candidatura para {title}\n\n"
        "Olá! Retomo o contato sobre a minha candidatura à vaga de {title}, enviada em {applied}. "
        "Sigo com interesse e disponível para conversar quando for possível.\n\n"
        "Obrigado pela atenção,\n{signature}",
    ),
    "en": (
        "Subject: Application for {title}\n\n"
        "Hi, I hope you are well.\n\n"
        "I applied for the {title} position at {company} on {applied}. I am still very interested in the "
        "opportunity and happy to talk or share any further information.\n\n"
        "Thank you for your time,\n{signature}",
        "Subject: Application for {title}\n\n"
        "Hi! I am following up on my application for the {title} position, sent on {applied}. "
        "I remain interested and available to talk whenever convenient.\n\n"
        "Thank you,\n{signature}",
    ),
}


@dataclass
class FollowupSummary:
    created: int = 0
    cold: int = 0


def _day(stamp: str) -> date:
    return datetime.fromisoformat(stamp).date()


def _write_draft(root: Path, app: sqlite3.Row, attempt: int, profile: Profile, language: str) -> str:
    signature = "\n".join(part for part in (profile.personal.name, profile.personal.linkedin) if part)
    text = DRAFTS[language][min(attempt, 2) - 1].format(
        title=app["title"], company=app["company"], applied=_day(app["submitted_at"]).strftime("%d/%m/%Y"),
        signature=signature,
    )
    path = Path("reports") / "followups" / f"{app['id']}-{attempt}.md"
    (root / path).parent.mkdir(parents=True, exist_ok=True)
    (root / path).write_text(
        f"# Follow-up {attempt}: {app['title']} ({app['company']})\n\n"
        "Rascunho para você revisar e enviar: ele nunca é enviado pelo jobbot.\n"
        "Mande para quem conduz o processo: o remetente do e-mail de confirmação, ou o recrutador no LinkedIn.\n\n"
        f"Vaga: {app['url']}\n\n---\n\n{text}\n",
        encoding="utf-8",
    )
    return path.as_posix()


def plan_followups(conn: sqlite3.Connection, root: Path, profile: Profile, settings: Settings,
                   today: date) -> FollowupSummary:
    summary = FollowupSummary()
    config = settings.followups
    if not config.enabled:
        return summary
    for app in db.applications_by_status(conn, "submitted"):
        if not app["submitted_at"]:
            continue
        if {s["stage"] for s in db.get_stages(conn, app["id"])} - NOT_AN_ANSWER:
            continue  # a empresa respondeu, a vaga fechou ou já está marcada sem resposta
        items = conn.execute("SELECT * FROM action_items WHERE application_id = ? AND kind = ? ORDER BY id",
                             (app["id"], KIND)).fetchall()
        if any(item["status"] == "open" for item in items):
            continue
        if items:
            last = items[-1]
            since = _day(last["completed_at"] or last["created_at"])  # a semana conta de quando foi feito
        else:
            since = _day(app["submitted_at"])
        if (today - since).days < config.after_days:
            continue
        if len(items) >= config.max_attempts:
            db.add_stage(conn, app["id"], "no_response", "followup")
            db.log_event(conn, "no_response", application_id=app["id"], job_id=app["job_id"],
                         payload={"stage": "no_response"})
            summary.cold += 1
            continue
        attempt = len(items) + 1
        job = db.get_job(conn, app["job_id"])
        language = detect_language(f"{job['title']}\n{job['description']}")
        item_id = db.add_action_item(conn, app["id"], KIND, f"Follow-up {attempt}: {app['title']}",
                                     link=app["url"], due_date=today.isoformat())
        db.set_action_prep(conn, item_id, _write_draft(root, app, attempt, profile, language))
        db.log_event(conn, "follow_up_planned", application_id=app["id"], job_id=app["job_id"],
                     payload={"attempt": attempt})
        summary.created += 1
    return summary
