"""Carrega e valida o perfil, as configurações e os documentos do usuário."""

from __future__ import annotations

import os
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pypdf import PdfReader
from pypdf.errors import PyPdfError

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
    extras: list[str] = Field(default_factory=list)


class SavedAnswer(_Strict):
    match: str
    answer: str


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


@dataclass(frozen=True)
class SetupIssue:
    level: Literal["error", "warning"]
    message: str


def project_root() -> Path:
    return Path(os.environ.get("JOBBOT_HOME") or Path(__file__).resolve().parents[2])


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


def read_document_text(path: Path) -> str:
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages).strip()
        if suffix == ".docx":
            with zipfile.ZipFile(path) as z:
                root = ElementTree.fromstring(z.read("word/document.xml"))
            paragraphs = ("".join(t.text or "" for t in p.iter(f"{WORD_NS}t")) for p in root.iter(f"{WORD_NS}p"))
            return "\n".join(p for p in paragraphs if p)
    except (OSError, KeyError, zipfile.BadZipFile, ElementTree.ParseError, PyPdfError) as exc:
        raise ConfigError(f"não foi possível ler {path.name}: {exc}") from exc
    raise ConfigError(f"formato não suportado: {path.name} (use PDF ou DOCX)")


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
        for doc in [profile.documents.resume, *profile.documents.extras]:
            if not (root / doc).exists():
                issues.append(SetupIssue("error", f"documento não encontrado: {doc}"))
        if profile.is_example:
            issues.append(SetupIssue("warning", "profile.yaml ainda tem os dados de exemplo; envios ficam bloqueados"))
    if not shutil.which("claude"):
        issues.append(SetupIssue("warning", "comando claude não encontrado; pontuação e textos não vão funcionar"))
    return issues
