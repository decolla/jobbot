from pathlib import Path

import pytest

from jobbot.apply.form_plan import FormField, match_option, plan_fill
from jobbot.apply.text_tailor import QuestionAnswer
from jobbot.profile import Profile

RESUME = Path("documents/curriculo.pdf")


def _profile(**over):
    data = dict(
        personal=dict(
            name="Ana Maria Souza", email="ana@real.com", phone="+55 11 91234-5678", city="São Paulo", state="SP",
            linkedin="https://linkedin.com/in/ana", github="https://github.com/ana", portfolio="",
        ),
        objective="Estágio",
        seniority=["estagio"],
        documents=dict(resume=str(RESUME)),
    )
    data.update(over)
    return Profile.model_validate(data)


def _field(label, kind="text", required=True, key=None, options=()):
    return FormField(key=key or f'[name="{label}"]', kind=kind, label=label, required=required, options=list(options))


def _plan(*fields, profile=None, **kwargs):
    return plan_fill(list(fields), profile or _profile(), RESUME, **kwargs)


def _value(plan, label):
    return next((a.value for a in plan.actions if a.label == label), None)


@pytest.mark.parametrize(
    "label,key,expected",
    [
        ("Full name ✱", '[name="name"]', "Ana Maria Souza"),
        ("Nome completo*", '[name="x"]', "Ana Maria Souza"),
        ("Nome*", '[id="first_name"]', "Ana"),
        ("Sobrenome*", '[id="last_name"]', "Maria Souza"),
        ("E-mail*", '[id="email"]', "ana@real.com"),
        ("Email ✱", '[name="email"]', "ana@real.com"),
        ("Telefone*", '[id="phone"]', "+55 11 91234-5678"),
        ("Phone ✱", '[name="phone"]', "+55 11 91234-5678"),
        ("LinkedIn URL ✱", '[name="urls[LinkedIn]"]', "https://linkedin.com/in/ana"),
        ("Qual é o seu perfil do Linkedin?", '[id="q1"]', "https://linkedin.com/in/ana"),
        ("GitHub URL", '[name="urls[GitHub]"]', "https://github.com/ana"),
        ("Localização (Cidade)*", '[id="candidate-location"]', "São Paulo"),
        ("Current location", '[name="location"]', "São Paulo"),
    ],
)
def test_known_fields_use_profile_data(label, key, expected):
    plan = _plan(_field(label, key=key))
    assert _value(plan, label) == expected
    assert plan.blockers == []


def test_resume_input_gets_the_resume_file():
    plan = _plan(_field("Anexar", kind="file", required=False, key='[id="resume"]'))
    assert _value(plan, "Anexar") == str(RESUME)


def test_country_picks_brazil_from_options():
    plan = _plan(_field("País*", kind="combobox", options=["Argentina", "Brazil", "Chile"]))
    assert _value(plan, "País*") == "Brazil"


@pytest.mark.parametrize(
    "option", ["Brazil +55", "Brazil", "Brasil", "Brasil (+55)"]
)
def test_country_accepts_options_with_phone_code(option):
    plan = _plan(_field("Country*", kind="combobox", options=["Bolivia +591", option, "Chile +56"]))
    assert _value(plan, "Country*") == option


def test_required_field_without_label_blocks_instead_of_asking_llm():
    plan = _plan(_field("", key='[name="cards[a][field7]"]'))
    assert plan.questions == []
    assert any("sem rótulo" in b for b in plan.blockers)


@pytest.mark.parametrize(
    "label",
    [
        "Qual é a sua pretensão salarial?*",
        "Salary expectation",
        "Expectativa de remuneração",
    ],
)
def test_salary_question_always_blocks_when_required(label):
    plan = _plan(_field(label))
    assert plan.actions == []
    assert any("salarial" in b for b in plan.blockers)


def test_salary_is_blocked_even_with_saved_answer():
    profile = _profile(saved_answers=[dict(match="pretensão salarial", answer="3000")])
    plan = _plan(_field("Qual é a sua pretensão salarial?*"), profile=profile)
    assert plan.actions == []
    assert plan.blockers


def test_optional_salary_question_is_left_blank():
    plan = _plan(_field("Pretensão salarial", required=False))
    assert plan.actions == []
    assert plan.blockers == []
    assert "Pretensão salarial" in plan.skipped


@pytest.mark.parametrize(
    "label",
    [
        "Qual é o seu CPF? (Formato: XXX.XXX.XXX-XX) *",
        "Qual é o seu endereço completo com CEP? *",
        "Você é uma pessoa com deficiência?*",
        "Qual a sua raça/cor?",
        "Orientação sexual",
        "Gender",
        "Data de nascimento",
        "Consentimento - Diversidade e Inclusão (D&I)*",
        "Estado civil",
        "Possui algum problema de saúde?",
    ],
)
def test_sensitive_required_question_blocks(label):
    plan = _plan(_field(label))
    assert plan.actions == []
    assert any("sensível" in b for b in plan.blockers)
    assert plan.questions == []


def test_sensitive_content_in_options_also_blocks():
    plan = _plan(_field("Selecione uma opção", kind="radio", options=["Não me identifico como PcD", "Física"]))
    assert any("sensível" in b for b in plan.blockers)


def test_optional_sensitive_question_is_left_blank():
    plan = _plan(_field("Gender", required=False))
    assert plan.actions == []
    assert plan.blockers == []


def test_saved_answer_resolves_sensitive_question():
    profile = _profile(saved_answers=[dict(match="pessoa com deficiencia", answer="Não")])
    plan = _plan(_field("Você é uma pessoa com deficiência?*", kind="combobox", options=["Sim", "Não"]), profile=profile)
    assert _value(plan, "Você é uma pessoa com deficiência?*") == "Não"
    assert plan.blockers == []


def test_saved_answer_not_in_options_blocks():
    profile = _profile(saved_answers=[dict(match="deficiencia", answer="Talvez")])
    plan = _plan(_field("Tem deficiência?", kind="radio", options=["Sim", "Não"]), profile=profile)
    assert plan.actions == []
    assert plan.blockers


def test_password_field_blocks_as_login():
    plan = _plan(_field("Senha", kind="password"), _field("E-mail*", key='[id="email"]'))
    assert any("login" in b for b in plan.blockers)


def test_someone_elses_email_is_not_filled_with_ours():
    label = "Se sim, informe o e-mail corporativo da pessoa que te indicou!"
    plan = _plan(_field(label, required=False))
    assert plan.actions == []


def test_required_missing_profile_data_blocks():
    plan = _plan(_field("Portfolio URL ✱"))
    assert any("Portfolio" in b for b in plan.blockers)


def test_optional_missing_profile_data_is_skipped():
    plan = _plan(_field("Portfolio URL", required=False))
    assert plan.blockers == []
    assert "Portfolio URL" in plan.skipped


def test_optional_unknown_question_is_skipped():
    plan = _plan(_field("Como você nos conheceu?", required=False))
    assert plan.questions == []
    assert "Como você nos conheceu?" in plan.skipped


def test_required_unknown_question_goes_to_llm():
    field = _field("Conte sobre um projeto", kind="textarea")
    plan = _plan(field)
    assert plan.questions == [field]
    assert plan.actions == []


def test_answered_question_is_filled():
    label = "Conte sobre um projeto"
    answers = {label: QuestionAnswer(question=label, can_answer=True, answer="Fiz uma API", evidence="API")}
    plan = _plan(_field(label, kind="textarea"), answers=answers)
    assert _value(plan, label) == "Fiz uma API"
    assert plan.questions == []


def test_question_the_llm_cannot_answer_blocks():
    label = "Conte sobre um projeto"
    answers = {label: QuestionAnswer(question=label, can_answer=False)}
    plan = _plan(_field(label, kind="textarea"), answers=answers)
    assert any(label in b for b in plan.blockers)


def test_answer_outside_options_blocks():
    label = "Nível de inglês"
    answers = {label: QuestionAnswer(question=label, can_answer=True, answer="Fluente", evidence="x")}
    plan = _plan(_field(label, kind="select", options=["Básico", "Avançado"]), answers=answers)
    assert any(label in b for b in plan.blockers)


def test_answer_matching_option_uses_the_option_text():
    label = "Nível de inglês"
    answers = {label: QuestionAnswer(question=label, can_answer=True, answer="avancado", evidence="x")}
    plan = _plan(_field(label, kind="select", options=["Básico", "Avançado"]), answers=answers)
    assert _value(plan, label) == "Avançado"


def test_card_number_field_never_gets_the_cover_letter():
    plan = _plan(_field("Número do cartão", required=False), cover_letter="Minha carta")
    assert _value(plan, "Número do cartão") is None


@pytest.mark.parametrize(
    "label",
    ["Quanto você espera ganhar?", "Expected pay", "Desired hourly rate", "Qual sua expectativa de bolsa?"],
)
def test_other_salary_phrasings_also_block(label):
    plan = _plan(_field(label))
    assert any("pretensão salarial" in blocker for blocker in plan.blockers)
    assert plan.questions == []


def test_cover_letter_textarea_receives_the_letter():
    plan = _plan(_field("Carta de apresentação", kind="textarea", required=False), cover_letter="Olá")
    assert _value(plan, "Carta de apresentação") == "Olá"


def test_required_cover_letter_file_uses_given_file():
    plan = _plan(_field("Anexar", kind="file", key='[id="cover_letter"]'), cover_letter_path=Path("carta.txt"))
    assert _value(plan, "Anexar") == "carta.txt"


def test_unknown_required_file_blocks():
    plan = _plan(_field("Histórico escolar", kind="file"))
    assert any("Histórico" in b for b in plan.blockers)


@pytest.mark.parametrize(
    "answer,options,expected",
    [
        ("Sim", ["Sim", "Não"], "Sim"),
        ("sim", ["Sim", "Não"], "Sim"),
        ("nao", ["Sim", "Não"], "Não"),
        ("Talvez", ["Sim", "Não"], None),
        ("Sim", [], "Sim"),
    ],
)
def test_match_option(answer, options, expected):
    assert match_option(answer, options) == expected
