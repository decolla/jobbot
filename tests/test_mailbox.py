from datetime import date
from email.message import EmailMessage

import pytest

from jobbot.profile import ConfigError, load_env
from jobbot.tracking.mailbox import Mailbox, parse_message


def _raw(subject="Assunto", sender="Acme <rh@acme.com>", body="Corpo do texto", html=None, message_id="<1@acme.com>"):
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = "pessoa@real.com"
    message["Date"] = "Thu, 05 Nov 2026 10:00:00 -0300"
    message["Message-ID"] = message_id
    message.set_content(body)
    if html:
        message.add_alternative(html, subtype="html")
    return message.as_bytes()


class FakeIMAP:
    def __init__(self, messages):
        self.messages = messages
        self.searches = []
        self.selected = None
        self.logged_out = False

    def login(self, user, password):
        self.credentials = (user, password)

    def select(self, mailbox, readonly=False):
        self.selected = (mailbox, readonly)
        return "OK", [b"1"]

    def search(self, charset, *criteria):
        self.searches.append(criteria)
        return "OK", [" ".join(str(i + 1) for i in range(len(self.messages))).encode()]

    def fetch(self, number, parts):
        self.fetched_parts = parts
        return "OK", [(b"1 (BODY[] {10}", self.messages[int(number) - 1])]

    def logout(self):
        self.logged_out = True


def test_parse_message_reads_headers_and_body():
    message = parse_message(_raw())
    assert message.subject == "Assunto"
    assert message.sender_address == "rh@acme.com"
    assert message.message_id == "<1@acme.com>"
    assert "Corpo do texto" in message.text


def test_parse_message_decodes_encoded_subject():
    assert parse_message(_raw(subject="Sua candidatura à vaga")).subject == "Sua candidatura à vaga"


def test_parse_message_keeps_links_from_html():
    html = '<p>Faça o <a href="https://hackerrank.com/t/1">teste</a></p>'
    message = parse_message(_raw(html=html))
    assert "https://hackerrank.com/t/1" in message.text
    assert message.html == html + "\n"


def test_parse_message_without_message_id_gets_stable_id():
    first = parse_message(_raw(message_id=""))
    second = parse_message(_raw(message_id=""))
    assert first.message_id and first.message_id == second.message_id


def _mailbox(imap):
    return Mailbox("imap.io", "user", "senha", imap_factory=lambda host: imap)


def test_fetch_opens_inbox_read_only_and_never_marks_as_read():
    imap = FakeIMAP([_raw()])
    _mailbox(imap).fetch_recent(7, today=date(2026, 11, 5))
    assert imap.selected == ("INBOX", True)
    assert "PEEK" in imap.fetched_parts
    assert imap.logged_out


def test_fetch_filters_by_date_in_imap_format():
    imap = FakeIMAP([])
    _mailbox(imap).fetch_recent(7, today=date(2026, 11, 5))
    assert imap.searches[0] == ("SINCE", "29-Oct-2026")


def test_fetch_filters_by_each_sender():
    imap = FakeIMAP([_raw()])
    messages = _mailbox(imap).fetch_recent(7, senders=["a@x.com", "b@x.com"], today=date(2026, 11, 5))
    assert [s[2:] for s in imap.searches] == [("FROM", "a@x.com"), ("FROM", "b@x.com")]
    assert len(messages) == 2


def test_from_env_requires_credentials(tmp_path):
    with pytest.raises(ConfigError, match="IMAP"):
        Mailbox.from_env({})


def test_load_env_reads_file_and_environment_overrides(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text('# comentário\nA=1\nB="dois"\n\nC = tres\n', encoding="utf-8")
    monkeypatch.setenv("C", "ambiente")
    env = load_env(tmp_path)
    assert (env["A"], env["B"], env["C"]) == ("1", "dois", "ambiente")


def test_load_env_without_file_uses_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAP_HOST", "x")
    assert load_env(tmp_path)["IMAP_HOST"] == "x"
