"""Filtro rápido por regras, antes de gastar uma chamada ao Claude."""

from __future__ import annotations

import re
from dataclasses import dataclass

from jobbot.models import Job, strip_accents
from jobbot.profile import Profile

LEVELS = {
    "estagio": re.compile(r"estagi|\bintern\b|\binternship\b|\baprendiz\b"),
    "junior": re.compile(r"\bjunior\b|\bjr\b|\btrainee\b|entry[- ]level"),
}
SENIOR_TITLE = re.compile(
    r"\b(senior|sr|pleno|staff|principal|lead|lider|head|gerente|manager|coordenador|coordenadora|"
    r"diretor|diretora|especialista|specialist|arquiteto|arquiteta|architect|ii|iii|iv)\b|\bpl\b(?!/)"
)
# Elegibilidade para vaga afirmativa é decisão pessoal: o jobbot não declara nada em nome do usuário.
AFFIRMATIVE = re.compile(
    r"afirmativ|exclusiv\w*\b.*\b(pcd|pessoas|mulher|negr|trans|lgbt|deficien)|\bpcd\b|comunidade negra|pessoas com deficiencia"
)
FOREIGN = re.compile(
    r"united states|estados unidos|\busa\b|\bus\b|canada|mexico|argentina|chile|colombia|\bperu\b|uruguay|uruguai|"
    r"spain|espanha|portugal|germany|alemanha|france|franca|italy|italia|ireland|irlanda|united kingdom|\buk\b|"
    r"london|india|\bemea\b|europe|europa|japan|asia"
)
COUNTRY_ONLY = re.compile(r"brasil|brazil|remote|remoto|home office|[^a-z]")
YEARS = [
    re.compile(r"(\d{1,2})\s*\+?\s*(?:anos?|years?|yrs?)\s+(?:de\s+|of\s+)?(?:experiencia|experience|atuacao|vivencia)"),
    re.compile(r"(?:experiencia|experience|vivencia)\s+(?:minima\s+)?(?:de|of)\s+(\d{1,2})\s*\+?\s*(?:anos?|years?)"),
    re.compile(r"minimo\s+de\s+(\d{1,2})\s*anos?"),
    re.compile(r"at least\s+(\d{1,2})\s*years?"),
    re.compile(r"(\d{1,2})\s*\+\s*(?:anos?|years?)"),
]
# Textos institucionais citam o tempo de casa da empresa, como "com 30 anos de experiência".
MAX_PLAUSIBLE_YEARS = 15
COMPANY_CONTEXT = re.compile(r"\b(temos|somos|empresa|companhia|nossa|nosso|ja sao|acumulamos)\b[^.]{0,40}$")
STACK_TERMS_IN_DESCRIPTION = 2


@dataclass(frozen=True)
class RuleResult:
    passed: bool
    reason: str = ""


def _has_term(term: str, plain_text: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(strip_accents(term))}(?![a-z0-9])", plain_text) is not None


def years_required(text: str) -> int:
    plain = strip_accents(text)
    required = 0
    for pattern in YEARS:
        for match in pattern.finditer(plain):
            years = int(match.group(1))
            about_company = COMPANY_CONTEXT.search(plain[max(0, match.start() - 60):match.start()])
            if years <= MAX_PLAUSIBLE_YEARS and not about_company:
                required = max(required, years)
    return required


def _seniority_problem(plain_title: str, profile: Profile) -> str | None:
    found = [level for level, pattern in LEVELS.items() if pattern.search(plain_title)]
    if found:
        if not set(found) & set(profile.seniority):
            return f"senioridade fora do perfil: {', '.join(found)}"
        return None
    if SENIOR_TITLE.search(plain_title):
        return "senioridade acima do perfil no título"
    return None


def _location_problem(job: Job, profile: Profile) -> str | None:
    prefs = profile.locations
    allowed = {"remote": prefs.remote, "hybrid": prefs.hybrid, "onsite": prefs.onsite, "unknown": True}
    if not allowed[job.modality]:
        return f"modalidade fora do perfil: {job.modality}"
    plain = strip_accents(job.location)
    if not plain:
        return None
    if FOREIGN.search(plain) and not re.search(r"brasil|brazil", plain):
        return f"local fora do Brasil: {job.location}"
    if job.modality == "remote" or not COUNTRY_ONLY.sub("", plain):
        return None
    if any(strip_accents(city) in plain for city in prefs.cities):
        return None
    return f"fora das suas cidades: {job.location}"


def _keyword_match(job: Job, plain_title: str, profile: Profile) -> bool:
    if not profile.keywords.positive and not profile.stack:
        return True
    if any(_has_term(term, plain_title) for term in profile.keywords.positive):
        return True
    plain_description = strip_accents(job.description)
    return sum(_has_term(term, plain_description) for term in profile.stack) >= STACK_TERMS_IN_DESCRIPTION


def evaluate(job: Job, profile: Profile) -> RuleResult:
    plain_title = strip_accents(job.title)
    if AFFIRMATIVE.search(plain_title):
        return RuleResult(False, "vaga afirmativa: avalie sua elegibilidade e use jobbot add se for o seu caso")
    for term in profile.keywords.negative:
        if _has_term(term, plain_title):
            return RuleResult(False, f"palavra negativa no título: {term}")
    problem = _seniority_problem(plain_title, profile) or _location_problem(job, profile)
    if problem:
        return RuleResult(False, problem)
    years = years_required(job.description)
    if years > profile.max_years_required:
        return RuleResult(False, f"exige {years} anos de experiência")
    if not _keyword_match(job, plain_title, profile):
        return RuleResult(False, "nenhuma palavra-chave do perfil")
    return RuleResult(True)
