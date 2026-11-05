from datetime import date

import pytest

from jobbot.actions.detector import extract_deadline, extract_link
from jobbot.tracking.stages import classify


@pytest.mark.parametrize(
    "subject,body,expected",
    [
        ("Recebemos sua candidatura", "Obrigado por se candidatar", "screening"),
        ("Thank you for applying to Acme", "We received your application", "screening"),
        ("Sua candidatura está em análise", "", "screening"),
        ("Próxima etapa", "Você foi convidado para o teste online de lógica", "test"),
        ("Coding challenge", "Please complete the HackerRank assessment", "test"),
        ("Desafio técnico", "Segue o desafio técnico da Acme", "test"),
        ("Avaliação", "Faça a avaliação técnica até sexta", "test"),
        ("Etapa de vídeo", "Grave um vídeo de apresentação", "video"),
        ("Video interview", "Complete your HireVue video interview", "video"),
        ("Entrevista", "Queremos agendar uma entrevista com você", "interview"),
        ("Interview invitation", "We'd like to schedule an interview", "interview"),
        ("Parabéns", "Temos uma proposta de estágio para você", "offer"),
        ("Offer", "We are pleased to offer you the position", "offer"),
        ("Atualização", "Infelizmente não seguiremos com sua candidatura", "rejected"),
        ("Update", "Unfortunately we will not be moving forward", "rejected"),
        ("Retorno", "Agradecemos, mas você não foi selecionado", "rejected"),
    ],
)
def test_classify_stage(subject, body, expected):
    assert classify(subject, body).stage == expected


def test_rejection_wins_over_words_of_other_stages():
    guess = classify("Resultado", "Infelizmente você não passou no teste online e na entrevista")
    assert guess.stage == "rejected"


def test_bare_unfortunately_is_not_a_rejection():
    assert classify("Entrevista", "Infelizmente precisamos remarcar a entrevista").stage == "interview"


def test_steps_that_may_happen_later_do_not_count():
    guess = classify("Candidatura", "Recebemos sua candidatura. O processo pode incluir teste online e entrevista.")
    assert guess.stage == "screening"


def test_invitation_with_pode_still_counts():
    assert classify("Próxima etapa", "Você pode fazer o teste online até sexta").stage == "test"


def test_unrelated_message_is_not_classified():
    assert classify("Newsletter", "Confira as novidades da semana") is None


def test_needs_human_only_for_stages_the_user_must_do():
    assert classify("x", "teste online").needs_human is True
    assert classify("x", "agendar uma entrevista").needs_human is True
    assert classify("x", "grave um vídeo de apresentação").needs_human is True
    assert classify("x", "recebemos sua candidatura").needs_human is False
    assert classify("x", "infelizmente não seguiremos").needs_human is False


TODAY = date(2026, 11, 5)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Faça o teste até 10/11/2026.", "2026-11-10"),
        ("Prazo: 10/11", "2026-11-10"),
        ("Responda até dia 15/1", "2027-01-15"),
        ("Deadline: 2026-11-20", "2026-11-20"),
        ("Complete by November 12, 2026", "2026-11-12"),
        ("Você tem até 12 de novembro para concluir", "2026-11-12"),
        ("Publicado em 01/10/2026. Prazo até 09/11/2026.", "2026-11-09"),
        ("Entrevista agendada para 12/11 às 14h", "2026-11-12"),
        ("Responda até as 18h do dia 10/11", "2026-11-10"),
        ("Sem data nenhuma", None),
        ("Vaga publicada em 01/10/2026", None),
        ("Prazo: 99/99/2026", None),
    ],
)
def test_extract_deadline(text, expected):
    assert extract_deadline(text, TODAY) == expected


def test_extract_link_prefers_assessment_platform():
    text = "Veja https://acme.com/sobre e faça https://www.hackerrank.com/test/abc123 agora"
    assert extract_link(text) == "https://www.hackerrank.com/test/abc123"


def test_extract_link_ignores_unsubscribe_and_images():
    text = "https://acme.com/unsubscribe?x=1 https://acme.com/logo.png https://acme.com/entrevista/9"
    assert extract_link(text) == "https://acme.com/entrevista/9"


def test_extract_link_trims_trailing_punctuation():
    assert extract_link("Acesse https://acme.com/teste/1.") == "https://acme.com/teste/1"


def test_extract_link_without_url_returns_none():
    assert extract_link("Sem links") is None
