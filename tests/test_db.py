import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from jobbot import db
from jobbot.models import Job


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "jobbot.db")
    yield connection
    connection.close()


def _job(**over):
    base = dict(title="Estágio Dev", company="Acme", location="SP", url="https://a.io/1", source="lever", description="Python")
    return Job(**{**base, **over})


def test_new_job_is_inserted_as_new(conn):
    job_id, created = db.upsert_job(conn, _job())
    assert created is True
    assert db.get_job(conn, job_id)["status"] == "new"


def test_same_job_from_another_source_is_not_duplicated(conn):
    first, _ = db.upsert_job(conn, _job())
    second, created = db.upsert_job(conn, _job(url="https://b.io/2", source="gupy"))
    assert (second, created) == (first, False)
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_same_url_with_different_text_is_not_duplicated(conn):
    first, _ = db.upsert_job(conn, _job())
    second, created = db.upsert_job(conn, _job(title="Estágio Desenvolvimento"))
    assert (second, created) == (first, False)


def test_seeing_a_job_again_keeps_its_score(conn):
    job_id, _ = db.upsert_job(conn, _job())
    db.set_score(conn, job_id, 88, "bom", "scored")
    db.upsert_job(conn, _job())
    row = db.get_job(conn, job_id)
    assert (row["score"], row["status"]) == (88, "scored")


def test_database_rejects_second_application_for_same_job(conn):
    job_id, _ = db.upsert_job(conn, _job())
    db.create_application(conn, job_id, "dry-run")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO applications (job_id, status, mode) VALUES (?, 'draft', 'auto')", (job_id,))


def test_create_application_returns_existing_row(conn):
    job_id, _ = db.upsert_job(conn, _job())
    assert db.create_application(conn, job_id, "dry-run") == db.create_application(conn, job_id, "auto")


def test_claim_for_submission_succeeds_only_once(conn):
    job_id, _ = db.upsert_job(conn, _job())
    app_id = db.create_application(conn, job_id, "auto")
    assert db.claim_for_submission(conn, app_id) is True
    assert db.claim_for_submission(conn, app_id) is False


def test_claim_is_refused_after_submitted(conn):
    job_id, _ = db.upsert_job(conn, _job())
    app_id = db.create_application(conn, job_id, "auto")
    db.claim_for_submission(conn, app_id)
    db.mark_submitted(conn, app_id)
    assert db.claim_for_submission(conn, app_id) is False


def test_mark_submitted_records_applied_stage(conn):
    job_id, _ = db.upsert_job(conn, _job())
    app_id = db.create_application(conn, job_id, "auto")
    db.claim_for_submission(conn, app_id)
    db.mark_submitted(conn, app_id)
    assert [s["stage"] for s in db.get_stages(conn, app_id)] == ["applied"]
    assert db.get_application(conn, app_id)["status"] == "submitted"


def test_uncertain_submission_cannot_be_claimed_again(conn):
    job_id, _ = db.upsert_job(conn, _job())
    app_id = db.create_application(conn, job_id, "auto")
    db.claim_for_submission(conn, app_id)
    db.set_application_status(conn, app_id, "submit_uncertain", reason="sem confirmação")
    assert db.claim_for_submission(conn, app_id) is False


def test_submissions_today_counts_only_today(conn):
    now = datetime(2026, 11, 5, 15, 0, tzinfo=timezone.utc)
    for n, when in enumerate([now, now - timedelta(days=1)]):
        job_id, _ = db.upsert_job(conn, _job(title=f"Vaga {n}", url=f"https://a.io/{n}"))
        app_id = db.create_application(conn, job_id, "auto")
        db.claim_for_submission(conn, app_id)
        db.mark_submitted(conn, app_id, when=when)
    assert db.submissions_on(conn, now.date()) == 1


def test_submissions_today_count_attempts_that_may_have_gone_out(conn):
    # Envio incerto pode ter chegado: conta no limite do dia, para o modo auto não passar dele.
    job_id, _ = db.upsert_job(conn, _job())
    app_id = db.create_application(conn, job_id, "auto")
    db.claim_for_submission(conn, app_id)
    db.log_event(conn, "application_submit_attempt", application_id=app_id, job_id=job_id)
    db.set_application_status(conn, app_id, "submit_uncertain")
    assert db.submissions_on(conn, datetime.now().date()) == 1
    db.mark_submitted(conn, app_id)
    assert db.submissions_on(conn, datetime.now().date()) == 1


def test_event_payload_round_trips_and_is_auditable(conn):
    db.log_event(conn, "application_submitted", application_id=None, payload={"email": "a@b.c"})
    event = db.get_events(conn, "application_submitted")[0]
    assert event["payload"] == {"email": "a@b.c"}


def test_message_is_seen_only_once(conn):
    assert db.mark_message_seen(conn, "<id-1@x>") is True
    assert db.mark_message_seen(conn, "<id-1@x>") is False


def test_action_item_is_not_duplicated_for_same_stage_kind(conn):
    job_id, _ = db.upsert_job(conn, _job())
    app_id = db.create_application(conn, job_id, "auto")
    first = db.add_action_item(conn, app_id, "test", "Teste técnico", link="https://t.io", due_date="2026-11-10")
    second = db.add_action_item(conn, app_id, "test", "Teste técnico", link="https://t.io", due_date="2026-11-10")
    assert first == second
    assert len(db.open_action_items(conn)) == 1


def test_transaction_commits_on_success(conn):
    with db.transaction(conn):
        db.upsert_job(conn, _job())
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_transaction_rolls_back_on_error(conn):
    with pytest.raises(RuntimeError):
        with db.transaction(conn):
            db.upsert_job(conn, _job())
            raise RuntimeError("falhou")
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_connect_is_idempotent(tmp_path):
    path = tmp_path / "x.db"
    db.connect(path).close()
    connection = db.connect(path)
    assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    connection.close()
