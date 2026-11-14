"""Currículo adaptado: o conteúdo vem só do currículo original; a vaga muda apenas a ordem e o resumo."""

import pytest

from jobbot.apply.cv_builder import (
    CVEntry,
    CVError,
    CVPlan,
    CVSection,
    StructuredCV,
    SummaryClaim,
    coverage,
    extract_cv,
    load_structured_cv,
    render_html,
    tailor_cv,
)
from jobbot.models import Job
from jobbot.profile import Profile
from jobbot.scoring.llm_scorer import Requirement, ScoreResult

RESUME = (
    "OBJETIVO\nEngenheiro de Software focado em sistemas distribuídos.\n"
    "EXPERIÊNCIA\nEngenheiro de Dados, Laboratório X, 2025 - Presente\n"
    "•Pipelines comApache Kafka e Avro.\n•APIs em .NET (C#) com MongoDB.\n•Servidores Linux com Docker.\n"
    "COMPETÊNCIAS\nLinguagens: C#, Python, SQL\n"
)


def _cv(summary="Engenheiro de Software focado em sistemas distribuídos."):
    return StructuredCV(
        summary=summary,
        sections=[
            CVSection(heading="Experiência", entries=[CVEntry(
                title="Engenheiro de Dados", organization="Laboratório X", period="2025 - Presente",
                bullets=["Pipelines com Apache Kafka e Avro.", "APIs em .NET (C#) com MongoDB.", "Servidores Linux com Docker."],
            )]),
            CVSection(heading="Competências", entries=[CVEntry(title="Linguagens", bullets=["C#, Python, SQL"])]),
        ],
    )


def _job():
    return Job(title="Estágio Backend", company="Acme", url="https://a.io/1", source="lever",
               description="Buscamos estagiário com Docker e Linux. Desejável .NET.")


def _profile():
    return Profile.model_validate(dict(
        personal=dict(name="Ana <b>Lima</b>", email="ana@real.com", phone="11 91234-5678", city="Uberlândia",
                      linkedin="https://linkedin.com/in/ana"),
        objective="Estágio", seniority=["estagio"], documents=dict(resume="documents/cv.pdf"),
    ))


class FakeLLM:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def ask(self, prompt, schema, model, system=None):
        self.calls.append(dict(prompt=prompt, schema=schema, model=model))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _texts(cv):
    return sorted(b for s in cv.sections for e in s.entries for b in e.bullets)


def test_extraction_with_literal_text_is_accepted():
    assert extract_cv(RESUME, FakeLLM(_cv()), "sonnet") == _cv()


def test_extraction_with_invented_line_is_retried_then_refused():
    invented = _cv()
    invented.sections[0].entries[0].bullets.append("Liderou time de 10 pessoas na Google.")
    llm = FakeLLM(invented, invented)
    with pytest.raises(CVError, match="Google"):
        extract_cv(RESUME, llm, "sonnet")
    assert len(llm.calls) == 2
    assert "Google" in llm.calls[1]["prompt"]  # a segunda tentativa recebe o motivo da recusa


def test_structured_cv_is_cached_until_the_resume_changes(tmp_path):
    llm = FakeLLM(_cv(), _cv())
    load_structured_cv(tmp_path, "documents/cv.pdf", RESUME, llm, "sonnet")
    load_structured_cv(tmp_path, "documents/cv.pdf", RESUME, llm, "sonnet")
    assert len(llm.calls) == 1
    load_structured_cv(tmp_path, "documents/cv.pdf", RESUME + "\nCERTIFICAÇÕES\nKafka", llm, "sonnet")
    assert len(llm.calls) == 2


def test_tailoring_only_reorders_existing_content():
    plan = CVPlan(bullet_order=[[[2, 0, 1]], [[0]]])
    tailored = tailor_cv(_cv(), _job(), RESUME, FakeLLM(plan), "sonnet")
    assert tailored.sections[0].entries[0].bullets[0] == "Servidores Linux com Docker."
    assert _texts(tailored) == _texts(_cv())


def test_invalid_or_missing_positions_keep_every_bullet():
    plan = CVPlan(bullet_order=[[[2, 2, 9]]])  # repetido, inexistente, e faltam itens e seções
    tailored = tailor_cv(_cv(), _job(), RESUME, FakeLLM(plan), "sonnet")
    assert tailored.sections[0].entries[0].bullets[0] == "Servidores Linux com Docker."
    assert _texts(tailored) == _texts(_cv())


def test_summary_with_evidence_replaces_the_original():
    summary = "Engenheiro de Software com Docker e Linux."
    plan = CVPlan(summary=summary, claims=[SummaryClaim(text="Docker e Linux", evidence="Servidores Linux com Docker")])
    assert tailor_cv(_cv(), _job(), RESUME, FakeLLM(plan), "sonnet").summary == summary


@pytest.mark.parametrize(
    "summary",
    [
        "Engenheiro de Software com Kubernetes e Docker.",  # termo técnico que não está no currículo
        "Engenheiro de Software com 5 anos de Docker.",  # número que não está no currículo
    ],
)
def test_summary_with_unsupported_term_keeps_the_original(summary):
    plan = CVPlan(summary=summary, claims=[SummaryClaim(text="Docker", evidence="Servidores Linux com Docker")])
    assert tailor_cv(_cv(), _job(), RESUME, FakeLLM(plan), "sonnet").summary == _cv().summary


def test_html_has_contact_and_sections_and_escapes_text():
    cv = _cv(summary="Engenheiro <script>alert(1)</script>")
    html = render_html(cv, _profile(), "pt")
    assert "<script>" not in html
    assert "Ana &lt;b&gt;Lima&lt;/b&gt;" in html
    assert "ana@real.com" in html and "linkedin.com/in/ana" in html
    assert html.index("Experiência") < html.index("Competências")


def test_coverage_counts_important_requirements_with_evidence():
    evaluation = ScoreResult(score=80, reason="ok", requirements=[
        Requirement(requirement="Docker", importance="critical", basis="stated", jd_quote="Docker", match="strong",
                    evidence="Servidores Linux com Docker"),
        Requirement(requirement="Kubernetes", importance="high", basis="stated", jd_quote="Kubernetes", match="missing"),
        Requirement(requirement="Inglês", importance="preferred", basis="stated", jd_quote="Inglês", match="missing"),
    ])
    assert coverage(evaluation) == (["Docker"], ["Kubernetes"])


def test_unsupported_term_at_start_of_sentence_is_also_caught():
    plan = CVPlan(summary="GraphQL e Docker no dia a dia.", claims=[SummaryClaim(text="Docker", evidence="Servidores Linux com Docker")])
    assert tailor_cv(_cv(), _job(), RESUME, FakeLLM(plan), "sonnet").summary == _cv().summary


def test_extraction_tidies_line_breaks_left_by_the_pdf():
    raw = _cv()
    raw.sections[0].entries[0].bullets[0] = "Pipelines com Apache\nKafka e Avro."
    resume = RESUME.replace("comApache Kafka", "comApache\nKafka")
    assert extract_cv(resume, FakeLLM(raw), "sonnet").sections[0].entries[0].bullets[0] == "Pipelines com Apache Kafka e Avro."


def test_hyphen_split_by_line_break_is_joined():
    raw = _cv()
    raw.sections[0].entries[0].bullets[2] = "Servidores Linux com Doc-\nker."
    assert extract_cv(RESUME, FakeLLM(raw), "sonnet").sections[0].entries[0].bullets[2] == "Servidores Linux com Docker."


def test_entry_with_a_single_line_and_no_details_is_rendered_inline():
    html = render_html(_cv(), _profile(), "pt")
    assert '<span class="head">Linguagens:</span> C#, Python, SQL' in html
    assert "<li>Pipelines com Apache Kafka e Avro.</li>" in html  # experiência continua em tópicos
