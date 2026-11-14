from datetime import date

import pytest

from jobbot import db
from jobbot.cli import main
from jobbot.models import Job
from jobbot.notify.report import collect, render_report, render_summary, write_report

TODAY = date.today()


@pytest.fixture
def conn(project):
    connection = db.connect(project / "data" / "jobbot.db")
    yield connection
    connection.close()


def _job(conn, n, title="Estágio Dev", company="Acme", source="lever", apply_path="form", score=None, status="new"):
    job_id, _ = db.upsert_job(conn, Job(title=f"{title} {n}", company=company, url=f"https://a.io/{n}", source=source,
                                        apply_path=apply_path))
    if score is not None:
        db.set_score(conn, job_id, score, "boa aderência", status)
    return job_id


def _app(conn, n, status, **job):
    app_id = db.create_application(conn, _job(conn, n, **job), "auto")
    db.set_application_status(conn, app_id, status, "motivo do bloqueio" if status == "blocked" else None)
    return app_id


def _report(conn, project):
    return render_report(collect(conn, TODAY, min_score=70), project)


def test_empty_day_has_every_section_and_says_nothing_pending(conn, project):
    text = _report(conn, project)
    assert text.startswith(f"# jobbot: {TODAY.isoformat()}")
    for title in ("Resumo", "Pendências suas", "Para você enviar", "Aguardando sua aprovação", "Candidaturas paradas",
                  "Novidades nos processos", "Enviadas hoje", "Vagas novas", "Erros e avisos"):
        assert f"## {title}" in text
    assert "Nada pendente" in text


def test_metrics_count_new_jobs_and_submissions(conn, project):
    _job(conn, 1)
    _job(conn, 2, source="greenhouse")
    app_id = _app(conn, 3, "draft")
    db.mark_submitted(conn, app_id)
    text = _report(conn, project)
    assert "Vagas novas hoje: 3 (greenhouse: 1, lever: 2)" in text
    assert "Candidaturas enviadas: 1 hoje, 1 no total" in text


def test_pending_actions_show_deadline_link_prep_and_command(conn, project):
    app_id = _app(conn, 1, "submitted")
    item = db.add_action_item(conn, app_id, "test", "Fazer teste: Estágio Dev 1", link="https://hackerrank.com/t",
                              due_date="2999-12-31")
    db.set_action_prep(conn, item, "reports/prep/1-test.md")
    text = _report(conn, project)
    assert "2999-12-31" in text
    assert "https://hackerrank.com/t" in text
    assert "(prep/1-test.md)" in text
    assert f"jobbot resolve {item}" in text


def test_blocked_applications_are_not_repeated_as_pending_actions(conn, project):
    app_id = _app(conn, 1, "blocked")
    item = db.add_action_item(conn, app_id, "form_blocked", "Candidatura parada: Estágio Dev 1")
    text = _report(conn, project)
    pending = text.split("## Pendências suas")[1].split("## Para você enviar")[0]
    assert "Nada pendente" in pending
    assert f"jobbot resolve {item}" not in text
    assert "motivo do bloqueio" in text


def test_overdue_and_close_deadlines_are_flagged(conn, project):
    first = _app(conn, 1, "submitted")
    second = _app(conn, 2, "submitted")
    db.add_action_item(conn, first, "test", "Teste antigo", due_date="2000-01-01")
    db.add_action_item(conn, second, "interview", "Entrevista hoje", due_date=TODAY.isoformat())
    text = _report(conn, project)
    assert "ATRASADA" in text
    assert "URGENTE" in text


def test_manual_applications_show_link_pack_and_done_command(conn, project):
    app_id = _app(conn, 1, "ready_manual", source="linkedin", apply_path="manual")
    pack = project / "data" / "applications" / str(app_id) / "pacote.md"
    pack.parent.mkdir(parents=True)
    pack.write_text("x", encoding="utf-8")
    text = _report(conn, project)
    assert "https://a.io/1" in text
    assert f"(../data/applications/{app_id}/pacote.md)" in text
    assert f"jobbot done {app_id}" in text


def test_approval_lists_pending_and_todays_previews(conn, project):
    pending = _app(conn, 1, "pending_approval")
    preview = _app(conn, 2, "dry_run")
    old = _app(conn, 3, "dry_run")
    conn.execute("UPDATE applications SET updated_at = '2000-01-01T00:00:00' WHERE id = ?", (old,))
    text = _report(conn, project)
    assert f"jobbot approve {pending}" in text
    assert f"jobbot approve {preview}" in text
    assert f"jobbot approve {old}" not in text
    assert "1 prévia(s) de dias anteriores" in text


def test_blocked_and_uncertain_show_reason(conn, project):
    _app(conn, 1, "blocked")
    _app(conn, 2, "submit_uncertain")
    text = _report(conn, project)
    assert "motivo do bloqueio" in text
    assert "jobbot apply --retry-blocked" in text
    assert "confira" in text.lower()


def test_process_news_lists_stages_detected_today(conn, project):
    app_id = _app(conn, 1, "submitted")
    db.log_event(conn, "stage_detected", application_id=app_id, payload={"stage": "rejected", "subject": "Retorno"})
    db.log_event(conn, "job_closed", application_id=app_id, payload={"url": "https://a.io/1"})
    text = _report(conn, project)
    assert "Recusa" in text
    assert "Vaga encerrada" in text
    assert "Estágio Dev 1 (Acme)" in text


def test_unverified_news_asks_the_user_to_check(conn, project):
    app_id = _app(conn, 1, "submitted")
    db.log_event(conn, "stage_unverified", application_id=app_id,
                 payload={"stage": "interview", "subject": "Convite", "from": "x@gmail.com"})
    text = _report(conn, project)
    assert "Entrevista?" in text
    assert "x@gmail.com" in text


def test_new_approved_jobs_are_listed_with_score(conn, project):
    _job(conn, 1, score=85, status="scored")
    _job(conn, 2, score=40, status="low_score")
    text = _report(conn, project)
    assert "85" in text and "Estágio Dev 1" in text
    assert "Estágio Dev 2" not in text


def test_errors_and_warnings_of_the_day(conn, project):
    db.log(conn, "error", "search.lever", "lever fora do ar")
    db.log(conn, "warning", "track", "e-mail ambíguo")
    text = _report(conn, project)
    assert "lever fora do ar" in text
    assert "e-mail ambíguo" in text


def test_summary_is_short_and_lists_what_needs_the_user(conn, project):
    app_id = _app(conn, 1, "submitted")
    db.add_action_item(conn, app_id, "test", "Fazer teste: Estágio Dev 1", due_date="2999-12-31")
    _app(conn, 2, "ready_manual", source="linkedin", apply_path="manual")
    summary = render_summary(collect(conn, TODAY, min_score=70), project / "reports" / "x.md")
    assert "Fazer teste: Estágio Dev 1" in summary
    assert "2999-12-31" in summary
    assert "Para você enviar: 1" in summary
    assert len(summary) < 3500


def test_write_report_creates_dated_file(conn, project):
    path, _ = write_report(conn, project, TODAY, min_score=70)
    assert path == project / "reports" / f"{TODAY.isoformat()}.md"
    assert path.read_text(encoding="utf-8").startswith("# jobbot")


def test_report_command_writes_file_and_prints_path(project, capsys):
    assert main(["report"]) == 0
    assert f"{TODAY.isoformat()}.md" in capsys.readouterr().out
    assert (project / "reports" / f"{TODAY.isoformat()}.md").exists()


def test_new_approved_job_links_its_evaluation(conn, project):
    job_id = _job(conn, 1, score=85, status="scored")
    (project / "reports" / "vagas").mkdir(parents=True)
    (project / "reports" / "vagas" / f"{job_id}.md").write_text("avaliação", encoding="utf-8")
    assert f"[avaliação](vagas/{job_id}.md)" in _report(conn, project)


def test_follow_up_shows_draft_link_and_no_response_is_news(conn, project):
    app_id = _app(conn, 1, "submitted")
    item = db.add_action_item(conn, app_id, "follow_up", "Follow-up 1: Estágio Dev 1", due_date=TODAY.isoformat())
    db.set_action_prep(conn, item, "reports/followups/1-1.md")
    db.log_event(conn, "no_response", application_id=app_id, job_id=1, payload={"stage": "no_response"})
    text = _report(conn, project)
    assert "[rascunho da mensagem](followups/1-1.md)" in text
    assert "- Sem resposta: Estágio Dev 1 (Acme)" in text


def test_funnel_shows_response_rates_by_source_and_score(conn, project):
    for n, (source, score, stage) in enumerate([("lever", 75, None), ("lever", 92, "interview"),
                                                ("greenhouse", 85, "rejected"), ("greenhouse", 95, "no_response")], 1):
        app_id = db.create_application(conn, _job(conn, n, source=source, score=score, status="scored"), "auto")
        db.mark_submitted(conn, app_id)
        if stage:
            db.add_stage(conn, app_id, stage, "email")
    text = _report(conn, project)
    assert "## Funil" in text
    assert "Enviadas: 4 | com resposta: 2 (50%)" in text
    assert "entrevistas e testes: 1 | propostas: 0 | recusas: 1 | sem resposta: 1" in text
    assert "greenhouse: 1 de 2" in text and "lever: 1 de 2" in text
    assert "70-79: 0 de 1" in text and "80-89: 1 de 1" in text and "90+: 1 de 2" in text


def test_funnel_does_not_count_acknowledgment_or_late_answer_as_no_response(conn, project):
    for n, stages in enumerate([["screening"], ["no_response", "interview"]], 1):
        app_id = db.create_application(conn, _job(conn, n, score=80, status="scored"), "auto")
        db.mark_submitted(conn, app_id)
        for stage in stages:
            db.add_stage(conn, app_id, stage, "email")
    text = _report(conn, project)
    assert "Enviadas: 2 | com resposta: 1 (50%)" in text
    assert "sem resposta: 0" in text


def test_job_closed_before_sending_is_news_with_the_link(conn, project):
    app_id = _app(conn, 1, "job_closed")
    db.log_event(conn, "application_job_closed", application_id=app_id, payload={"url": "https://a.io/1"})
    text = _report(conn, project)
    assert "- Vaga encerrada antes do envio: Estágio Dev 1 (Acme), confira https://a.io/1" in text
