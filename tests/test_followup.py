"""Follow-up de candidaturas sem resposta: só rascunho e pendência, nunca envio."""

from datetime import date, datetime, timedelta

import pytest

from jobbot import db
from jobbot.actions.followup import plan_followups
from jobbot.models import Job
from jobbot.profile import load_profile, load_settings

TODAY = date(2026, 11, 14)


@pytest.fixture
def conn(project):
    connection = db.connect(project / "data" / "jobbot.db")
    yield connection
    connection.close()


def _submitted(conn, days_ago, n=1, description="Vaga de estágio com Python no nosso time de dados."):
    job_id, _ = db.upsert_job(conn, Job(title=f"Estágio Dev {n}", company="Acme", url=f"https://a.io/{n}",
                                        source="lever", description=description))
    app_id = db.create_application(conn, job_id, "auto")
    when = datetime.combine(TODAY - timedelta(days=days_ago), datetime.min.time()).astimezone()
    db.mark_submitted(conn, app_id, when=when)
    return app_id


def _items(conn, app_id):
    return conn.execute("SELECT * FROM action_items WHERE application_id = ? AND kind = 'follow_up' ORDER BY id",
                        (app_id,)).fetchall()


def _age_items(conn, app_id, days):
    stamp = (datetime.combine(TODAY - timedelta(days=days), datetime.min.time()).astimezone()).isoformat()
    conn.execute("UPDATE action_items SET created_at = ? WHERE application_id = ?", (stamp, app_id))


def _plan(conn, project, **over):
    settings = load_settings(project)
    for key, value in over.items():
        setattr(settings.followups, key, value)
    return plan_followups(conn, project, load_profile(project), settings, TODAY)


def test_week_without_answer_creates_follow_up_with_draft(conn, project):
    app_id = _submitted(conn, days_ago=8)
    summary = _plan(conn, project)
    (item,) = _items(conn, app_id)
    assert summary.created == 1
    assert item["due_date"] == TODAY.isoformat()
    draft = (project / item["prep_path"]).read_text(encoding="utf-8")
    assert "Estágio Dev 1" in draft and "Acme" in draft and "Seu Nome Completo" in draft
    assert "nunca é enviado" in draft.lower()


def test_recent_application_has_no_follow_up(conn, project):
    app_id = _submitted(conn, days_ago=3)
    _plan(conn, project)
    assert _items(conn, app_id) == []


def test_any_answer_means_no_follow_up(conn, project):
    app_id = _submitted(conn, days_ago=10)
    db.add_stage(conn, app_id, "screening", "email")
    _plan(conn, project)
    assert _items(conn, app_id) == []


def test_running_twice_does_not_duplicate(conn, project):
    app_id = _submitted(conn, days_ago=8)
    _plan(conn, project)
    _plan(conn, project)
    assert len(_items(conn, app_id)) == 1


def test_second_follow_up_only_after_first_is_done_and_a_week_passed(conn, project):
    app_id = _submitted(conn, days_ago=20)
    _plan(conn, project)
    db.complete_action_items(conn, app_id, ["follow_up"])
    _plan(conn, project)
    assert len(_items(conn, app_id)) == 1  # o primeiro acabou de ser feito
    _age_items(conn, app_id, 8)
    _plan(conn, project)
    assert len(_items(conn, app_id)) == 2


def test_after_max_follow_ups_application_is_marked_without_answer(conn, project):
    app_id = _submitted(conn, days_ago=30)
    for _ in range(2):
        _plan(conn, project)
        db.complete_action_items(conn, app_id, ["follow_up"])
        _age_items(conn, app_id, 8)
    summary = _plan(conn, project)
    assert summary.cold == 1
    assert len(_items(conn, app_id)) == 2
    assert "no_response" in [s["stage"] for s in db.get_stages(conn, app_id)]
    _plan(conn, project)
    assert [s["stage"] for s in db.get_stages(conn, app_id)].count("no_response") == 1


def test_disabled_follow_ups_do_nothing(conn, project):
    app_id = _submitted(conn, days_ago=8)
    _plan(conn, project, enabled=False)
    assert _items(conn, app_id) == []


def test_english_job_gets_english_draft(conn, project):
    english = "We are looking for an intern to join our team. You will work with Python and the experience is great."
    app_id = _submitted(conn, days_ago=8, description=english)
    _plan(conn, project)
    draft = (project / _items(conn, app_id)[0]["prep_path"]).read_text(encoding="utf-8")
    assert "I applied" in draft
