"""O que acontece depois do clique em enviar, sem navegador: a página pode fechar ou o navegador cair."""

from playwright.sync_api import Error as PlaywrightError

from jobbot.apply import form_filler


class _Button:
    def click(self, timeout=None, no_wait_after=None):
        pass


class _PageClosedAfterClick:
    url = "http://127.0.0.1/form"

    def inner_text(self, selector, timeout=None):
        return ""

    def locator(self, selector):
        return self

    def count(self):
        return 0

    def wait_for_timeout(self, ms):
        raise PlaywrightError("Target page, context or browser has been closed")


def test_page_closed_after_click_is_uncertain(monkeypatch):
    # O clique pode ter enviado: tratar como "parada" permitiria um segundo envio no --retry-blocked.
    monkeypatch.setattr(form_filler, "_find_submit_button", lambda page: _Button())
    result = form_filler.submit_form(_PageClosedAfterClick(), timeout_ms=1000)
    assert result.status == "uncertain"
