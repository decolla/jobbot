"""Banco de histórias para perguntas comportamentais de entrevista (formato STAR), tiradas só dos documentos.

Cada história cita trechos literais dos documentos. História com evidência que não existe, número ou nome
que não está nos documentos é descartada e listada, para o usuário saber o que faltou. A reflexão ("o que
aprendi") fica em branco: só o usuário sabe.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from jobbot.apply.text_tailor import unsupported_terms
from jobbot.models import match_key

SYSTEM = (
    "Você ajuda um estudante a se preparar para entrevistas, transformando fatos dos documentos dele em "
    "histórias no formato STAR. Use somente o que está em <fatos>. Não invente números, nomes, ferramentas, "
    "pessoas nem resultados; se um fato não tem resultado escrito, descreva o resultado sem números."
)


class Story(BaseModel):
    title: str
    themes: list[str] = Field(description="Perguntas que a história responde: trabalho em equipe, desafio técnico, "
                                           "aprender algo novo, conflito, liderança...")
    situation: str
    task: str
    action: str = Field(description="O que ele fez, na primeira pessoa")
    result: str
    evidence: list[str] = Field(description="Trechos copiados literalmente de <fatos> que sustentam a história")


class StoryBank(BaseModel):
    stories: list[Story] = Field(description="De 4 a 8 histórias, cada uma de um fato diferente")


def _problem(story: Story, facts: str) -> str | None:
    facts_key = match_key(facts)
    if not story.evidence:
        return "sem evidência"
    missing = [quote for quote in story.evidence if match_key(quote) not in facts_key]
    if missing:
        return f"evidência que não está nos documentos: {missing[0]!r}"
    unsupported = unsupported_terms(" ".join([story.situation, story.task, story.action, story.result]), facts)
    if unsupported:
        return f"fora dos documentos: {', '.join(unsupported)}"
    return None


def build_stories(facts: str, llm, model: str) -> tuple[list[Story], list[tuple[str, str]]]:
    prompt = (
        "Escreva de 4 a 8 histórias STAR para perguntas comportamentais de entrevista, cada uma a partir de um "
        "fato diferente dos documentos, na primeira pessoa e em português.\n\n"
        f"<fatos>\n{facts}\n</fatos>"
    )
    bank = llm.ask(prompt, StoryBank, model=model, system=SYSTEM)
    kept, dropped = [], []
    for story in bank.stories:
        problem = _problem(story, facts)
        if problem:
            dropped.append((story.title, problem))
        else:
            kept.append(story)
    return kept, dropped


def stories_path(root: Path) -> Path:
    return root / "reports" / "historias.md"


def write_stories(root: Path, kept: list[Story], dropped: list[tuple[str, str]]) -> Path:
    lines = [
        "# Suas histórias para entrevista",
        "",
        "Histórias no formato STAR (situação, tarefa, ação, resultado), tiradas só dos seus documentos.",
        "Treine em voz alta. A reflexão, o que você aprendeu, só você pode completar.",
        "",
    ]
    for story in kept:
        lines += [
            f"## {story.title}",
            "",
            f"Responde a: {', '.join(story.themes)}",
            "",
            f"- **Situação:** {story.situation}",
            f"- **Tarefa:** {story.task}",
            f"- **Ação:** {story.action}",
            f"- **Resultado:** {story.result}",
            "- **Reflexão:** _(complete você: o que aprendeu, o que faria diferente)_",
            "",
            "Base nos seus documentos: " + " · ".join(f'"{" ".join(quote.split())}"' for quote in story.evidence),
            "",
        ]
    if dropped:
        lines += ["## Descartadas pela conferência", ""]
        lines += [f"- {title}: {reason}" for title, reason in dropped]
        lines.append("")
    path = stories_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
