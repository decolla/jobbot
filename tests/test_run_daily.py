import shutil
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from jobbot import db, pipeline
from jobbot.apply.base import recover_interrupted
from jobbot.cli import main
from jobbot.models import Job
from jobbot.sources.base import PoliteClient

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def offline(monkeypatch):
    """Sem fontes, sem Claude e sem rede: só a orquestração do run-daily."""
    monkeypatch.setattr(pipeline, "build_enabled_sources", lambda settings: [])
    monkeypatch.setattr(pipeline, "build_llm", lambda settings: None)
    transport = httpx.MockTransport(lambda request: httpx.Response(404))
    monkeypatch.setattr(pipeline, "build_http", lambda settings: PoliteClient(0, 0, transport=transport, sleep=lambda _: None))


def test_run_daily_runs_every_step_and_writes_report(project, offline, capsys):
    assert main(["run-daily"]) == 0
    out = capsys.readouterr().out
    steps = ["== busca ==", "== pontuação ==", "== candidatura ==", "== acompanhamento ==", "== relatório =="]
    assert [out.index(step) for step in steps] == sorted(out.index(step) for step in steps)
    assert (project / "reports" / f"{date.today().isoformat()}.md").exists()


def test_failing_step_does_not_stop_the_others(project, offline, monkeypatch, capsys):
    def broken(*args, **kwargs):
        raise RuntimeError("pontuação quebrou")

    monkeypatch.setattr(pipeline, "run_score", broken)
    assert main(["run-daily"]) == 1
    captured = capsys.readouterr()
    assert "== acompanhamento ==" in captured.out
    assert "pontuação quebrou" in captured.err
    report = (project / "reports" / f"{date.today().isoformat()}.md").read_text(encoding="utf-8")
    assert "pontuação quebrou" in report


def test_run_daily_sends_notification_when_enabled(project, offline, monkeypatch):
    sent = []
    monkeypatch.setattr("jobbot.cli.notify", lambda settings, env, subject, text: sent.append(subject) or [])
    assert main(["run-daily"]) == 0
    assert sent and date.today().isoformat() in sent[0]


def test_notification_failure_is_reported_but_run_succeeds(project, offline, monkeypatch, capsys):
    monkeypatch.setattr("jobbot.cli.notify", lambda settings, env, subject, text: ["telegram: HTTP 500"])
    assert main(["run-daily"]) == 0
    assert "telegram: HTTP 500" in capsys.readouterr().out


def _submitting(conn, n, minutes_ago):
    job_id, _ = db.upsert_job(conn, Job(title=f"Vaga {n}", company="Acme", url=f"https://a.io/{n}", source="lever"))
    app_id = db.create_application(conn, job_id, "auto")
    db.claim_for_submission(conn, app_id)
    stamp = (datetime.now().astimezone() - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
    conn.execute("UPDATE applications SET updated_at = ? WHERE id = ?", (stamp, app_id))
    return app_id


def test_interrupted_submission_becomes_uncertain_never_retried(project):
    conn = db.connect(project / "data" / "jobbot.db")
    old = _submitting(conn, 1, minutes_ago=120)
    recent = _submitting(conn, 2, minutes_ago=1)
    assert recover_interrupted(conn) == 1
    assert db.get_application(conn, old)["status"] == "submit_uncertain"
    assert db.get_application(conn, recent)["status"] == "submitting"
    assert [item["kind"] for item in db.open_action_items(conn)] == ["submit_uncertain"]
    assert db.claim_for_submission(conn, old) is False
    conn.close()


def test_scheduler_examples_call_run_daily():
    for name in ("jobbot-daily.cron", "jobbot.service", "register_windows_task.ps1"):
        assert "run-daily" in (REPO / "src" / "jobbot" / "scheduler" / name).read_text(encoding="utf-8")
    assert "OnCalendar" in (REPO / "src" / "jobbot" / "scheduler" / "jobbot.timer").read_text(encoding="utf-8")


def test_windows_task_script_is_ascii_for_powershell_5():
    (REPO / "src" / "jobbot" / "scheduler" / "register_windows_task.ps1").read_bytes().decode("ascii")


@pytest.mark.skipif(shutil.which("powershell") is None, reason="PowerShell não disponível")
def test_windows_task_script_has_no_syntax_errors():
    script = REPO / "src" / "jobbot" / "scheduler" / "register_windows_task.ps1"
    command = (
        "$errors = $null; [System.Management.Automation.Language.Parser]::ParseFile("
        f"'{script}', [ref]$null, [ref]$errors) | Out-Null; $errors.Count"
    )
    result = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True, timeout=60)
    assert result.stdout.strip() == "0", result.stdout + result.stderr
