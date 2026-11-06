"""Lê, preenche e envia formulários com Playwright.

O navegador usa um perfil persistente, onde o usuário faz login à mão. Nenhum disfarce de automação é
usado: se o site mostrar CAPTCHA ou bloqueio, o fluxo para e chama o usuário.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright

from jobbot.apply.form_plan import FillAction, FillPlan, FormField
from jobbot.models import strip_accents

APPLY_LINK = re.compile(r"candidat|apply|inscrev|aplicar", re.IGNORECASE)
SUBMIT_TEXT = re.compile(r"enviar|submit|candidatar|inscri|send application|finalizar", re.IGNORECASE)
NOT_SUBMIT_TEXT = re.compile(r"accept|deny|aceitar|recusar|rejeitar|cookie|buscar|search|linkedin|indeed|google|dropbox", re.IGNORECASE)
CONFIRMATION = re.compile(
    r"obrigad[oa] por|thank you for|thanks for applying|candidatura (foi )?(enviada|recebida|realizada)|"
    r"application (has been |was )?(submitted|received)|recebemos sua candidatura|inscricao (enviada|realizada|concluida)"
)
CONFIRMATION_URL = re.compile(r"/(confirmation|thanks|thank-you|obrigado)\b", re.IGNORECASE)
INVALID_FIELDS = '[aria-invalid="true"]:visible'

OBSTACLE_JS = r"""
() => {
  const visible = el => {
    const r = el.getBoundingClientRect();
    return r.width >= 2 && r.height >= 2 && el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true});
  };
  // O selo do reCAPTCHA invisível (256x60) e os quadros ocultos do hCaptcha não são um desafio na tela.
  const widgets = [...document.querySelectorAll('.g-recaptcha,.h-captcha,.cf-turnstile')]
    .filter(el => !el.classList.contains('grecaptcha-badge') && el.dataset.size !== 'invisible' && visible(el));
  const frames = [...document.querySelectorAll('iframe')]
    .filter(f => /recaptcha|hcaptcha|turnstile|challenges\.cloudflare/.test(f.src || ''))
    .filter(f => !f.closest('.grecaptcha-badge') && visible(f))
    .filter(f => { const r = f.getBoundingClientRect(); return r.width >= 150 && r.height >= 65; });
  const interstitial = /just a moment|attention required|verify you are human/i.test(document.title);
  if (widgets.length || frames.length || interstitial) return 'captcha';
  if ([...document.querySelectorAll('input[type=password]')].some(visible)) return 'login';
  return null;
}
"""

INSPECT_JS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const labelText = label => {
    // Mensagens ocultas de componentes ("nenhum resultado...") ficam dentro do label e não fazem parte dele.
    const copy = label.cloneNode(true);
    const originals = label.querySelectorAll('*'), copies = copy.querySelectorAll('*');
    originals.forEach((node, i) => { if (!node.checkVisibility({checkVisibilityCSS: true})) copies[i].remove(); });
    copy.querySelectorAll('select,input,textarea,button').forEach(n => n.remove());
    return clean(copy.textContent);
  };
  const groupLabel = el => {
    const question = el.closest('.application-question');
    const label = question && question.querySelector('.application-label');
    if (label) return clean(label.textContent);
    const fieldset = el.closest('fieldset');
    const legend = fieldset && fieldset.querySelector('legend');
    if (legend) return clean(legend.textContent);
    const group = el.closest('[role=group],[role=radiogroup]');
    return group ? clean(group.getAttribute('aria-label')) : '';
  };
  const ownLabel = el => {
    if (el.labels && el.labels.length) return labelText(el.labels[0]);
    if (el.getAttribute('aria-label')) return clean(el.getAttribute('aria-label'));
    const by = el.getAttribute('aria-labelledby');
    const node = by && document.getElementById(by.split(' ')[0]);
    if (node) return clean(node.textContent);
    return groupLabel(el) || clean(el.placeholder);
  };
  const shown = el => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && el.checkVisibility({checkVisibilityCSS: true}) && getComputedStyle(el).opacity !== '0';
  };
  const quote = s => s.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
  const selector = el => el.id ? `[id="${quote(el.id)}"]` : `[name="${quote(el.name)}"]`;
  const starred = text => /[*✱]/.test(text);

  const fields = [];
  const groups = new Map();
  for (const el of document.querySelectorAll('input,textarea,select')) {
    const type = (el.type || '').toLowerCase();
    if (['hidden', 'submit', 'button', 'reset', 'image'].includes(type)) continue;
    // Campos sem id nem name são auxiliares de componentes visuais, como o React Select.
    if ((!el.id && !el.name) || /captcha/i.test(el.name || '')) continue;
    if (type !== 'file' && !shown(el)) continue;

    if (type === 'radio' || type === 'checkbox') {
      const groupKey = el.name || el.id;
      if (!groups.has(groupKey)) {
        const label = groupLabel(el) || ownLabel(el);
        const field = {key: el.name ? `[name="${quote(el.name)}"]` : selector(el), kind: type, label, required: false, options: []};
        groups.set(groupKey, field);
        fields.push(field);
      }
      const field = groups.get(groupKey);
      field.options.push(ownLabel(el));
      field.required = field.required || el.required || starred(field.label);
      continue;
    }

    const label = ownLabel(el);
    let kind = 'text';
    if (el.tagName === 'TEXTAREA') kind = 'textarea';
    else if (el.tagName === 'SELECT') kind = 'select';
    else if (el.getAttribute('role') === 'combobox') kind = 'combobox';
    else if (['file', 'email', 'tel', 'password'].includes(type)) kind = type;
    fields.push({
      key: selector(el), kind, label,
      required: el.required || el.getAttribute('aria-required') === 'true' || starred(label),
      options: el.tagName === 'SELECT' ? [...el.options].filter(o => o.value !== '').map(o => clean(o.text)) : [],
    });
  }
  return fields;
}
"""

OPTION_LABELS_JS = r"""
els => els.map(el => {
  const label = el.labels && el.labels[0];
  if (!label) return '';
  const copy = label.cloneNode(true);
  copy.querySelectorAll('select,input,textarea,button').forEach(n => n.remove());
  return copy.textContent.replace(/\s+/g, ' ').trim();
})
"""


class FillProblem(Exception):
    """O formulário não aceitou o valor planejado."""


@dataclass
class SubmitResult:
    # confirmed: o site confirmou | invalid: nada foi enviado | captcha: desafio na tela | uncertain: sem como saber
    status: str
    detail: str = ""


class BrowserSession:
    def __init__(self, profile_dir: Path, headless: bool = True):
        self._profile_dir = profile_dir
        self._headless = headless

    def __enter__(self) -> "BrowserSession":
        self._playwright = sync_playwright().start()
        self.context = self._playwright.chromium.launch_persistent_context(
            str(self._profile_dir), headless=self._headless, locale="pt-BR"
        )
        return self

    def __exit__(self, *exc_info) -> None:
        self.context.close()
        self._playwright.stop()

    def new_page(self) -> Page:
        return self.context.new_page()


def login_interactively(profile_dir: Path, url: str) -> None:
    """Abre o navegador para o usuário entrar na conta. O jobbot não lê nem guarda a senha."""
    with BrowserSession(profile_dir, headless=False) as session:
        session.new_page().goto(url)
        session.context.wait_for_event("close", timeout=0)


def _settle(page: Page) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=5000)
    except PlaywrightError:
        pass  # Sites com conexões abertas nunca ficam ociosos; o conteúdo já carregou.


def open_form(page: Page, url: str) -> None:
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    _settle(page)
    if page.locator('input:visible:not([type="hidden"]), textarea:visible, select:visible').count():
        return
    # Só links: um botão "Candidatar-se" pode ser candidatura em um clique, e abrir a página não pode enviar nada.
    link = page.locator("a[href]:visible").filter(has_text=APPLY_LINK).first
    if link.count():
        link.click(timeout=10000)
        page.wait_for_load_state("domcontentloaded", timeout=30000)
        _settle(page)


def detect_obstacle(page: Page) -> str | None:
    return page.evaluate(OBSTACLE_JS)


def _read_combobox_options(page: Page, key: str) -> list[str]:
    visible_options = page.locator('[role="option"]:visible')
    try:
        page.locator(key).first.click(timeout=2000)
    except PlaywrightError:
        return []
    try:
        visible_options.first.wait_for(state="visible", timeout=1500)
    except PlaywrightError:
        pass  # Campos de busca só mostram opções depois de digitar.
    options = visible_options.all_inner_texts()
    page.keyboard.press("Escape")
    return [option.strip() for option in options]


def inspect_form(page: Page) -> list[FormField]:
    fields = [FormField(**item) for item in page.evaluate(INSPECT_JS)]
    for field in fields:
        if field.kind == "combobox":
            field.options = _read_combobox_options(page, field.key)
    return fields


def _pick_combobox_option(page: Page, action: FillAction, timeout_ms: int) -> None:
    box = page.locator(action.key).first
    box.click(timeout=timeout_ms)
    box.fill(action.value, timeout=timeout_ms)
    options = page.locator('[role="option"]:visible')
    try:
        options.first.wait_for(state="visible", timeout=min(timeout_ms, 2500))
    except PlaywrightError as exc:
        raise FillProblem("o campo não mostrou opções") from exc
    exact = options.filter(has_text=re.compile(rf"^\s*{re.escape(action.value)}\s*$", re.IGNORECASE))
    choice = exact if exact.count() else options.filter(has_text=action.value)
    if not choice.count():
        raise FillProblem(f"opção não encontrada: {action.value}")
    choice.first.click(timeout=timeout_ms)


def _fill_one(page: Page, action: FillAction, timeout_ms: int) -> None:
    locator = page.locator(action.key)
    if action.kind == "file":
        locator.first.set_input_files(action.value, timeout=timeout_ms)
    elif action.kind == "select":
        locator.first.select_option(label=action.value, timeout=timeout_ms)
    elif action.kind == "combobox":
        _pick_combobox_option(page, action, timeout_ms)
    elif action.kind in ("radio", "checkbox"):
        labels = locator.evaluate_all(OPTION_LABELS_JS)
        if action.value not in labels:
            raise FillProblem(f"opção não encontrada: {action.value}")
        locator.nth(labels.index(action.value)).check(force=True, timeout=timeout_ms)
    else:
        locator.first.fill(action.value, timeout=timeout_ms)


def fill_form(page: Page, plan: FillPlan, timeout_ms: int = 5000, skip_kinds: tuple[str, ...] = ()) -> list[str]:
    problems = []
    for action in plan.actions:
        if action.kind in skip_kinds:
            continue
        try:
            _fill_one(page, action, timeout_ms)
        except (PlaywrightError, FillProblem) as exc:
            problems.append(f"não consegui preencher {action.label}: {str(exc).splitlines()[0]}")
    return problems


def _find_submit_button(page: Page):
    # Botões de cookies e de "enviar com LinkedIn" também podem ser type=submit: o texto vem primeiro,
    # e um botão genérico só vale se estiver no mesmo formulário dos campos.
    for candidates in (
        page.locator("button:visible").filter(has_text=SUBMIT_TEXT).filter(has_not_text=NOT_SUBMIT_TEXT),
        page.locator('input[type="submit"]:visible'),
        page.locator('form:has(input:not([type="hidden"]), textarea) button[type="submit"]:visible').filter(has_not_text=NOT_SUBMIT_TEXT),
    ):
        if candidates.count():
            return candidates.last
    return None


def _body_text(page: Page) -> str:
    return strip_accents(page.inner_text("body", timeout=1000))


def submit_form(page: Page, timeout_ms: int = 20000) -> SubmitResult:
    button = _find_submit_button(page)
    if button is None:
        return SubmitResult("invalid", "botão de envio não encontrado")
    already_confirmed = CONFIRMATION.search(_body_text(page)) is not None
    invalid_before = page.locator(INVALID_FIELDS).count()
    start_url = page.url
    try:
        # no_wait_after: o clique volta assim que é feito. Esperar a navegação aqui faria um servidor lento
        # estourar o tempo depois do envio já ter saído, e o envio pareceria nunca ter acontecido.
        button.click(timeout=min(5000, timeout_ms), no_wait_after=True)
    except PlaywrightError as exc:
        return SubmitResult("invalid", f"não foi possível clicar em enviar: {str(exc).splitlines()[0]}")

    # Daqui em diante o envio pode ter saído: na dúvida, o resultado é "uncertain", nunca algo que permita repetir.
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        try:
            page.wait_for_timeout(250)
        except PlaywrightError:
            return SubmitResult("uncertain", "a página fechou depois do clique em enviar")
        try:
            if page.url != start_url and CONFIRMATION_URL.search(page.url):
                return SubmitResult("confirmed")
            if not already_confirmed and CONFIRMATION.search(_body_text(page)):
                return SubmitResult("confirmed")
            if detect_obstacle(page) == "captcha":
                return SubmitResult("captcha", "o site pediu CAPTCHA depois do envio")
            # Campo que já estava inválido antes do clique não diz nada: só conta marcação nova.
            if page.locator(INVALID_FIELDS).count() > invalid_before:
                return SubmitResult("invalid", "o formulário apontou campos inválidos")
        except PlaywrightError:
            continue  # A página está navegando; a próxima volta lê o novo conteúdo.
    try:
        if page.locator("form :invalid:visible").count():
            return SubmitResult("invalid", "o navegador recusou campos inválidos")
    except PlaywrightError:
        pass
    return SubmitResult("uncertain", "o site não confirmou o envio dentro do tempo limite")
