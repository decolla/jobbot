"""Decide o que fazer com cada campo do formulário. Não toca no navegador.

Ordem de decisão: senha e salário param sempre; resposta salva pelo usuário vem antes de tudo o mais;
dado sensível sem resposta salva para; depois, dados do perfil; o resto vai para o Claude.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from jobbot.models import strip_accents
from jobbot.profile import Profile

if TYPE_CHECKING:
    from jobbot.apply.text_tailor import QuestionAnswer

SALARY = re.compile(
    r"salar|pretensao|remuneracao|compensation|pay expectation|\bwage|expected pay|desired pay|hourly rate"
    r"|(espera|deseja|pretende|gostaria de) (ganhar|receber)|expectativa de (bolsa|ganho)|valor da bolsa"
)
SENSITIVE = re.compile(
    r"\bcpf\b|\brg\b|\bcnh\b|passaporte|passport|nascimento|birth|\bidade\b|\bage\b|endereco|address|\bcep\b|zip code|"
    r"genero|gender|\bsexo\b|sexual|\braca\b|\bcor\b|etnia|\brace\b|ethnic|deficien|disabil|\bpcd\b|saude|health|"
    r"religi|estado civil|marital|filhos|veteran|gravid|pregnan|consent|autorizo|lgpd|declaro|concordo|\bagree\b|"
    r"termos|privacidade|conta bancaria"
)
BRAZIL_NAMES = ("Brasil", "Brazil")
BRAZIL_PREFIXES = ("brasil", "brazil")
# Rótulo longo costuma ser pergunta sobre outra coisa, como "e-mail da pessoa que te indicou".
MAX_KNOWN_LABEL_CHARS = 45


@dataclass
class FormField:
    key: str
    kind: str
    label: str
    required: bool
    options: list[str] = field(default_factory=list)


@dataclass
class FillAction:
    key: str
    kind: str
    label: str
    value: str
    origin: str
    sensitive: bool = False


@dataclass
class FillPlan:
    actions: list[FillAction] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    questions: list[FormField] = field(default_factory=list)


def _plain(text: str) -> str:
    return re.sub(r"\s+", " ", strip_accents(text).replace("*", "").replace("✱", "")).strip()


def match_option(answer: str, options: list[str]) -> str | None:
    if not options:
        return answer
    wanted = _plain(answer)
    return next((option for option in options if _plain(option) == wanted), None)


def _country(options: list[str]) -> str:
    if not options:
        return BRAZIL_NAMES[0]
    # A lista pode trazer o código do telefone junto, como "Brazil +55".
    return next((option for option in options if _plain(option).startswith(BRAZIL_PREFIXES)), "")


def _known_value(item: FormField, label: str, key: str, profile: Profile) -> str | None:
    """Valor vindo do perfil. None: o campo não é um dado conhecido."""
    if len(label) > MAX_KNOWN_LABEL_CHARS:
        return None
    person = profile.personal
    parts = person.name.split()
    both = f"{label} {key}"
    if re.search(r"first_?name|first name|primeiro nome|given name", both):
        return parts[0] if parts else ""
    if re.search(r"last_?name|last name|sobrenome|surname|family name", both):
        return " ".join(parts[1:])
    if re.search(r"full name|nome completo|^nome$|^name$|your name|seu nome", label):
        return person.name
    if item.kind == "email" or re.search(r"^(seu |your )?e-?mail\b", label):
        return person.email
    if re.search(r"telefone|celular|phone|whatsapp|mobile", label):
        return person.phone
    if "linkedin" in label:
        return person.linkedin
    if "github" in label:
        return person.github
    if re.search(r"portfolio|website|site pessoal|personal site", label):
        return person.portfolio
    if re.search(r"^pais\b|^country\b", label):
        return _country(item.options)
    if re.search(r"^estado\b|^uf\b|^state\b", label):
        return person.state
    if re.search(r"cidade|\bcity\b|localizacao|location", label):
        return person.city
    return None


def _plan_file(item: FormField, label: str, key: str, plan: FillPlan, resume_path: Path, cover_letter_path: Path | None) -> None:
    both = f"{label} {key}"
    if re.search(r"cover|\bcarta\b", both):
        if cover_letter_path:
            plan.actions.append(FillAction(item.key, item.kind, item.label, str(cover_letter_path), "carta"))
        elif not item.required:
            plan.skipped.append(item.label)
        return
    if re.search(r"resume|curriculo|\bcv\b", both):
        plan.actions.append(FillAction(item.key, item.kind, item.label, str(resume_path), "curriculo"))
        return
    _leave_blank_or_block(item, plan, f"arquivo que o jobbot não tem: {item.label}")


def _leave_blank_or_block(item: FormField, plan: FillPlan, reason: str) -> None:
    if item.required:
        plan.blockers.append(reason)
    else:
        plan.skipped.append(item.label)


def _saved_answer(label: str, profile: Profile) -> str | None:
    for saved in profile.saved_answers:
        if _plain(saved.match) in label:
            return saved.answer
    return None


def plan_fill(fields: list[FormField], profile: Profile, resume_path: Path, cover_letter: str = "",
              answers: dict[str, QuestionAnswer] | None = None, cover_letter_path: Path | None = None) -> FillPlan:
    """Sem `answers`, lista as perguntas para o Claude. Com `answers`, monta o plano final."""
    plan = FillPlan()
    for item in fields:
        label = _plain(item.label)
        key = _plain(item.key)
        searchable = " ".join([label, *(_plain(option) for option in item.options)])

        if item.kind == "password":
            plan.blockers.append("campo de senha: exige login, faça com jobbot login")
            continue
        if SALARY.search(label):
            _leave_blank_or_block(item, plan, f"pretensão salarial: {item.label}")
            continue

        is_sensitive = SENSITIVE.search(searchable) is not None
        saved = _saved_answer(label, profile)
        if saved is not None:
            value = match_option(saved, item.options)
            if value is None:
                plan.blockers.append(f"resposta salva não está entre as opções: {item.label}")
            else:
                plan.actions.append(FillAction(item.key, item.kind, item.label, value, "resposta salva", is_sensitive))
            continue
        if is_sensitive:
            _leave_blank_or_block(item, plan, f"pergunta sensível sem resposta salva: {item.label}")
            continue

        if item.kind == "file":
            _plan_file(item, label, key, plan, resume_path, cover_letter_path)
            continue
        if not label:
            _leave_blank_or_block(item, plan, f"campo obrigatório sem rótulo: {item.key}")
            continue
        if re.search(r"\bcarta\b|cover letter", label):
            if cover_letter:
                plan.actions.append(FillAction(item.key, item.kind, item.label, cover_letter, "carta"))
            continue

        known = _known_value(item, label, key, profile)
        if known is not None:
            value = match_option(known, item.options) if known else None
            if value:
                plan.actions.append(FillAction(item.key, item.kind, item.label, value, "perfil"))
            else:
                _leave_blank_or_block(item, plan, f"dado que falta no perfil: {item.label}")
            continue

        if not item.required:
            plan.skipped.append(item.label)
        elif answers is None:
            plan.questions.append(item)
        else:
            _apply_answer(item, answers.get(item.label), plan)
    return plan


def _apply_answer(item: FormField, answer: QuestionAnswer | None, plan: FillPlan) -> None:
    value = match_option(answer.answer, item.options) if answer and answer.can_answer and answer.answer else None
    if value is None:
        plan.blockers.append(f"pergunta sem resposta nos seus dados: {item.label}")
    else:
        plan.actions.append(FillAction(item.key, item.kind, item.label, value, "resposta"))
