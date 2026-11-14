"""Fluxo completo de candidatura: banco real, navegador real e site local. Só o Claude é simulado."""

from datetime import date

import pytest
import yaml

from conftest import write_docx
from form_site import FormSite
from jobbot import db
from jobbot.apply.base import Applier
from jobbot.apply.text_tailor import Claim, QuestionAnswer, Tailored
from jobbot.llm import LLMError
from jobbot.models import Job
from jobbot.profile import ConfigError, load_profile, load_settings

pytestmark = pytest.mark.browser

EVIDENCE = "Projeto X em Python"


@pytest.fixture(scope="module")
def site():
    server = FormSite()
    yield server
    server.close()


@pytest.fixture(autouse=True)
def clear_posts(site):
    site.posts.clear()


@pytest.fixture
def real_project(project):
    path = project / "config/profile.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["personal"]["email"] = "pessoa@real.com"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return project


@pytest.fixture
def conn(project):
    connection = db.connect(project / "data" / "jobbot.db")
    yield connection
    connection.close()


class FakeLLM:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = 0

    def ask(self, prompt, schema, model, system=None):
        self.calls += 1
        result = self.results.pop(0) if self.results else _tailored()
        if isinstance(result, Exception):
            raise result
        return result


def _tailored(answers=()):
    return Tailored(
        cover_letter="Fiz o Projeto X em Python.",
        claims=[Claim(text="Projeto X", evidence=EVIDENCE)],
        answers=list(answers),
    )


def _classic_answers():
    def answer(question, value):
        return QuestionAnswer(question=question, can_answer=True, answer=value, evidence=EVIDENCE)

    return [
        answer("Conte sobre um projeto ✱", "Fiz o Projeto X"),
        answer("Já programou em Python? ✱", "Sim"),
        answer("Nível de inglês ✱", "Avançado"),
    ]


def _add_job(conn, url, score=90, n=1, **over):
    base = dict(title=f"Estágio {n}", company="Acme", location="São Paulo", url=url, source="greenhouse", description="Python")
    job_id, _ = db.upsert_job(conn, Job(**{**base, **over}))
    db.set_score(conn, job_id, score, "aderente", "scored")
    return job_id


def _applier(conn, root, llm=None, confirm=None, submit_timeout_ms=3000):
    return Applier(conn, load_settings(root), load_profile(root), root, llm or FakeLLM(), confirm=confirm,
                   submit_timeout_ms=submit_timeout_ms)


def _application(conn, job_id):
    return db.get_application_by_job(conn, job_id)


def test_dry_run_fills_but_never_posts(conn, project, site):
    job_id = _add_job(conn, site.url("/simple"))
    summary = _applier(conn, project).run("dry-run")
    app = _application(conn, job_id)
    assert summary.dry_run == 1
    assert app["status"] == "dry_run"
    assert site.posts == []
    assert (project / "data" / "applications" / str(app["id"]) / "formulario.png").exists()


def test_dry_run_never_uploads_the_resume(conn, project, site):
    _add_job(conn, site.url("/auto-upload"))
    _applier(conn, project).run("dry-run")
    assert site.posts == []


def test_real_submission_attaches_the_resume(conn, real_project, site):
    _add_job(conn, site.url("/auto-upload"))
    _applier(conn, real_project).run("auto")
    assert "/upload" in [path for path, _ in site.posts]


def test_dry_run_records_what_would_be_sent(conn, project, site):
    job_id = _add_job(conn, site.url("/simple"))
    _applier(conn, project).run("dry-run")
    event = db.get_events(conn, "application_dry_run")[0]
    assert event["application_id"] == _application(conn, job_id)["id"]
    assert {"label": "Full name ✱", "value": "Seu Nome Completo", "origin": "perfil"} in event["payload"]["fields"]


def test_second_dry_run_does_not_spend_llm_again(conn, project, site):
    _add_job(conn, site.url("/simple"))
    llm = FakeLLM()
    _applier(conn, project, llm).run("dry-run")
    _applier(conn, project, llm).run("dry-run")
    assert llm.calls == 1


def test_auto_submits_form_with_answers(conn, real_project, site):
    job_id = _add_job(conn, site.url("/classic"))
    llm = FakeLLM(_tailored(_classic_answers()))
    summary = _applier(conn, real_project, llm).run("auto")
    assert summary.submitted == 1
    assert len(site.posts) == 1
    body = site.posts[0][1].decode("utf-8", "replace")
    assert "pessoa@real.com" in body and "Fiz o Projeto X" in body
    app = _application(conn, job_id)
    assert app["status"] == "submitted"
    assert [s["stage"] for s in db.get_stages(conn, app["id"])] == ["applied"]
    assert db.get_events(conn, "application_submitted")[0]["application_id"] == app["id"]


def test_auto_never_applies_twice_to_same_job(conn, real_project, site):
    _add_job(conn, site.url("/simple"))
    _applier(conn, real_project).run("auto")
    _applier(conn, real_project).run("auto")
    _applier(conn, real_project).run("auto")
    assert len(site.posts) == 1


def test_same_job_from_two_sources_is_applied_once(conn, real_project, site):
    _add_job(conn, site.url("/simple"))
    _add_job(conn, site.url("/simple2"), source="lever")
    _applier(conn, real_project).run("auto")
    assert len(site.posts) == 1


def test_low_score_job_is_not_applied(conn, real_project, site):
    job_id = _add_job(conn, site.url("/simple"), score=69)
    _applier(conn, real_project).run("auto")
    assert site.posts == []
    assert _application(conn, job_id) is None


def test_daily_limit_stops_submissions_and_prefers_best_score(conn, real_project, site):
    (real_project / "config/settings.yaml").write_text("daily_application_limit: 1\n", encoding="utf-8")
    weaker = _add_job(conn, site.url("/simple"), score=75, n=1)
    better = _add_job(conn, site.url("/simple2"), score=95, n=2)
    summary = _applier(conn, real_project).run("auto")
    assert len(site.posts) == 1
    assert site.posts[0][0] == "/submit"
    assert _application(conn, better)["status"] == "submitted"
    assert _application(conn, weaker) is None
    assert summary.skipped_by_limit == 1


def test_daily_limit_counts_earlier_submissions_today(conn, real_project, site):
    (real_project / "config/settings.yaml").write_text("daily_application_limit: 1\n", encoding="utf-8")
    _add_job(conn, site.url("/simple"), n=1)
    _applier(conn, real_project).run("auto")
    _add_job(conn, site.url("/simple2"), n=2)
    _applier(conn, real_project).run("auto")
    assert len(site.posts) == 1
    assert db.submissions_on(conn, date.today()) == 1


@pytest.mark.parametrize("mode", ["auto", "approval"])
def test_example_profile_cannot_send(conn, project, site, mode):
    _add_job(conn, site.url("/simple"))
    with pytest.raises(ConfigError, match="exemplo"):
        _applier(conn, project).run(mode)
    assert site.posts == []


def test_approval_without_terminal_waits_and_sends_nothing(conn, real_project, site):
    job_id = _add_job(conn, site.url("/simple"))
    summary = _applier(conn, real_project).run("approval")
    assert summary.pending_approval == 1
    assert _application(conn, job_id)["status"] == "pending_approval"
    assert site.posts == []


def test_approve_sends_once(conn, real_project, site):
    job_id = _add_job(conn, site.url("/simple"))
    _applier(conn, real_project).run("approval")
    app_id = _application(conn, job_id)["id"]
    assert _applier(conn, real_project).approve(app_id) == "submitted"
    assert len(site.posts) == 1
    with pytest.raises(ConfigError):
        _applier(conn, real_project).approve(app_id)
    assert len(site.posts) == 1


def test_approve_refuses_example_profile(conn, project, site):
    job_id = _add_job(conn, site.url("/simple"))
    _applier(conn, project).run("dry-run")
    with pytest.raises(ConfigError, match="exemplo"):
        _applier(conn, project).approve(_application(conn, job_id)["id"])
    assert site.posts == []


def test_approve_respects_daily_limit(conn, real_project, site):
    (real_project / "config/settings.yaml").write_text("daily_application_limit: 1\n", encoding="utf-8")
    first = _add_job(conn, site.url("/simple"), n=1)
    second = _add_job(conn, site.url("/simple2"), n=2)
    applier = _applier(conn, real_project)
    applier.run("approval")
    db.set_application_status(conn, db.create_application(conn, second, "approval"), "pending_approval")
    applier.approve(_application(conn, first)["id"])
    with pytest.raises(ConfigError, match="limite"):
        applier.approve(_application(conn, second)["id"])
    assert len(site.posts) == 1


def test_terminal_confirmation_yes_sends(conn, real_project, site):
    job_id = _add_job(conn, site.url("/simple"))
    _applier(conn, real_project, confirm=lambda preview: True).run("approval")
    assert _application(conn, job_id)["status"] == "submitted"
    assert len(site.posts) == 1


def test_terminal_confirmation_no_keeps_pending(conn, real_project, site):
    job_id = _add_job(conn, site.url("/simple"))
    _applier(conn, real_project, confirm=lambda preview: False).run("approval")
    assert _application(conn, job_id)["status"] == "pending_approval"
    assert site.posts == []


def test_confirmation_shows_the_values_to_be_sent(conn, real_project, site):
    _add_job(conn, site.url("/simple"))
    seen = []
    _applier(conn, real_project, confirm=lambda preview: seen.append(preview) or False).run("approval")
    assert "Seu Nome Completo" in seen[0]
    assert "Fiz o Projeto X em Python." in seen[0]


@pytest.mark.parametrize("path,word", [("/visible-captcha", "CAPTCHA"), ("/cloudflare", "CAPTCHA"), ("/login", "login")])
def test_obstacle_blocks_without_llm_and_asks_for_help(conn, real_project, site, path, word):
    job_id = _add_job(conn, site.url(path))
    llm = FakeLLM()
    summary = _applier(conn, real_project, llm).run("auto")
    app = _application(conn, job_id)
    assert summary.blocked == 1
    assert app["status"] == "blocked"
    assert word in app["reason"]
    assert llm.calls == 0
    assert site.posts == []
    item = db.open_action_items(conn)[0]
    assert item["kind"] == "form_blocked"
    assert item["link"] == site.url(path)


def test_page_without_form_is_blocked(conn, real_project, site):
    job_id = _add_job(conn, site.url("/no-form"))
    _applier(conn, real_project).run("auto")
    assert "formulário" in _application(conn, job_id)["reason"]


def test_sensitive_question_blocks_and_leaves_material_ready(conn, real_project, site):
    job_id = _add_job(conn, site.url("/sensitive"))
    _applier(conn, real_project).run("auto")
    app = _application(conn, job_id)
    assert app["status"] == "blocked"
    assert "CPF" in app["reason"]
    assert site.posts == []
    pack = (real_project / "data" / "applications" / str(app["id"]) / "pacote.md").read_text(encoding="utf-8")
    assert "Fiz o Projeto X em Python." in pack
    assert "CPF" in pack
    assert site.url("/sensitive") in pack


def test_saved_answer_is_sent_but_masked_in_audit(conn, real_project, site):
    path = real_project / "config/profile.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["saved_answers"] = [{"match": "CPF", "answer": "111.222.333-44"}]
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    job_id = _add_job(conn, site.url("/sensitive"))
    _applier(conn, real_project).run("auto")
    assert b"111.222.333-44" in site.posts[0][1]
    event = db.get_events(conn, "application_submitted")[0]
    assert "111.222.333-44" not in str(event["payload"])
    assert "111.222.333-44" not in _application(conn, job_id)["plan_json"]


def test_uncertain_submission_is_flagged_and_never_retried(conn, real_project, site):
    job_id = _add_job(conn, site.url("/classic-silent"))
    llm = FakeLLM(_tailored(_classic_answers()))
    _applier(conn, real_project, llm, submit_timeout_ms=500).run("auto")
    app = _application(conn, job_id)
    assert app["status"] == "submit_uncertain"
    assert db.open_action_items(conn)[0]["kind"] == "submit_uncertain"
    _applier(conn, real_project, llm, submit_timeout_ms=500).run("auto", retry_blocked=True)
    assert len(site.posts) == 1


def test_browser_failure_after_click_is_uncertain_and_never_retried(conn, real_project, site, monkeypatch):
    from playwright.sync_api import Error as PlaywrightError

    from jobbot.apply import form_filler

    def click_then_crash(page, timeout_ms=0):
        form_filler._find_submit_button(page).click()
        page.wait_for_load_state()
        raise PlaywrightError("Target page, context or browser has been closed")

    job_id = _add_job(conn, site.url("/simple"))
    monkeypatch.setattr("jobbot.apply.base.submit_form", click_then_crash)
    _applier(conn, real_project).run("auto")
    monkeypatch.undo()
    assert _application(conn, job_id)["status"] == "submit_uncertain"
    assert db.open_action_items(conn)[0]["kind"] == "submit_uncertain"
    _applier(conn, real_project).run("auto", retry_blocked=True)
    assert len(site.posts) == 1


def test_invalid_submission_blocks_and_can_be_retried(conn, real_project, site):
    job_id = _add_job(conn, site.url("/classic-invalid"))
    answers = _tailored(_classic_answers())
    _applier(conn, real_project, FakeLLM(answers)).run("auto")
    assert _application(conn, job_id)["status"] == "blocked"
    assert len(site.posts) == 1
    site.responses["/invalid"], original = site.responses["/submit"], site.responses["/invalid"]
    try:
        _applier(conn, real_project, FakeLLM(answers)).run("auto", retry_blocked=True)
    finally:
        site.responses["/invalid"] = original
    assert _application(conn, job_id)["status"] == "submitted"
    assert len(site.posts) == 2


def test_blocked_job_is_not_retried_without_asking(conn, real_project, site):
    _add_job(conn, site.url("/sensitive"))
    llm = FakeLLM()
    _applier(conn, real_project, llm).run("auto")
    calls = llm.calls
    _applier(conn, real_project, llm).run("auto")
    assert llm.calls == calls


def test_fabricated_text_blocks_submission(conn, real_project, site):
    job_id = _add_job(conn, site.url("/simple"))
    fake = Tailored(cover_letter="Fui CTO", claims=[Claim(text="CTO", evidence="CTO no Google")])
    _applier(conn, real_project, FakeLLM(fake, fake)).run("auto")
    app = _application(conn, job_id)
    assert app["status"] == "blocked"
    assert "CTO no Google" in app["reason"]
    assert site.posts == []


def test_llm_failure_blocks_without_submitting(conn, real_project, site):
    job_id = _add_job(conn, site.url("/simple"))
    _applier(conn, real_project, FakeLLM(LLMError("limite"))).run("auto")
    assert _application(conn, job_id)["status"] == "blocked"
    assert site.posts == []


def test_question_the_llm_cannot_answer_blocks(conn, real_project, site):
    job_id = _add_job(conn, site.url("/classic"))
    answers = _classic_answers()
    answers[0] = QuestionAnswer(question="Conte sobre um projeto ✱", can_answer=False)
    _applier(conn, real_project, FakeLLM(_tailored(answers))).run("auto")
    assert "Conte sobre um projeto" in _application(conn, job_id)["reason"]
    assert site.posts == []


def test_manual_source_prepares_material_without_opening_browser(conn, real_project, monkeypatch):
    def no_browser(*args, **kwargs):
        raise AssertionError("não deveria abrir o navegador")

    monkeypatch.setattr("jobbot.apply.base.BrowserSession", no_browser)
    job_id = _add_job(conn, "https://www.linkedin.com/jobs/view/1", source="linkedin", apply_path="manual")
    summary = _applier(conn, real_project).run("auto")
    app = _application(conn, job_id)
    assert summary.manual == 1
    assert app["status"] == "ready_manual"
    pack = (real_project / "data" / "applications" / str(app["id"]) / "pacote.md").read_text(encoding="utf-8")
    assert "Fiz o Projeto X em Python." in pack
    assert "https://www.linkedin.com/jobs/view/1" in pack


def test_dry_run_is_promoted_later_without_new_llm_call(conn, real_project, site):
    job_id = _add_job(conn, site.url("/simple"))
    llm = FakeLLM()
    _applier(conn, real_project, llm).run("dry-run")
    _applier(conn, real_project, llm).run("auto")
    assert llm.calls == 1
    assert _application(conn, job_id)["status"] == "submitted"
    assert len(site.posts) == 1


def test_browser_error_on_one_job_does_not_stop_the_next(conn, real_project, site):
    broken = _add_job(conn, "http://127.0.0.1:1/fechado", score=99, n=1)
    good = _add_job(conn, site.url("/simple"), score=80, n=2)
    _applier(conn, real_project).run("auto")
    assert _application(conn, broken)["status"] == "blocked"
    assert _application(conn, good)["status"] == "submitted"


ENGLISH = "We are looking for an intern to join our team. You will work with Python and the experience is great."


def _with_english_resume(project):
    write_docx(project / "documents" / "cv-en.docx", "Project X in Python")
    path = project / "config/profile.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["documents"]["resume_en"] = "documents/cv-en.docx"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


class RecordingLLM(FakeLLM):
    def __init__(self, result):
        super().__init__(result)
        self.prompts = []

    def ask(self, prompt, schema, model, system=None):
        self.prompts.append(prompt)
        return super().ask(prompt, schema, model, system)


def test_english_job_uses_english_resume_for_upload_and_facts(conn, project, site):
    _with_english_resume(project)
    _add_job(conn, site.url("/auto-upload"), description=ENGLISH)
    english = Tailored(cover_letter="I built Project X in Python.", claims=[Claim(text="x", evidence="Project X in Python")])
    llm = RecordingLLM(english)
    _applier(conn, project, llm).run("dry-run")
    fields = db.get_events(conn, "application_dry_run")[0]["payload"]["fields"]
    assert any(f["origin"] == "curriculo" and f["value"].endswith("cv-en.docx") for f in fields)
    assert "Project X in Python" in llm.prompts[0]
    assert "Projeto X em Python" not in llm.prompts[0]


def test_portuguese_job_keeps_main_resume(conn, project, site):
    _with_english_resume(project)
    _add_job(conn, site.url("/auto-upload"), description="Buscamos uma pessoa para atuar com Python no time de dados.")
    _applier(conn, project).run("dry-run")
    fields = db.get_events(conn, "application_dry_run")[0]["payload"]["fields"]
    assert any(f["origin"] == "curriculo" and f["value"].endswith("curriculo.docx") for f in fields)
