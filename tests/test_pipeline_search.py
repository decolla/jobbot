import pytest

from jobbot import db, pipeline
from jobbot.cli import main
from jobbot.models import Job
from jobbot.sources.base import SourceBlocked


class FakeSource:
    def __init__(self, name, jobs=(), error=None, warnings=()):
        self.name, self._jobs, self._error, self.warnings = name, list(jobs), error, list(warnings)

    def fetch_jobs(self):
        if self._error:
            raise self._error
        return self._jobs


def _job(n, source="lever"):
    return Job(title=f"Estágio {n}", company="Acme", url=f"https://a.io/{n}", source=source)


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "t.db")
    yield connection
    connection.close()


def test_search_counts_new_and_repeated_jobs(conn):
    db.upsert_job(conn, _job(1))
    summary = pipeline.run_search(conn, [FakeSource("lever", [_job(1), _job(2)])])
    assert summary.per_source == {"lever": (2, 1)}
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2


def test_failing_source_does_not_stop_the_others(conn):
    sources = [FakeSource("ruim", error=SourceBlocked("HTTP 403")), FakeSource("boa", [_job(1)])]
    summary = pipeline.run_search(conn, sources)
    assert summary.per_source["boa"] == (1, 1)
    assert "ruim" in summary.failures


def test_blocked_source_is_logged_for_the_user(conn):
    pipeline.run_search(conn, [FakeSource("ruim", error=SourceBlocked("HTTP 403"))])
    assert "HTTP 403" in conn.execute("SELECT message FROM logs WHERE level = 'error'").fetchone()["message"]


def test_source_warnings_are_logged(conn):
    pipeline.run_search(conn, [FakeSource("lever", warnings=["board quebrada: 404"])])
    assert "board quebrada" in conn.execute("SELECT message FROM logs WHERE level = 'warning'").fetchone()["message"]


def test_search_command_prints_summary(project, capsys, monkeypatch):
    monkeypatch.setattr(pipeline, "build_enabled_sources", lambda settings: [FakeSource("lever", [_job(1)])])
    assert main(["search"]) == 0
    out = capsys.readouterr().out
    assert "lever" in out and "1 nova" in out


def test_search_command_fails_when_every_source_fails(project, capsys, monkeypatch):
    monkeypatch.setattr(
        pipeline, "build_enabled_sources", lambda settings: [FakeSource("lever", error=SourceBlocked("HTTP 403"))]
    )
    assert main(["search"]) == 1
