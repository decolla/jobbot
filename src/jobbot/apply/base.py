"""Orquestra a candidatura: respeita o modo, o limite diário e nunca envia duas vezes."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

from jobbot import db
from jobbot.apply.form_filler import BrowserSession, detect_obstacle, fill_form, inspect_form, open_form, submit_form
from jobbot.apply.form_plan import FillPlan, FormField, plan_fill
from jobbot.apply.text_tailor import FabricationError, QuestionAnswer, Tailored, build_facts, tailor
from jobbot.llm import LLMError
from jobbot.models import Job, strip_accents
from jobbot.profile import ConfigError, Profile, Settings, read_document_text

OBSTACLE_MESSAGES = {
    "captcha": "o site pediu CAPTCHA ou verificação anti-bot; candidate-se à mão pelo link",
    "login": "o site pediu login; rode jobbot login com o link da vaga e depois tente de novo",
}
MASK = "***"


@dataclass
class ApplySummary:
    dry_run: int = 0
    pending_approval: int = 0
    submitted: int = 0
    blocked: int = 0
    manual: int = 0
    uncertain: int = 0
    skipped_by_limit: int = 0


def _question_key(text: str) -> str:
    return re.sub(r"\s+", " ", strip_accents(text)).strip()


def _audit_fields(plan: FillPlan) -> list[dict]:
    """O que foi preenchido, para auditoria. Respostas sensíveis não ficam gravadas."""
    return [
        {"label": action.label, "value": MASK if action.sensitive else action.value, "origin": action.origin}
        for action in plan.actions
    ]


def _first_line(exc: Exception) -> str:
    return (str(exc).splitlines() or [type(exc).__name__])[0]


class Applier:
    def __init__(self, conn: sqlite3.Connection, settings: Settings, profile: Profile, root: Path, llm,
                 confirm: Callable[[str], bool] | None = None, submit_timeout_ms: int = 20000):
        self.conn = conn
        self.settings = settings
        self.profile = profile
        self.root = root
        self.llm = llm
        self.confirm = confirm
        self.submit_timeout_ms = submit_timeout_ms
        self._session: BrowserSession | None = None
        self._facts: str | None = None

    # ---------- entrada ----------

    def run(self, mode: str, retry_blocked: bool = False) -> ApplySummary:
        if mode != "dry-run":
            self._require_real_profile()
        retryable = ["draft"]
        if mode != "dry-run":
            retryable.append("dry_run")
        if retry_blocked:
            retryable.append("blocked")
        rows = db.jobs_ready_to_apply(self.conn, self.settings.min_score, retryable)
        remaining = max(self.settings.daily_application_limit - db.submissions_on(self.conn, date.today()), 0)
        summary = ApplySummary()
        try:
            for index, row in enumerate(rows):
                if index >= remaining:
                    summary.skipped_by_limit = len(rows) - index
                    db.log(self.conn, "info", "apply", f"limite diário: {summary.skipped_by_limit} vaga(s) ficam para depois")
                    break
                self._process(row, mode, summary)
        finally:
            self._close_browser()
        return summary

    def approve(self, app_id: int) -> str:
        app = db.get_application(self.conn, app_id)
        if app is None:
            raise ConfigError(f"candidatura {app_id} não existe")
        if app["status"] not in ("pending_approval", "dry_run"):
            raise ConfigError(f"candidatura {app_id} está como {app['status']} e não pode ser aprovada")
        self._require_real_profile()
        if db.submissions_on(self.conn, date.today()) >= self.settings.daily_application_limit:
            raise ConfigError("limite diário de candidaturas atingido; tente amanhã")
        row = db.get_job(self.conn, app["job_id"])
        if row["apply_path"] == "manual":
            raise ConfigError(f"candidatura {app_id} é manual: envie pelo link e rode jobbot done {app_id}")
        summary = ApplySummary()
        try:
            self._prepare_form(app_id, row, "approval", summary, approved=True)
        except (PlaywrightError, LLMError, FabricationError) as exc:
            self._block(app_id, row, _first_line(exc), summary)
        finally:
            self._close_browser()
        return db.get_application(self.conn, app_id)["status"]

    def prepare_manual(self, job_id: int) -> Path:
        """Prepara carta e pacote de uma vaga que o usuário vai enviar à mão."""
        row = db.get_job(self.conn, job_id)
        app_id = db.create_application(self.conn, job_id, "manual")
        self._prepare_manual(app_id, row, ApplySummary())
        return self._app_dir(app_id) / "pacote.md"

    # ---------- preparação ----------

    def _process(self, row: sqlite3.Row, mode: str, summary: ApplySummary) -> None:
        app_id = db.create_application(self.conn, row["id"], mode)
        try:
            if row["apply_path"] == "manual":
                self._prepare_manual(app_id, row, summary)
            else:
                self._prepare_form(app_id, row, mode, summary)
        except (PlaywrightError, LLMError, FabricationError) as exc:
            self._block(app_id, row, _first_line(exc), summary)

    def _prepare_manual(self, app_id: int, row: sqlite3.Row, summary: ApplySummary) -> None:
        tailored = self._tailor(app_id, Job.from_row(row), [])
        db.save_application_content(self.conn, app_id, "manual", tailored.cover_letter, {"tailored": tailored.model_dump()})
        self._write_pack(app_id, row, tailored, FillPlan(), "Esta fonte não permite automação: envie pelo link.")
        db.set_application_status(self.conn, app_id, "ready_manual")
        db.log_event(self.conn, "application_ready_manual", application_id=app_id, job_id=row["id"])
        summary.manual += 1

    def _prepare_form(self, app_id: int, row: sqlite3.Row, mode: str, summary: ApplySummary, approved: bool = False) -> None:
        job = Job.from_row(row)
        page = self._browser().new_page()
        try:
            open_form(page, job.url)
            obstacle = detect_obstacle(page)
            if obstacle:
                self._block(app_id, row, OBSTACLE_MESSAGES[obstacle], summary)
                return
            fields = inspect_form(page)
            if not fields:
                self._block(app_id, row, "formulário não encontrado na página da vaga", summary)
                return
            plan, tailored = self._build_plan(app_id, job, fields)
            db.save_application_content(self.conn, app_id, mode, tailored.cover_letter, self._stored_plan(plan, tailored))
            if plan.blockers:
                self._block(app_id, row, "; ".join(plan.blockers), summary, plan, tailored)
                return
            if approved or mode == "auto":
                self._send(app_id, row, page, plan, summary)
            elif mode == "approval":
                self._ask_approval(app_id, row, page, plan, tailored, summary)
            else:
                self._dry_run(app_id, row, page, plan, summary)
        finally:
            page.close()

    def _build_plan(self, app_id: int, job: Job, fields: list[FormField]) -> tuple[FillPlan, Tailored]:
        resume = self.root / self.profile.documents.resume
        questions = plan_fill(fields, self.profile, resume).questions
        tailored = self._tailor(app_id, job, questions)
        letter_path = self._app_dir(app_id) / "carta.txt"
        letter_path.write_text(tailored.cover_letter, encoding="utf-8")
        given = {_question_key(answer.question): answer for answer in tailored.answers}
        answers: dict[str, QuestionAnswer] = {}
        for question in questions:
            answer = given.get(_question_key(question.label))
            if answer:
                answers[question.label] = answer
        plan = plan_fill(fields, self.profile, resume, cover_letter=tailored.cover_letter, answers=answers,
                         cover_letter_path=letter_path)
        return plan, tailored

    def _tailor(self, app_id: int, job: Job, questions: list[FormField]) -> Tailored:
        stored = db.get_application(self.conn, app_id)["plan_json"]
        if stored and "tailored" in json.loads(stored):
            previous = Tailored.model_validate(json.loads(stored)["tailored"])
            known = {_question_key(answer.question) for answer in previous.answers}
            if all(_question_key(question.label) in known for question in questions):
                return previous
        return tailor(job, self._load_facts(), questions, self.llm, self.settings.llm.writing_model)

    def _load_facts(self) -> str:
        if self._facts is None:
            resume_text = read_document_text(self.root / self.profile.documents.resume)
            extras = [read_document_text(self.root / extra) for extra in self.profile.documents.extras]
            self._facts = build_facts(self.profile, resume_text, extras)
        return self._facts

    # ---------- resultado ----------

    def _dry_run(self, app_id: int, row: sqlite3.Row, page: Page, plan: FillPlan, summary: ApplySummary) -> None:
        # Alguns formulários sobem o arquivo assim que ele é escolhido; em dry-run nada pode sair da máquina.
        problems = fill_form(page, plan, skip_kinds=("file",))
        if problems:
            self._block(app_id, row, "; ".join(problems), summary, plan)
            return
        self._screenshot(page, app_id, "formulario.png")
        db.set_application_status(self.conn, app_id, "dry_run")
        db.log_event(self.conn, "application_dry_run", application_id=app_id, job_id=row["id"], payload=self._audit(row, plan))
        summary.dry_run += 1

    def _ask_approval(self, app_id: int, row: sqlite3.Row, page: Page, plan: FillPlan, tailored: Tailored,
                      summary: ApplySummary) -> None:
        if self.confirm is not None and self.confirm(self._preview(row, plan, tailored)):
            self._send(app_id, row, page, plan, summary)
            return
        db.set_application_status(self.conn, app_id, "pending_approval")
        db.log_event(self.conn, "application_pending_approval", application_id=app_id, job_id=row["id"],
                     payload=self._audit(row, plan))
        summary.pending_approval += 1

    def _send(self, app_id: int, row: sqlite3.Row, page: Page, plan: FillPlan, summary: ApplySummary) -> None:
        problems = fill_form(page, plan)
        if problems:
            self._block(app_id, row, "; ".join(problems), summary, plan)
            return
        if not db.claim_for_submission(self.conn, app_id):
            db.log(self.conn, "warning", "apply", f"candidatura {app_id} já foi reservada ou enviada; nada foi feito")
            return
        # Registrado antes do clique: se o processo cair aqui, a auditoria ainda mostra a tentativa.
        db.log_event(self.conn, "application_submit_attempt", application_id=app_id, job_id=row["id"], payload=self._audit(row, plan))
        result = submit_form(page, timeout_ms=self.submit_timeout_ms)
        self._screenshot(page, app_id, "envio.png")
        if result.status == "confirmed":
            db.mark_submitted(self.conn, app_id)
            db.complete_action_items(self.conn, app_id, ["form_blocked"])
            db.log_event(self.conn, "application_submitted", application_id=app_id, job_id=row["id"], payload=self._audit(row, plan))
            summary.submitted += 1
        elif result.status == "uncertain":
            reason = "o site não confirmou o envio; confira pelo link antes de tentar de novo"
            db.set_application_status(self.conn, app_id, "submit_uncertain", reason)
            db.add_action_item(self.conn, app_id, "submit_uncertain", f"Conferir envio: {row['title']}", link=row["url"])
            db.log_event(self.conn, "application_submit_uncertain", application_id=app_id, job_id=row["id"], payload=self._audit(row, plan))
            summary.uncertain += 1
        else:
            self._block(app_id, row, result.detail, summary, plan)

    def _block(self, app_id: int, row: sqlite3.Row, reason: str, summary: ApplySummary, plan: FillPlan | None = None,
               tailored: Tailored | None = None) -> None:
        db.set_application_status(self.conn, app_id, "blocked", reason)
        db.add_action_item(self.conn, app_id, "form_blocked", f"Candidatura parada: {row['title']}", link=row["url"])
        db.log_event(self.conn, "application_blocked", application_id=app_id, job_id=row["id"], payload={"reason": reason})
        if tailored:
            self._write_pack(app_id, row, tailored, plan or FillPlan(), reason)
        summary.blocked += 1

    # ---------- apoio ----------

    def _require_real_profile(self) -> None:
        if self.profile.is_example:
            raise ConfigError("profile.yaml ainda tem os dados de exemplo; envios ficam bloqueados")

    def _browser(self) -> BrowserSession:
        if self._session is None:
            self._session = BrowserSession(self.root / "data" / "browser_profile", headless=self.settings.browser.headless)
            self._session.__enter__()
        return self._session

    def _close_browser(self) -> None:
        if self._session is not None:
            self._session.__exit__(None, None, None)
            self._session = None

    def _app_dir(self, app_id: int) -> Path:
        path = self.root / "data" / "applications" / str(app_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _screenshot(self, page: Page, app_id: int, name: str) -> None:
        try:
            page.screenshot(path=str(self._app_dir(app_id) / name), full_page=True)
        except PlaywrightError as exc:
            db.log(self.conn, "warning", "apply", f"sem captura de tela da candidatura {app_id}: {_first_line(exc)}")

    def _audit(self, row: sqlite3.Row, plan: FillPlan) -> dict:
        return {"title": row["title"], "company": row["company"], "url": row["url"],
                "fields": _audit_fields(plan), "skipped": plan.skipped}

    def _stored_plan(self, plan: FillPlan, tailored: Tailored) -> dict:
        return {"fields": _audit_fields(plan), "blockers": plan.blockers, "skipped": plan.skipped,
                "tailored": tailored.model_dump()}

    def _preview(self, row: sqlite3.Row, plan: FillPlan, tailored: Tailored) -> str:
        lines = [f"Vaga: {row['title']} ({row['company']})", f"Link: {row['url']}", f"Score: {row['score']}", "", "Campos:"]
        lines += [f"  {field['label']}: {field['value']}" for field in _audit_fields(plan)]
        if plan.skipped:
            lines += ["", "Deixados em branco:", *(f"  {label}" for label in plan.skipped)]
        lines += ["", "Carta:", tailored.cover_letter]
        return "\n".join(lines)

    def _write_pack(self, app_id: int, row: sqlite3.Row, tailored: Tailored, plan: FillPlan, reason: str) -> None:
        lines = [
            f"# {row['title']} ({row['company']})",
            "",
            f"- Link: {row['url']}",
            f"- Score: {row['score']} ({row['score_reason']})",
            f"- Situação: {reason}",
            f"- Depois de enviar, rode: jobbot done {app_id}",
            "",
            "## Carta de apresentação",
            "",
            tailored.cover_letter,
        ]
        answered = [answer for answer in tailored.answers if answer.can_answer]
        if answered:
            lines += ["", "## Respostas prontas", ""]
            lines += [f"- **{answer.question}** {answer.answer}" for answer in answered]
        if plan.blockers:
            lines += ["", "## Só você pode responder", ""]
            lines += [f"- {blocker}" for blocker in plan.blockers]
        (self._app_dir(app_id) / "pacote.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def recover_interrupted(conn: sqlite3.Connection, max_age: timedelta = timedelta(hours=1)) -> int:
    """Envio preso em 'submitting' (o processo caiu no meio) vira incerto: o usuário confere, nunca é repetido."""
    limit = datetime.now().astimezone() - max_age
    recovered = 0
    for app in db.applications_by_status(conn, "submitting"):
        if datetime.fromisoformat(app["updated_at"]) > limit:
            continue  # pode ser um envio acontecendo agora
        db.set_application_status(conn, app["id"], "submit_uncertain", "o envio foi interrompido; confira pelo link")
        db.add_action_item(conn, app["id"], "submit_uncertain", f"Conferir envio: {app['title']}", link=app["url"])
        db.log_event(conn, "application_submit_interrupted", application_id=app["id"], job_id=app["job_id"])
        recovered += 1
    return recovered


def reject_application(conn: sqlite3.Connection, app_id: int) -> None:
    app = db.get_application(conn, app_id)
    if app is None:
        raise ConfigError(f"candidatura {app_id} não existe")
    if app["status"] == "submitted":
        raise ConfigError(f"candidatura {app_id} já foi enviada")
    db.set_application_status(conn, app_id, "rejected_by_user")
    db.complete_action_items(conn, app_id, ["form_blocked", "submit_uncertain"])
    db.log_event(conn, "application_rejected_by_user", application_id=app_id, job_id=app["job_id"])


def mark_done(conn: sqlite3.Connection, app_id: int) -> None:
    """O usuário enviou à mão ou confirmou um envio incerto."""
    app = db.get_application(conn, app_id)
    if app is None:
        raise ConfigError(f"candidatura {app_id} não existe")
    if app["status"] == "submitted":
        raise ConfigError(f"candidatura {app_id} já consta como enviada")
    db.mark_submitted(conn, app_id, mode="manual")
    db.complete_action_items(conn, app_id, ["form_blocked", "submit_uncertain"])
    db.log_event(conn, "application_marked_done", application_id=app_id, job_id=app["job_id"])
