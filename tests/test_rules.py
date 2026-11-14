import pytest

from jobbot.models import Job
from jobbot.profile import Profile
from jobbot.scoring.rules import evaluate, years_required


def _profile(**over):
    data = dict(
        personal=dict(name="A", email="a@real.com", city="São Paulo"),
        objective="Estágio em software",
        seniority=["estagio", "junior"],
        stack=["Python", "SQL", "Git"],
        locations=dict(cities=["São Paulo"], remote=True, hybrid=True, onsite=False),
        keywords=dict(positive=["desenvolvedor", "software", "ti"], negative=["vendas"]),
        max_years_required=2,
        documents=dict(resume="documents/cv.pdf"),
    )
    data.update(over)
    return Profile.model_validate(data)


def _job(title="Estágio em Desenvolvedor", location="São Paulo", modality="hybrid", description=""):
    return Job(title=title, company="Acme", location=location, modality=modality, description=description,
               url="https://a.io/1", source="lever")


@pytest.mark.parametrize(
    "title",
    [
        "Desenvolvedor Sênior",
        "Senior Software Engineer",
        "Desenvolvedor Pleno",
        "Tech Lead de Software",
        "Software Engineering Manager",
        "Staff Software Engineer",
        "Especialista em Software",
        "Software Engineer III",
        "Data Scientist II | Dados",
        "[Job - 32643] Mid DevOps Engineer, Brazil",
        "[Job 32463] Mid level, QA Automation",
        "[Job-32584] Mid-Level Java/React Developer, Brazil",
        "[Job-31573] AI Engineer Master",
        "[Job-32678] Master Backend Developer (Kotlin/Java), Brasil",
    ],
)
def test_senior_titles_are_discarded(title):
    result = evaluate(_job(title=title), _profile())
    assert not result.passed
    assert "senioridade" in result.reason


@pytest.mark.parametrize(
    "title", ["Estágio em Software", "Estagiário(a) Desenvolvedor", "Desenvolvedor Júnior", "Software Engineer Jr", "Software Intern"]
)
def test_entry_level_titles_pass(title):
    assert evaluate(_job(title=title), _profile()).passed


def test_engineer_level_one_passes():
    assert evaluate(_job(title="Software Engineer I"), _profile()).passed


def test_title_without_level_passes_to_llm():
    assert evaluate(_job(title="Desenvolvedor Python"), _profile()).passed


def test_internal_audit_is_not_mistaken_for_internship():
    assert not evaluate(_job(title="Software Analyst Interna Sênior"), _profile()).passed


def test_level_not_wanted_by_profile_is_discarded():
    result = evaluate(_job(title="Desenvolvedor Júnior"), _profile(seniority=["estagio"]))
    assert not result.passed
    assert "senioridade" in result.reason


@pytest.mark.parametrize(
    "title",
    [
        "[Exclusiva PcD] Desenvolvedor",
        "Desenvolvedor | Vaga Afirmativa para Mulheres Pretas e Pardas",
        "Banco de Talentos Software - Comunidade Negra",
        "Estágio Software - Vaga Afirmativa para Pessoas com Deficiência",
    ],
)
def test_affirmative_jobs_are_never_automatic(title):
    result = evaluate(_job(title=title), _profile())
    assert not result.passed
    assert "afirmativa" in result.reason


def test_negative_keyword_in_title_discards_regardless_of_accent_or_case():
    profile = _profile(keywords=dict(positive=["software"], negative=["contábil"]))
    result = evaluate(_job(title="Estágio Software CONTABIL"), profile)
    assert not result.passed
    assert "contábil" in result.reason


def test_positive_keyword_in_title_passes():
    assert evaluate(_job(title="Analista de TI Júnior"), _profile()).passed


def test_keyword_does_not_match_inside_another_word():
    # "ti" está dentro de "Atividades" e "Estatística", mas não é a palavra TI.
    result = evaluate(_job(title="Estágio em Atividades de Estatística"), _profile())
    assert not result.passed
    assert "palavra-chave" in result.reason


def test_two_stack_terms_in_description_pass_without_title_keyword():
    job = _job(title="Analista de BI Júnior", description="Usamos Python e SQL no dia a dia.")
    assert evaluate(job, _profile()).passed


def test_one_stack_term_in_description_is_not_enough():
    job = _job(title="Analista de Atendimento Júnior", description="Noções de SQL são um diferencial.")
    assert not evaluate(job, _profile()).passed


def test_java_does_not_match_javascript():
    profile = _profile(stack=["Java", "Spring"], keywords=dict(positive=["java"], negative=[]))
    assert not evaluate(_job(title="Estágio JavaScript"), profile).passed


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Exigimos 5 anos de experiência com Java", 5),
        ("5+ years of experience", 5),
        ("mínimo de 4 anos em backend", 4),
        ("experiência de 3 anos", 3),
        ("Somos uma empresa com 20 anos de mercado", 0),
        ("Bolsa de 2 anos de duração", 0),
        ("3 anos de experiência ou 7+ years of experience", 7),
        ("Com 30 anos de experiência em transformação tecnológica", 0),
        ("Temos 10 anos de experiência no setor", 0),
        ("Nossa empresa tem 12 anos de experiência", 0),
    ],
)
def test_years_required(text, expected):
    assert years_required(text) == expected


def test_too_many_years_required_is_discarded():
    job = _job(description="Requisito: 5 anos de experiência")
    result = evaluate(job, _profile())
    assert not result.passed
    assert "5 anos" in result.reason


def test_years_at_the_limit_pass():
    assert evaluate(_job(description="2 anos de experiência"), _profile()).passed


def test_remote_passes_when_profile_accepts_remote():
    assert evaluate(_job(location="Brasil", modality="remote"), _profile()).passed


def test_remote_is_discarded_when_profile_rejects_remote():
    profile = _profile(locations=dict(cities=["São Paulo"], remote=False, hybrid=True, onsite=True))
    assert not evaluate(_job(location="Brasil", modality="remote"), profile).passed


def test_onsite_is_discarded_when_profile_rejects_onsite():
    assert not evaluate(_job(modality="onsite"), _profile()).passed


def test_hybrid_in_other_city_is_discarded():
    result = evaluate(_job(location="Recife", modality="hybrid"), _profile())
    assert not result.passed
    assert "Recife" in result.reason


def test_city_match_ignores_accents():
    assert evaluate(_job(location="Sao Paulo, Brasil", modality="hybrid"), _profile()).passed


def test_unknown_modality_in_other_city_is_discarded():
    assert not evaluate(_job(location="Curitiba", modality="unknown"), _profile()).passed


def test_unknown_modality_with_country_only_passes():
    assert evaluate(_job(location="Brazil", modality="unknown"), _profile()).passed


def test_remote_abroad_is_discarded():
    result = evaluate(_job(location="United States (Remote)", modality="remote"), _profile())
    assert not result.passed
    assert "United States" in result.reason


def test_empty_location_passes_to_llm():
    assert evaluate(_job(location="", modality="unknown"), _profile()).passed


def test_dealbreaker_in_description_discards_job():
    profile = _profile(dealbreakers=["contrato PJ"])
    result = evaluate(_job(description="Contratação via Contrato PJ, 40h semanais."), profile)
    assert (result.passed, result.reason) == (False, "critério eliminatório seu: contrato PJ")


def test_job_without_dealbreaker_passes():
    profile = _profile(dealbreakers=["contrato PJ"])
    assert evaluate(_job(description="Python e SQL, regime CLT."), profile).passed


def test_master_data_is_a_field_not_a_level():
    assert evaluate(_job(title="Estágio Desenvolvedor Master Data"), _profile()).passed
