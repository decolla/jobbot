from jobbot import db
from jobbot.cli import main
from jobbot.models import Job


def test_config_problem_prints_message_instead_of_traceback(project, capsys):
    conn = db.connect(project / "data" / "jobbot.db")
    db.upsert_job(conn, Job(title="Estágio Desenvolvedor", company="A", location="São Paulo", url="https://a.io", source="x"))
    conn.close()
    (project / "documents/curriculo.docx").unlink()
    assert main(["score"]) == 1
    assert "curriculo.docx" in capsys.readouterr().err


def test_status_shows_mode_and_succeeds(project, capsys):
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "gupy" in out


def test_status_fails_when_setup_is_broken(project, capsys):
    (project / "documents/curriculo.docx").unlink()
    assert main(["status"]) == 1
    assert "curriculo.docx" in capsys.readouterr().out
