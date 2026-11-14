"""Modelo único de vaga, usado por todas as fontes."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, computed_field, field_validator

Modality = Literal["remote", "hybrid", "onsite", "unknown"]
Language = Literal["pt", "en"]

PT_WORDS = {"de", "da", "do", "das", "dos", "que", "para", "com", "uma", "um", "em", "nao", "voce", "nossa",
            "nosso", "vaga", "experiencia", "conhecimento", "atuar", "pessoa", "sera", "como", "sao"}
EN_WORDS = {"the", "and", "to", "of", "with", "for", "you", "we", "our", "are", "is", "will", "experience",
            "team", "work", "your", "skills", "in", "an", "be"}

TRACKING_PARAMS = {
    "trk", "trkinfo", "refid", "trackingid", "lipi", "midtoken", "midsig", "eid",
    "otptoken", "ssid", "from", "tk", "gh_src", "source", "src", "ref", "jobboardsource",
}


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def match_key(text: str) -> str:
    """Forma de comparar trechos literais: sem acentos, maiúsculas, espaços nem hífens.

    A extração de PDF gruda palavras ("comApache") e separa outras ("F ull-stack", "inte-gration").
    """
    return re.sub(r"[\s\-\u00ad\u2010]+", "", strip_accents(text))


def detect_modality(text: str) -> Modality:
    plain = strip_accents(text)
    if re.search(r"hibrid|hybrid", plain):
        return "hybrid"
    if re.search(r"remot[oae]|home[ -]?office|teletrabalho|anywhere", plain):
        return "remote"
    if re.search(r"presencial|on[- ]?site", plain):
        return "onsite"
    return "unknown"


def detect_language(text: str) -> Language:
    """Idioma da vaga, para escolher o currículo. Na dúvida, português."""
    words = re.findall(r"[a-z]+", strip_accents(text))
    pt = sum(word in PT_WORDS for word in words)
    en = sum(word in EN_WORDS for word in words)
    return "en" if en >= 3 and en > 1.5 * pt else "pt"


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING_PARAMS and not key.lower().startswith("utm_")
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


class Job(BaseModel):
    title: str
    company: str
    location: str = ""
    modality: Modality = "unknown"
    description: str = ""
    url: str
    posted_at: str | None = None
    source: str
    # "manual": o jobbot prepara o material, mas quem envia é o usuário (LinkedIn, Indeed, Gupy...).
    apply_path: Literal["form", "manual"] = "form"

    @field_validator("title", "company", "location")
    @classmethod
    def _collapse_whitespace(cls, value: str) -> str:
        return " ".join(value.split())

    @classmethod
    def from_row(cls, row) -> "Job":
        fields = ("title", "company", "location", "modality", "description", "url", "posted_at", "source", "apply_path")
        return cls(**{name: row[name] for name in fields})

    @computed_field
    @property
    def hash(self) -> str:
        identity = "|".join(re.sub(r"[^a-z0-9]", "", strip_accents(v)) for v in (self.company, self.title, self.location))
        return hashlib.sha256(identity.encode()).hexdigest()
