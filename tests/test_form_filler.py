import pytest

from form_site import FormSite
from jobbot.apply.form_filler import BrowserSession, detect_obstacle, fill_form, inspect_form, open_form, submit_form
from jobbot.apply.form_plan import FillAction, FillPlan

pytestmark = pytest.mark.browser


@pytest.fixture(scope="module")
def site():
    server = FormSite()
    yield server
    server.close()


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    with BrowserSession(tmp_path_factory.mktemp("browser_profile"), headless=True) as browser:
        yield browser


@pytest.fixture
def page(session):
    tab = session.new_page()
    yield tab
    tab.close()


@pytest.fixture(autouse=True)
def clear_posts(site):
    site.posts.clear()


def _by_label(fields, text):
    return next(f for f in fields if text in f.label)


def test_inspect_reads_kinds_labels_and_required(page, site):
    open_form(page, site.url("/classic"))
    fields = inspect_form(page)
    name = _by_label(fields, "Full name")
    assert (name.kind, name.required, name.key) == ("text", True, '[name="name"]')
    assert _by_label(fields, "Email").kind == "email"
    assert _by_label(fields, "Resume").kind == "file"
    assert _by_label(fields, "Current company").required is False
    assert _by_label(fields, "Conte sobre").kind == "textarea"


def test_inspect_ignores_hidden_inputs_and_buttons(page, site):
    open_form(page, site.url("/classic"))
    keys = [f.key for f in inspect_form(page)]
    assert '[name="token"]' not in keys
    assert len(keys) == 8


def test_inspect_groups_radio_buttons_under_the_question(page, site):
    open_form(page, site.url("/classic"))
    field = _by_label(inspect_form(page), "Já programou")
    assert field.kind == "radio"
    assert field.options == ["Sim", "Não"]
    assert field.required is True


def test_inspect_reads_lever_style_question_label(page, site):
    open_form(page, site.url("/card"))
    field = inspect_form(page)[0]
    assert "Como soube da vaga" in field.label
    assert field.options == ["Site", "Amigo"]


def test_inspect_reads_select_label_without_option_text(page, site):
    open_form(page, site.url("/classic"))
    field = _by_label(inspect_form(page), "Nível de inglês")
    assert field.kind == "select"
    assert field.options == ["Básico", "Avançado"]
    assert "Avançado" not in field.label


def test_inspect_reads_combobox_options_and_skips_helper_input(page, site):
    open_form(page, site.url("/combobox"))
    fields = inspect_form(page)
    assert len(fields) == 1
    assert fields[0].kind == "combobox"
    assert fields[0].options == ["Superior incompleto", "Superior completo"]
    assert fields[0].required is True


def test_inspect_label_excludes_hidden_helper_text(page, site):
    open_form(page, site.url("/lever-labels"))
    assert inspect_form(page)[0].label == "Current location ✱"


def test_inspect_textarea_without_label_uses_question_text(page, site):
    open_form(page, site.url("/lever-labels"))
    assert inspect_form(page)[1].label == "Conte sobre você✱"


def test_inspect_waits_for_combobox_options_that_load_late(page, site):
    open_form(page, site.url("/slow-combobox"))
    assert inspect_form(page)[0].options == ["Superior incompleto"]


def test_submit_clicks_the_application_button_not_the_cookie_banner(page, site):
    open_form(page, site.url("/cookie-buttons"))
    page.fill('[name="name"]', "Ana")
    assert submit_form(page, timeout_ms=3000).status == "confirmed"
    assert [path for path, _ in site.posts] == ["/submit"]


def test_submit_never_clicks_cookie_button_when_it_is_the_only_one(page, site):
    open_form(page, site.url("/only-cookie-buttons"))
    assert submit_form(page, timeout_ms=300).status == "invalid"
    assert site.posts == []


def test_submit_accepts_generic_submit_button_inside_the_form(page, site):
    open_form(page, site.url("/generic-submit"))
    page.fill('[name="name"]', "Ana")
    assert submit_form(page, timeout_ms=3000).status == "confirmed"


def test_submit_finds_button_of_type_button_by_its_text(page, site):
    open_form(page, site.url("/lever-labels"))
    assert submit_form(page, timeout_ms=3000).status == "confirmed"
    assert [path for path, _ in site.posts] == ["/submit"]


def test_redirect_to_thanks_page_counts_as_confirmed(page, site):
    open_form(page, site.url("/thanks-redirect"))
    page.fill('[name="name"]', "Ana")
    assert submit_form(page, timeout_ms=3000).status == "confirmed"


def test_open_form_follows_apply_link(page, site):
    open_form(page, site.url("/job"))
    assert page.url.endswith("/classic")
    assert inspect_form(page)


def test_open_form_stays_when_there_is_no_apply_link(page, site):
    open_form(page, site.url("/no-form"))
    assert inspect_form(page) == []


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/visible-captcha", "captcha"),
        ("/cloudflare", "captcha"),
        ("/login", "login"),
        ("/classic", None),
        ("/hidden-captcha", None),
    ],
)
def test_detect_obstacle(page, site, path, expected):
    open_form(page, site.url(path))
    assert detect_obstacle(page) == expected


def _classic_plan(resume_path):
    def action(key, kind, label, value):
        return FillAction(key, kind, label, value, "teste")

    return FillPlan(
        actions=[
            action('[name="resume"]', "file", "CV", str(resume_path)),
            action('[name="name"]', "text", "Nome", "Ana Souza"),
            action('[name="email"]', "email", "Email", "ana@real.com"),
            action('[name="phone"]', "text", "Fone", "11 91234-5678"),
            action('[id="why"]', "textarea", "Projeto", "Fiz uma API"),
            action('[name="py"]', "radio", "Python", "Não"),
            action('[name="ingles"]', "select", "Inglês", "Avançado"),
        ]
    )


def test_fill_sets_every_kind_of_field_without_submitting(page, site, tmp_path):
    resume = tmp_path / "curriculo.docx"
    resume.write_bytes(b"conteudo")
    open_form(page, site.url("/classic"))
    assert fill_form(page, _classic_plan(resume)) == []
    assert page.input_value('[name="name"]') == "Ana Souza"
    assert page.input_value('[id="why"]') == "Fiz uma API"
    assert page.input_value('[name="ingles"]') == "Avançado"
    assert page.locator('[name="py"]').nth(1).is_checked()
    assert page.evaluate("() => document.querySelector('[name=resume]').files[0].name") == "curriculo.docx"
    assert site.posts == []


def test_fill_picks_combobox_option(page, site):
    open_form(page, site.url("/combobox"))
    plan = FillPlan(actions=[FillAction('[id="grau"]', "combobox", "Grau", "Superior completo", "teste")])
    assert fill_form(page, plan) == []
    assert page.input_value('[id="grau_valor"]') == "Superior completo"


def test_fill_reports_combobox_option_that_does_not_exist(page, site):
    open_form(page, site.url("/combobox"))
    plan = FillPlan(actions=[FillAction('[id="grau"]', "combobox", "Grau", "Doutorado", "teste")])
    problems = fill_form(page, plan)
    assert problems and "Grau" in problems[0]


def test_fill_reports_field_that_disappeared(page, site):
    open_form(page, site.url("/classic"))
    plan = FillPlan(actions=[FillAction('[name="sumiu"]', "text", "Sumiu", "x", "teste")])
    assert "Sumiu" in fill_form(page, plan, timeout_ms=300)[0]


def _fill_classic(page, site, path, tmp_path):
    resume = tmp_path / "curriculo.docx"
    resume.write_bytes(b"conteudo")
    open_form(page, site.url(path))
    assert fill_form(page, _classic_plan(resume)) == []


def test_submit_sends_form_once_with_values_and_file(page, site, tmp_path):
    _fill_classic(page, site, "/classic", tmp_path)
    result = submit_form(page, timeout_ms=3000)
    assert result.status == "confirmed"
    assert len(site.posts) == 1
    body = site.posts[0][1]
    assert b"Ana Souza" in body
    assert b'filename="curriculo.docx"' in body


def test_submit_without_confirmation_is_uncertain(page, site, tmp_path):
    _fill_classic(page, site, "/classic-silent", tmp_path)
    assert submit_form(page, timeout_ms=800).status == "uncertain"


def test_submit_with_validation_error_is_invalid(page, site, tmp_path):
    _fill_classic(page, site, "/classic-invalid", tmp_path)
    assert submit_form(page, timeout_ms=3000).status == "invalid"


def test_submit_that_triggers_captcha_stops(page, site, tmp_path):
    _fill_classic(page, site, "/classic-captcha", tmp_path)
    assert submit_form(page, timeout_ms=3000).status == "captcha"


def test_submit_without_button_is_invalid_and_sends_nothing(page, site):
    open_form(page, site.url("/no-form"))
    assert submit_form(page, timeout_ms=300).status == "invalid"
    assert site.posts == []
