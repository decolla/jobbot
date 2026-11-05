import pytest

from jobbot.notify.email import send_email
from jobbot.profile import ConfigError

ENV = {"SMTP_HOST": "smtp.io", "SMTP_PORT": "587", "SMTP_USER": "eu@real.com", "SMTP_PASSWORD": "senha-app"}


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port
        self.calls = []
        self.messages = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user))

    def send_message(self, message):
        self.messages.append(message)


@pytest.fixture(autouse=True)
def reset():
    FakeSMTP.instances.clear()


def test_send_uses_tls_and_login():
    send_email(ENV, "rh@acme.com", "Re: Vaga", "Olá", smtp_factory=FakeSMTP)
    smtp = FakeSMTP.instances[0]
    assert (smtp.host, smtp.port) == ("smtp.io", 587)
    assert smtp.calls == ["starttls", ("login", "eu@real.com")]


def test_message_has_headers_and_body():
    send_email(ENV, "rh@acme.com", "Re: Vaga", "Olá, confirmo.", in_reply_to="<1@x>", smtp_factory=FakeSMTP)
    message = FakeSMTP.instances[0].messages[0]
    assert (message["From"], message["To"], message["Subject"]) == ("eu@real.com", "rh@acme.com", "Re: Vaga")
    assert message["In-Reply-To"] == "<1@x>"
    assert message["References"] == "<1@x>"
    assert "Olá, confirmo." in message.get_content()


def test_missing_smtp_settings_is_config_error():
    with pytest.raises(ConfigError, match="SMTP"):
        send_email({}, "rh@acme.com", "Assunto", "Corpo", smtp_factory=FakeSMTP)
