from jobbot.cli import main


def test_status_shows_mode_and_succeeds(project, capsys):
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "gupy" in out


def test_status_fails_when_setup_is_broken(project, capsys):
    (project / "documents/curriculo.docx").unlink()
    assert main(["status"]) == 1
    assert "curriculo.docx" in capsys.readouterr().out
