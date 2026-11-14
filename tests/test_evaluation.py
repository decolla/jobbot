"""Avaliação estruturada: requisitos com importância e evidência conferidas pelo código."""

from datetime import date

from jobbot.models import Job
from jobbot.scoring.evaluation import audit, render_evaluation
from jobbot.scoring.llm_scorer import Gap, Requirement, ScoreResult

JD = "Requisitos: experiência com Python é obrigatória. Desejável: Docker. Atuar com APIs REST."
FACTS = "Construiu APIs robustas em .NET (C#) e pipelines comApache Kafka. Stack: Python, Docker."


def _job(**over):
    base = dict(title="Estágio Dev", company="Acme", description=JD, url="https://a.io/1", source="lever")
    return Job(**{**base, **over})


def _req(**over):
    base = dict(requirement="Python", importance="critical", basis="stated", jd_quote="experiência com Python é obrigatória",
                match="strong", evidence="Stack: Python")
    return Requirement(**{**base, **over})


def _result(*requirements, **over):
    return ScoreResult(**{"score": 80, "reason": "boa aderência", "requirements": list(requirements), **over})


def test_verified_requirement_is_kept():
    result, notes = audit(_result(_req()), _job(), FACTS, date(2026, 11, 14))
    assert result.requirements[0] == _req()
    assert notes == []


def test_stated_requirement_without_literal_quote_becomes_inferred():
    result, notes = audit(_result(_req(jd_quote="5 anos de Python")), _job(), FACTS, date(2026, 11, 14))
    req = result.requirements[0]
    assert req.basis == "inferred"
    assert req.jd_quote == ""
    assert notes


def test_inferred_requirement_cannot_be_critical_or_high():
    # Um palpite de mercado não pode criar obrigação nem fazer a vaga parecer fora de alcance.
    result, _ = audit(_result(_req(basis="inferred", jd_quote="", importance="high")), _job(), FACTS, date(2026, 11, 14))
    assert result.requirements[0].importance == "meaningful"


def test_match_without_evidence_in_documents_is_downgraded():
    result, notes = audit(_result(_req(evidence="5 anos de Python na Google")), _job(), FACTS, date(2026, 11, 14))
    req = result.requirements[0]
    assert (req.match, req.evidence) == ("partial", "")
    assert any("Python" in note for note in notes)


def test_evidence_matches_despite_pdf_spacing():
    req = _req(requirement="Kafka", jd_quote="Atuar com APIs REST", evidence="pipelines com Apache Kafka")
    result, _ = audit(_result(req), _job(), FACTS, date(2026, 11, 14))
    assert result.requirements[0].match == "strong"


def test_old_posting_adds_legitimacy_signal():
    result, _ = audit(_result(), _job(posted_at="2026-08-01"), FACTS, date(2026, 11, 14))
    assert any("105 dias" in signal for signal in result.legitimacy_signals)


def test_report_sorts_by_importance_and_puts_gaps_first_within_band():
    result = _result(
        _req(requirement="Docker", importance="preferred", basis="structural", jd_quote="", match="strong"),
        _req(requirement="Python", importance="critical", match="strong"),
        _req(requirement="APIs REST", importance="critical", match="missing", evidence=""),
        gaps=[Gap(gap="APIs REST", mitigation="Citar as APIs em .NET")],
        red_flags=["Descrição genérica"],
        legitimacy="caution",
        legitimacy_signals=["sem data de publicação"],
    )
    text = render_evaluation(_job(), 80, result)
    order = [text.index(name) for name in ("| APIs REST", "| Python", "| Docker")]
    assert order == sorted(order)
    assert "Citar as APIs em .NET" in text
    assert "Descrição genérica" in text
    assert "sem data de publicação" in text


def test_report_escapes_table_breaking_text_from_job():
    result = _result(_req(requirement="C | C++\nlinha", jd_quote="experiência com Python é obrigatória"))
    text = render_evaluation(_job(), 80, result)
    row = next(line for line in text.splitlines() if line.startswith("| C"))
    assert "C \\| C++ linha" in row
