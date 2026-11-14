"""Carrega e valida o perfil, as configurações e os documentos do usuário."""

from __future__ import annotations

import os
import re
import shutil
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from jobbot.models import detect_language

WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
EXAMPLE_EMAIL_DOMAIN = "@exemplo.com"


class ConfigError(Exception):
    """Configuração ou documento ausente ou inválido."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Personal(_Strict):
    name: str
    email: str
    phone: str = ""
    city: str
    state: str = ""
    linkedin: str = ""
    github: str = ""
    portfolio: str = ""


class Education(_Strict):
    course: str
    institution: str
    status: str = ""
    expected_graduation: str = ""


class Locations(_Strict):
    cities: list[str] = Field(default_factory=list)
    remote: bool = True
    hybrid: bool = True
    onsite: bool = False


class SalaryRange(_Strict):
    min: int | None = None
    max: int | None = None


class Keywords(_Strict):
    positive: list[str] = Field(default_factory=list)
    negative: list[str] = Field(default_factory=list)


class Documents(_Strict):
    resume: str
    resume_en: str = ""  # usado nas vagas em inglês, se existir
    extras: list[str] = Field(default_factory=list)

    def resume_for(self, language: str) -> str:
        return self.resume_en if language == "en" and self.resume_en else self.resume


class SavedAnswer(_Strict):
    match: str
    answer: str

    @field_validator("match")
    @classmethod
    def _not_empty(cls, value: str) -> str:
        # Um trecho vazio casaria com qualquer pergunta do formulário.
        if not value.strip():
            raise ValueError("match não pode ser vazio")
        return value


class Profile(_Strict):
    personal: Personal
    objective: str
    education: list[Education] = Field(default_factory=list)
    seniority: list[str]
    stack: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    locations: Locations = Field(default_factory=Locations)
    salary_range: SalaryRange = Field(default_factory=SalaryRange)
    keywords: Keywords = Field(default_factory=Keywords)
    max_years_required: int = 2
    dealbreakers: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    availability: str = ""
    saved_answers: list[SavedAnswer] = Field(default_factory=list)
    documents: Documents

    @property
    def is_example(self) -> bool:
        return self.personal.email.endswith(EXAMPLE_EMAIL_DOMAIN)


class BrowserSettings(_Strict):
    headless: bool = True


class HttpSettings(_Strict):
    min_delay_seconds: float = 2
    max_delay_seconds: float = 6


class LLMSettings(_Strict):
    scoring_model: str = "haiku"
    writing_model: str = "sonnet"
    timeout_seconds: int = 180


class SourceSettings(BaseModel):
    """Opções comuns a toda fonte. O restante é específico de cada uma."""

    model_config = ConfigDict(extra="allow")
    enabled: bool = False


class TrackingSettings(_Strict):
    email: bool = False
    lookback_days: int = 14


class NotificationSettings(_Strict):
    telegram: bool = False
    email: bool = False


class AutoReplySettings(_Strict):
    enabled: bool = False


class FollowupSettings(_Strict):
    enabled: bool = True
    after_days: int = Field(default=7, ge=1)
    max_attempts: int = Field(default=2, ge=0)


class Settings(_Strict):
    mode: Literal["dry-run", "approval", "auto"] = "dry-run"
    min_score: int = Field(default=70, ge=0, le=100)
    daily_application_limit: int = Field(default=15, ge=0)
    max_scores_per_run: int = Field(default=30, ge=0)
    browser: BrowserSettings = Field(default_factory=BrowserSettings)
    http: HttpSettings = Field(default_factory=HttpSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    sources: dict[str, SourceSettings] = Field(default_factory=dict)
    tracking: TrackingSettings = Field(default_factory=TrackingSettings)
    notifications: NotificationSettings = Field(default_factory=NotificationSettings)
    auto_reply: AutoReplySettings = Field(default_factory=AutoReplySettings)
    followups: FollowupSettings = Field(default_factory=FollowupSettings)


@dataclass(frozen=True)
class SetupIssue:
    level: Literal["error", "warning"]
    message: str


def project_root() -> Path:
    return Path(os.environ.get("JOBBOT_HOME") or Path(__file__).resolve().parents[2])


def load_env(root: Path | None = None) -> dict[str, str]:
    """Lê os segredos do .env. Variáveis de ambiente já definidas têm prioridade."""
    values: dict[str, str] = {}
    path = (root or project_root()) / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and not key.startswith("#"):
                values[key.strip()] = value.strip().strip("\"'")
    return {**values, **os.environ}


def _load_yaml_model(path: Path, model: type[BaseModel]):
    if not path.exists():
        raise ConfigError(f"{path.name} não encontrado em {path.parent}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path.name} não é um YAML válido: {exc}") from exc
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        raise ConfigError(f"{path.name}: {problems}") from exc


def load_profile(root: Path | None = None) -> Profile:
    return _load_yaml_model((root or project_root()) / "config" / "profile.yaml", Profile)


def load_settings(root: Path | None = None) -> Settings:
    return _load_yaml_model((root or project_root()) / "config" / "settings.yaml", Settings)


# Acentos que o pypdf extrai soltos de PDFs gerados por LaTeX, com o caractere combinante equivalente.
LOOSE_ACCENTS = {"´": "\u0301", "`": "\u0300", "˜": "\u0303", "ˆ": "\u0302", "¨": "\u0308"}
_ACCENT = "[" + "".join(LOOSE_ACCENTS) + "]"


def clean_pdf_text(text: str) -> str:
    """Junta acentos soltos à letra ("constru¸ c˜ ao" vira "construção") e letras separadas pelo kerning."""
    # "distribu ´ ıdos": o i sem pingo vem cercado de espaços que não existem na palavra.
    text = re.sub(rf"\s?({_ACCENT})\s?ı", lambda m: "i" + LOOSE_ACCENTS[m[1]], text)
    # A cedilha vem antes do c minúsculo ("¸ c") e depois do C maiúsculo ("C ¸").
    text = re.sub(r"¸\s?([cC])", "\\1\u0327", text)
    text = re.sub(r"([cC])\s?¸", "\\1\u0327", text)
    text = re.sub(rf"({_ACCENT})\s?([AEIOUaeiouNn])", lambda m: m[2] + LOOSE_ACCENTS[m[1]], text)
    # "F ull-stack", "T emporais", "V ARISE": o kerning dessas maiúsculas vira espaço na extração.
    text = re.sub(r"(?<![\w.])([FTVWY]) (?=[a-z]|[A-Z]{2})", r"\1", text)
    return unicodedata.normalize("NFC", text)


def read_document_text(path: Path) -> str:
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            return clean_pdf_text("\n".join(page.extract_text() or "" for page in PdfReader(path).pages).strip())
        if suffix == ".docx":
            with zipfile.ZipFile(path) as z:
                root = ElementTree.fromstring(z.read("word/document.xml"))
            paragraphs = ("".join(t.text or "" for t in p.iter(f"{WORD_NS}t")) for p in root.iter(f"{WORD_NS}p"))
            return "\n".join(p for p in paragraphs if p)
    except (OSError, KeyError, zipfile.BadZipFile, ElementTree.ParseError, PyPdfError) as exc:
        raise ConfigError(f"não foi possível ler {path.name}: {exc}") from exc
    raise ConfigError(f"formato não suportado: {path.name} (use PDF ou DOCX)")


def resume_text_for(root: Path, profile: Profile, job, cache: dict[str, str] | None = None) -> str:
    """Texto do currículo no idioma da vaga. O cache evita reler o PDF a cada vaga."""
    path = profile.documents.resume_for(detect_language(f"{job.title}\n{job.description}"))
    if cache is None:
        return read_document_text(root / path)
    if path not in cache:
        cache[path] = read_document_text(root / path)
    return cache[path]


def check_setup(root: Path | None = None) -> list[SetupIssue]:
    root = root or project_root()
    issues: list[SetupIssue] = []
    try:
        load_settings(root)
    except ConfigError as exc:
        issues.append(SetupIssue("error", str(exc)))
    try:
        profile = load_profile(root)
    except ConfigError as exc:
        issues.append(SetupIssue("error", f"{exc} (use config/profile.example.yaml como base)"))
        profile = None
    if profile:
        documents = profile.documents
        for doc in [documents.resume, *([documents.resume_en] if documents.resume_en else []), *documents.extras]:
            if not (root / doc).exists():
                issues.append(SetupIssue("error", f"documento não encontrado: {doc}"))
        if profile.is_example:
            issues.append(SetupIssue("warning", "profile.yaml ainda tem os dados de exemplo; envios ficam bloqueados"))
    if not shutil.which("claude"):
        issues.append(SetupIssue("warning", "comando claude não encontrado; pontuação e textos não vão funcionar"))
    return issues
