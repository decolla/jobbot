"""Site local para testar o preenchimento em um navegador de verdade, sem internet."""

from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CLASSIC = """
<html><head><title>Vaga</title></head><body>
<form action="{action}" method="post" enctype="multipart/form-data">
  <label>Resume/CV <input type="file" name="resume"></label>
  <label>Full name ✱ <input type="text" name="name" required></label>
  <label>Email ✱ <input type="email" name="email" required></label>
  <label>Phone ✱ <input type="text" name="phone" required></label>
  <label>Current company <input type="text" name="org"></label>
  <label for="why">Conte sobre um projeto ✱</label><textarea id="why" name="why" required></textarea>
  <fieldset><legend>Já programou em Python? ✱</legend>
    <label><input type="radio" name="py" value="1" required> Sim</label>
    <label><input type="radio" name="py" value="0" required> Não</label>
  </fieldset>
  <label>Nível de inglês ✱
    <select name="ingles" required><option value="">Selecione</option><option>Básico</option><option>Avançado</option></select>
  </label>
  <input type="hidden" name="token" value="x">
  <button type="button">Dropbox</button>
  <button type="submit">Enviar candidatura</button>
</form></body></html>
"""

CARD_QUESTION = """
<html><body><form action="/submit" method="post"><ul>
<li class="application-question"><div class="application-label"><div class="text">Como soube da vaga?<span>✱</span></div></div>
  <div class="application-field"><ul>
    <li><label><input type="radio" name="cards[a][field0]" value="x" required><span>Site</span></label></li>
    <li><label><input type="radio" name="cards[a][field0]" value="y" required><span>Amigo</span></label></li>
  </ul></div></li>
</ul><button type="submit">Submit application</button></form></body></html>
"""

COMBOBOX = """
<html><body><form action="/submit" method="post">
  <label for="grau">Grau de escolaridade*</label>
  <input id="grau" type="text" role="combobox" aria-haspopup="true" required>
  <div id="lista" hidden role="listbox">
    <div role="option">Superior incompleto</div><div role="option">Superior completo</div>
  </div>
  <input type="text" required tabindex="-1" style="opacity:0;height:1px;width:1px">
  <input type="hidden" id="grau_valor" name="grau">
  <button type="submit">Enviar inscrição</button>
</form>
<script>
  const c = document.getElementById('grau'), l = document.getElementById('lista');
  c.addEventListener('click', () => { l.hidden = false; });
  c.addEventListener('keydown', e => { if (e.key === 'Escape') l.hidden = true; });
  l.querySelectorAll('[role=option]').forEach(o => o.addEventListener('click', () => {
    c.value = o.innerText; document.getElementById('grau_valor').value = o.innerText; l.hidden = true;
  }));
</script></body></html>
"""

SENSITIVE_FORM = """
<html><body><form action="/submit" method="post">
  <label>Full name ✱ <input type="text" name="name" required></label>
  <label>Qual é o seu CPF? ✱ <input type="text" name="cpf" required></label>
  <button type="submit">Enviar candidatura</button>
</form></body></html>
"""

SIMPLE_FORM = """
<html><body><form action="/submit" method="post">
  <label>Full name ✱ <input type="text" name="name" required></label>
  <button type="submit">Enviar candidatura</button>
</form></body></html>
"""

# Imita formulários que sobem o arquivo assim que ele é escolhido, antes do envio final.
AUTO_UPLOAD_FORM = """
<html><body><form action="/submit" method="post">
  <label>Full name ✱ <input type="text" name="name" required></label>
  <label>Resume/CV <input type="file" name="resume"></label>
  <button type="submit">Enviar candidatura</button>
</form>
<script>
  document.querySelector('[name=resume]').addEventListener('change', () => fetch('/upload', {method: 'POST', body: 'arquivo'}));
</script></body></html>
"""

# Trechos reais do Lever: texto oculto dentro do rótulo e textarea sem elemento label.
LEVER_LABELS = """
<html><body><form action="/submit" method="post"><ul>
<li class="application-question"><label><div class="application-label">Current location <span>✱</span></div>
  <div class="application-field"><input id="location-input" type="text" name="location" required>
    <div style="display:none">No location found. Try entering a different location</div></div></label></li>
<li class="application-question custom-question"><div><div class="application-label full-width textarea">
  <div class="text">Conte sobre você<span class="required">✱</span></div></div>
  <div class="application-field"><textarea name="cards[a][field0]" required></textarea></div></div></li>
</ul><button type="button" id="btn-submit" onclick="this.form.submit()">SUBMIT APPLICATION</button></form></body></html>
"""

THANKS_REDIRECT = """
<html><body><form action="/thanks" method="post">
  <label>Full name ✱ <input type="text" name="name" required></label>
  <button type="submit">Enviar candidatura</button>
</form></body></html>
"""

# Banner de cookies real do Lever: Accept e Deny também são type=submit e vêm depois do envio real.
COOKIE_BUTTONS = """
<html><body><form action="/submit" method="post">
  <label>Full name ✱ <input type="text" name="name" required></label>
  <button type="submit">Enviar candidatura</button>
</form>
<form action="/cookies" method="post"><button type="submit">Deny</button><button type="submit">Accept</button></form>
</body></html>
"""

ONLY_COOKIE_BUTTONS = """
<html><body><form action="/submit" method="post"><label>Full name ✱ <input type="text" name="name" required></label></form>
<form action="/cookies" method="post"><button type="submit">Accept</button></form>
</body></html>
"""

GENERIC_SUBMIT = """
<html><body><form action="/submit" method="post">
  <label>Full name ✱ <input type="text" name="name" required></label>
  <button type="submit">Continuar</button>
</form></body></html>
"""

# As opções só aparecem um pouco depois do clique, como em componentes reais.
SLOW_COMBOBOX = """
<html><body><form action="/submit" method="post">
  <label for="grau">Grau de escolaridade*</label>
  <input id="grau" type="text" role="combobox" required>
  <div id="lista" hidden role="listbox"><div role="option">Superior incompleto</div></div>
  <button type="submit">Enviar</button>
</form>
<script>
  const c = document.getElementById('grau'), l = document.getElementById('lista');
  c.addEventListener('click', () => setTimeout(() => { l.hidden = false; }, 500));
  c.addEventListener('keydown', e => { if (e.key === 'Escape') l.hidden = true; });
</script></body></html>
"""

JOB_PAGE = '<html><body><h1>Vaga</h1><p>Descrição</p><a href="/classic">Apply for this job</a></body></html>'
# Candidatura em um clique: o botão envia sozinho, sem formulário.
ONE_CLICK = """
<html><body><h1>Vaga</h1>
<button onclick="fetch('/one-click-apply', {method: 'POST'})">Candidatar-se</button>
</body></html>
"""
SLOW_POSTS = {"/slow-submit": 0.8, "/very-slow-submit": 2.0}
# Formulário comum com servidor lento: a resposta chega depois do tempo do clique.
SLOW_NATIVE = """
<html><body><form action="/very-slow-submit" method="post">
  <label>Full name ✱ <input type="text" name="name" required></label>
  <button type="submit">Enviar candidatura</button>
</form></body></html>
"""
# Envio por JavaScript, como nos formulários React: o clique volta na hora e a resposta demora.
# Um campo opcional já vem marcado como inválido antes do clique.
PRE_INVALID = """
<html><body><form id="f" action="/slow-submit" method="post">
  <label>Full name ✱ <input type="text" name="name" required></label>
  <label>Site <input type="text" name="site" aria-invalid="true"></label>
  <button type="submit">Enviar candidatura</button>
</form>
<script>
  document.getElementById('f').addEventListener('submit', async e => {
    e.preventDefault();
    await fetch('/slow-submit', {method: 'POST', body: new FormData(e.target)});
    document.body.innerHTML = '<h1>Obrigado por se candidatar!</h1>';
  });
</script></body></html>
"""
VISIBLE_CAPTCHA = '<html><body><form><input name="name"><div class="g-recaptcha" style="width:304px;height:78px"></div></form></body></html>'
HIDDEN_CAPTCHA = (
    '<html><body><form><input name="name"><div class="grecaptcha-badge" style="width:256px;height:60px"></div>'
    '<iframe src="about:blank" title="hcaptcha" style="visibility:hidden;width:1280px;height:720px"></iframe>'
    "</form></body></html>"
)
CLOUDFLARE = "<html><head><title>Just a moment...</title></head><body>Checking your browser</body></html>"
LOGIN = '<html><body><form><input type="email" name="email"><input type="password" name="pw"></form></body></html>'
CONFIRMED = "<html><body><h1>Obrigado por se candidatar!</h1></body></html>"
SILENT = "<html><body><h1>Página qualquer</h1></body></html>"
INVALID = '<html><body><form><input name="name" aria-invalid="true"><span class="error">Campo obrigatório</span></form></body></html>'
CAPTCHA_AFTER = '<html><body><div class="h-captcha" style="width:304px;height:78px"></div></body></html>'


class FormSite:
    def __init__(self):
        self.posts: list[tuple[str, bytes]] = []
        self.pages = {
            "/classic": CLASSIC.format(action="/submit"),
            "/classic-silent": CLASSIC.format(action="/silent"),
            "/classic-invalid": CLASSIC.format(action="/invalid"),
            "/classic-captcha": CLASSIC.format(action="/captcha"),
            "/thanks-redirect": THANKS_REDIRECT,
            "/lever-labels": LEVER_LABELS,
            "/cookie-buttons": COOKIE_BUTTONS,
            "/only-cookie-buttons": ONLY_COOKIE_BUTTONS,
            "/generic-submit": GENERIC_SUBMIT,
            "/slow-combobox": SLOW_COMBOBOX,
            "/auto-upload": AUTO_UPLOAD_FORM,
            "/sensitive": SENSITIVE_FORM,
            "/simple": SIMPLE_FORM,
            "/simple2": SIMPLE_FORM,
            "/card": CARD_QUESTION,
            "/combobox": COMBOBOX,
            "/job": JOB_PAGE,
            "/one-click": ONE_CLICK,
            "/pre-invalid": PRE_INVALID,
            "/slow-native": SLOW_NATIVE,
            "/visible-captcha": VISIBLE_CAPTCHA,
            "/hidden-captcha": HIDDEN_CAPTCHA,
            "/cloudflare": CLOUDFLARE,
            "/login": LOGIN,
            "/no-form": SILENT,
        }
        self.responses = {"/submit": CONFIRMED, "/silent": SILENT, "/thanks": SILENT, "/invalid": INVALID, "/captcha": CAPTCHA_AFTER}
        site = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, status: int, body: str) -> None:
                data = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                page = site.pages.get(self.path)
                self._send(200 if page else 404, page or "nao encontrada")

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                site.posts.append((self.path, body))
                time.sleep(SLOW_POSTS.get(self.path, 0))
                self._send(200, site.responses.get(self.path, CONFIRMED))

            def log_message(self, *args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self._server.server_address[1]}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def url(self, path: str) -> str:
        return self.base_url + path

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
