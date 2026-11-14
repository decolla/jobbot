import pytest

from jobbot.models import Job, detect_language, detect_modality, normalize_url


def _job(**over):
    base = dict(title="Estágio em Dev", company="Acme", location="São Paulo", url="https://a.io/1", source="greenhouse")
    return Job(**{**base, **over})


def test_hash_ignores_case_accents_and_punctuation():
    a = _job(title="Estágio em Desenvolvimento!", company="ACME S.A.", location="São Paulo")
    b = _job(title="estagio em desenvolvimento", company="acme sa", location="Sao Paulo", url="https://b.io/2", source="gupy")
    assert a.hash == b.hash


@pytest.mark.parametrize("field,value", [("title", "Estágio em Dados"), ("company", "Outra"), ("location", "Recife")])
def test_hash_changes_with_identity_fields(field, value):
    assert _job().hash != _job(**{field: value}).hash


def test_hash_ignores_url_and_source():
    assert _job().hash == _job(url="https://outra.io/9", source="lever").hash


def test_extra_whitespace_from_sources_is_collapsed():
    job = _job(title=" SOFTWARE ENGINEER I .NET", company="Acme\n", location="São  Paulo ")
    assert (job.title, job.company, job.location) == ("SOFTWARE ENGINEER I .NET", "Acme", "São Paulo")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Remoto - Brasil", "remote"),
        ("Remote", "remote"),
        ("Home office", "remote"),
        ("São Paulo - Híbrido", "hybrid"),
        ("Hybrid, Curitiba", "hybrid"),
        ("Presencial em Recife", "onsite"),
        ("On-site", "onsite"),
        ("Florianópolis", "unknown"),
        ("", "unknown"),
    ],
)
def test_detect_modality(text, expected):
    assert detect_modality(text) == expected


def test_normalize_url_drops_tracking_but_keeps_identity_params():
    url = "https://www.indeed.com/viewjob?jk=abc123&utm_source=mail&trk=x#frag"
    assert normalize_url(url) == "https://www.indeed.com/viewjob?jk=abc123"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("We are looking for a software engineering intern to join our team and work with Python.", "en"),
        ("Buscamos uma pessoa estagiária para atuar com desenvolvimento de software no nosso time.", "pt"),
        ("Python, SQL, Docker", "pt"),
        ("", "pt"),
    ],
)
def test_detect_language(text, expected):
    assert detect_language(text) == expected
