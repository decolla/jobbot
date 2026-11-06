"""Etapas do processo seletivo e classificação de e-mails por regras."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from jobbot.models import strip_accents

# Etapas em que só o usuário pode agir: viram item de ação.
HUMAN_STAGES = ("test", "video", "interview", "offer")

# Ordem importa: recusa vence tudo ("não passou no teste"), vídeo vem antes de entrevista ("video interview").
# Os padrões rodam sobre texto sem acentos e em minúsculas.
PATTERNS = [
    ("rejected", r"nao (vamos |iremos |poderemos )?(seguir|prosseguir|avancar|dar continuidade)"
                 r"|nao (seguiremos|prosseguiremos|avancaremos|avancou|foi selecionad|foi aprovad|passou)"
                 r"|optamos por (seguir com )?outr|seguir com outros? (candidat|perfi)"
                 r"|not (be )?(moving|move) forward|not (be )?(proceeding|progressing)"
                 r"|(decided|chosen) to (move forward|proceed|pursue|go) with other"
                 r"|not (been )?selected|were not selected"),
    ("offer", r"proposta (de )?(estagio|emprego|trabalho|contratacao|formal)|temos uma proposta|carta[ -]oferta"
              r"|job offer|offer letter|(pleased|happy|excited) to offer|extend (you )?an offer"),
    ("video", r"hirevue|video ?interview|grav(e|ar|acao de) (um |o |seu )?video|video (de apresentacao|pitch)"
              r"|record (a |your )?video|one-way video"),
    ("test", r"teste (online|on-line|tecnico|de logica|logico|pratico|de ingles|comportamental|de perfil|de raciocinio)"
             r"|avaliacao (tecnica|online|on-line|de logica|logica|pratica|de ingles)|desafio (tecnico|de codigo|pratico)"
             r"|hackerrank|codility|coderbyte|hackerearth|codesignal|testgorilla"
             r"|coding (challenge|test|assessment)|technical (test|assessment|challenge)|online assessment|take[- ]home"),
    ("interview", r"entrevista|interview|bate[- ]papo|agendar (uma )?(conversa|call|reuniao)|schedule a (call|chat)"),
    ("screening", r"recebemos (a )?(sua )?(candidatura|inscricao|aplicacao)"
                  r"|candidatura (foi )?(recebida|registrada|confirmada|realizada com sucesso|enviada com sucesso)"
                  r"|em analise|triagem|obrigad[oa] por (se candidatar|sua candidatura)"
                  r"|thank(s| you) for (applying|your application)|received your application"
                  r"|application (has been )?received|under review"),
]

# Frases sobre etapas que talvez aconteçam ("o processo pode incluir teste") não são convite.
HYPOTHETICAL = re.compile(
    r"pode(m|ra|rao)? (incluir|envolver|contemplar|ter|haver)|caso (voce )?(nao )?(seja|avance|passe)"
    r"|\bse (voce )?(nao )?(for|seja|passar|avancar) |(may|might|could) (include|involve)"
    r"|if (you are |you're |you were )?(not )?(selected|shortlisted|successful)|eventualmente"
)


@dataclass(frozen=True)
class StageGuess:
    stage: str

    @property
    def needs_human(self) -> bool:
        return self.stage in HUMAN_STAGES


class StageAnswer(BaseModel):
    """Resposta do Claude quando as regras não reconhecem o e-mail."""

    stage: Literal["screening", "test", "video", "interview", "offer", "rejected", "none"]


def _without_hypotheticals(text: str) -> str:
    sentences = re.split(r"[.!?\n]+", text)
    return "\n".join(s for s in sentences if not HYPOTHETICAL.search(s))


def classify(subject: str, body: str) -> StageGuess | None:
    text = _without_hypotheticals(strip_accents(f"{subject}\n{body}"))
    for stage, pattern in PATTERNS:
        if re.search(pattern, text):
            return StageGuess(stage)
    return None
