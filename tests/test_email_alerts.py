"""Os HTMLs daqui são sintéticos: ainda não há e-mail real de alerta para virar fixture."""

import pytest

from jobbot.profile import ConfigError, SourceSettings
from jobbot.sources import build_sources
from jobbot.sources.base import SourceError
from jobbot.sources.email_alerts import EmailAlertsSource, parse_alert
from jobbot.tracking.mailbox import MailboxError, Message

LINKEDIN = """
<table>
  <tr><td><a href="https://www.linkedin.com/comm/jobs/view/111/?trackingId=abc"><img src="x.png" alt="Acme"></a></td></tr>
  <tr><td><a href="https://www.linkedin.com/comm/jobs/view/111/?trackingId=abc">Estágio em Desenvolvimento</a></td></tr>
  <tr><td><p>Acme · São Paulo, SP (Híbrido)</p></td></tr>
  <tr><td><a href="https://www.linkedin.com/comm/jobs/view/222/?trackingId=def">Desenvolvedor Júnior</a></td></tr>
  <tr><td><p>Beta · Brasil (Remoto)</p></td></tr>
  <tr><td><a href="https://www.linkedin.com/comm/jobs/alerts">Ver todas as vagas</a></td></tr>
  <tr><td><a href="https://www.linkedin.com/comm/psettings/unsubscribe">Cancelar inscrição</a></td></tr>
</table>
"""

INDEED = """
<div>
  <a href="https://br.indeed.com/rc/clk?jk=abc123&from=ja">Estagiário de TI</a>
  <div>Gama</div>
  <div>Recife, PE</div>
  <a href="https://br.indeed.com/viewjob?jk=def456&tk=1">Analista de Dados Júnior</a>
  <div>Delta</div>
  <div>Remoto</div>
</div>
"""


def _message(html, message_id="<1@x>", sender="jobalerts-noreply@linkedin.com"):
    return Message(message_id=message_id, subject="Novas vagas", sender=sender, sender_address=sender,
                   text="", html=html, date="2026-11-05")


def test_linkedin_alert_becomes_manual_jobs_with_canonical_urls():
    jobs = parse_alert(_message(LINKEDIN))
    assert [j.url for j in jobs] == ["https://www.linkedin.com/jobs/view/111", "https://www.linkedin.com/jobs/view/222"]
    assert all(j.apply_path == "manual" and j.source == "linkedin" for j in jobs)


def test_linkedin_alert_reads_title_company_location_and_modality():
    job = parse_alert(_message(LINKEDIN))[0]
    assert job.title == "Estágio em Desenvolvimento"
    assert job.company == "Acme"
    assert job.location == "São Paulo, SP (Híbrido)"
    assert job.modality == "hybrid"


def test_indeed_alert_reads_jobs():
    jobs = parse_alert(_message(INDEED, sender="alert@indeed.com"))
    assert [j.url for j in jobs] == ["https://br.indeed.com/viewjob?jk=abc123", "https://br.indeed.com/viewjob?jk=def456"]
    assert jobs[0].title == "Estagiário de TI"
    assert jobs[0].company == "Gama"
    assert jobs[0].location == "Recife, PE"
    assert jobs[1].modality == "remote"
    assert all(j.source == "indeed" for j in jobs)


def test_alert_without_job_links_returns_nothing():
    assert parse_alert(_message("<p>Sem vagas hoje</p>")) == []


class FakeMailbox:
    def __init__(self, *messages):
        self.messages = messages
        self.calls = []

    def fetch_recent(self, days, senders=None, today=None):
        self.calls.append((days, senders))
        return list(self.messages)


def _config():
    return SourceSettings(enabled=True, senders=["jobalerts-noreply@linkedin.com"], lookback_days=3)


def test_source_reads_only_configured_senders():
    mailbox = FakeMailbox(_message(LINKEDIN))
    source = EmailAlertsSource(_config(), http=None, mailbox=mailbox)
    assert len(source.fetch_jobs()) == 2
    assert mailbox.calls == [(3, ["jobalerts-noreply@linkedin.com"])]


def test_alert_in_unknown_format_becomes_a_warning():
    source = EmailAlertsSource(_config(), http=None, mailbox=FakeMailbox(_message("<p>layout novo</p>")))
    assert source.fetch_jobs() == []
    assert "Novas vagas" in source.warnings[0]


def test_mailbox_failure_becomes_source_error():
    class Broken:
        def fetch_recent(self, days, senders=None, today=None):
            raise MailboxError("login recusado")

    with pytest.raises(SourceError, match="login recusado"):
        EmailAlertsSource(_config(), http=None, mailbox=Broken()).fetch_jobs()


def test_source_without_imap_credentials_fails_with_clear_error(monkeypatch):
    monkeypatch.setattr("jobbot.sources.email_alerts.load_env", lambda: {})
    with pytest.raises(SourceError, match="IMAP"):
        EmailAlertsSource(_config(), http=None).fetch_jobs()


def test_email_alerts_can_be_enabled_in_settings():
    sources = build_sources({"email_alerts": _config()}, http=None)
    assert [s.name for s in sources] == ["email_alerts"]


def test_unknown_source_is_rejected():
    with pytest.raises(ConfigError, match="desconhecida"):
        build_sources({"nova": SourceSettings(enabled=True)}, http=None)
