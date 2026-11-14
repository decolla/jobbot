"""Confere a avaliação do Claude contra a vaga e os documentos, e escreve o relatório da vaga.

O Claude cita trechos literais da vaga (de onde vem a importância de cada requisito) e dos documentos
(de onde vem a aderência). O código confere os dois: trecho que não existe não sustenta nada.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from jobbot.models import Job, match_key
from jobbot.scoring.llm_scorer import Requirement, ScoreResult

IMPORTANCE_ORDER = ["critical", "high", "meaningful", "preferred", "low_signal"]
IMPORTANCE_LABELS = {"critical": "crítico", "high": "alto", "meaningful": "relevante", "preferred": "desejável",
                     "low_signal": "genérico"}
BASIS_LABELS = {"stated": "a vaga diz", "structural": "pela estrutura", "inferred": "estimado"}
MATCH_LABELS = {"strong": "atende", "partial": "em parte", "missing": "não atende"}
LEGITIMACY_LABELS = {"high": "parece real e ativa", "caution": "sinais mistos", "suspicious": "vários sinais de vaga fantasma"}
OLD_POSTING_DAYS = 60


def _audit_requirement(req: Requirement, job_key: str, facts_key: str, notes: list[str]) -> Requirement:
    changes: dict = {}
    if req.basis == "stated" and (not req.jd_quote.strip() or match_key(req.jd_quote) not in job_key):
        notes.append(f"{req.requirement}: trecho da vaga não encontrado; importância tratada como estimada")
        changes.update(basis="inferred", jd_quote="")
    # Estimativa não pode criar obrigação nem fazer a vaga parecer fora de alcance.
    if changes.get("basis", req.basis) == "inferred" and req.importance in ("critical", "high"):
        changes["importance"] = "meaningful"
    if req.match != "missing" and (not req.evidence.strip() or match_key(req.evidence) not in facts_key):
        notes.append(f"{req.requirement}: evidência não encontrada nos seus documentos")
        changes.update(match="partial" if req.match == "strong" else "missing", evidence="")
    return req.model_copy(update=changes) if changes else req


def audit(result: ScoreResult, job: Job, facts: str, today: date) -> tuple[ScoreResult, list[str]]:
    notes: list[str] = []
    job_key = match_key(f"{job.title}\n{job.description}")
    facts_key = match_key(facts)
    requirements = [_audit_requirement(req, job_key, facts_key, notes) for req in result.requirements]
    signals = list(result.legitimacy_signals)
    if job.posted_at:
        try:
            age = (today - date.fromisoformat(job.posted_at[:10])).days
        except ValueError:
            age = None
        if age is not None and age > OLD_POSTING_DAYS:
            signals.append(f"publicada há {age} dias")
    return result.model_copy(update={"requirements": requirements, "legitimacy_signals": signals}), notes


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def _sorted(requirements: list[Requirement]) -> list[Requirement]:
    # Importância decrescente e, dentro da mesma faixa, o que falta antes do que já atende.
    return sorted(requirements, key=lambda r: (IMPORTANCE_ORDER.index(r.importance), r.match == "strong"))


def render_evaluation(job: Job, score: int, result: ScoreResult, notes: list[str] = ()) -> str:
    lines = [
        f"# {job.title} ({job.company})",
        "",
        f"- Link: {job.url}",
        f"- Local: {job.location or 'não informado'} · modalidade: {job.modality}",
        f"- Score: {score}. {result.reason}",
        f"- Legitimidade: {LEGITIMACY_LABELS[result.legitimacy]}",
        "",
        "## Requisitos",
        "",
    ]
    if result.requirements:
        lines += ["| Requisito | Peso | Você | Na vaga | Evidência nos seus documentos |", "|---|---|---|---|---|"]
        for req in _sorted(result.requirements):
            weight = f"{IMPORTANCE_LABELS[req.importance]} ({BASIS_LABELS[req.basis]})"
            quote = f'"{_cell(req.jd_quote)}"' if req.jd_quote else "—"
            lines.append(f"| {_cell(req.requirement)} | {weight} | {MATCH_LABELS[req.match]} | {quote} | "
                         f"{_cell(req.evidence) or '—'} |")
    else:
        lines.append("Nenhum requisito listado.")
    lines += ["", "## Lacunas e como lidar", ""]
    lines += [f"- **{gap.gap}**: {gap.mitigation}" for gap in result.gaps] or ["Nenhuma lacuna importante."]
    lines += ["", "## Alertas", ""]
    lines += [f"- {flag}" for flag in result.red_flags] or ["Nenhum."]
    lines += ["", "## Legitimidade da vaga", "",
              "Sinais para você decidir, não uma acusação: muitos têm explicação legítima.", ""]
    lines += [f"- {signal}" for signal in result.legitimacy_signals] or ["- Nenhum sinal de alerta."]
    if notes:
        lines += ["", "## Conferência automática", ""]
        lines += [f"- {note}" for note in notes]
    return "\n".join(lines) + "\n"


def evaluation_path(root: Path, job_id: int) -> Path:
    return root / "reports" / "vagas" / f"{job_id}.md"


def write_evaluation(root: Path, job_id: int, job: Job, score: int, result: ScoreResult, notes: list[str]) -> Path:
    path = evaluation_path(root, job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_evaluation(job, score, result, notes), encoding="utf-8")
    return path
