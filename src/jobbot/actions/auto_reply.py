"""Resposta automática só para logística simples, com modelo fixo e dados do perfil.

Responde a "ainda tem interesse?" e "qual sua disponibilidade?". Qualquer outro pedido na mensagem
(documento, salário, link, anexo...) faz o jobbot não responder e deixar para o usuário.
"""

from __future__ import annotations

import re

from jobbot.models import strip_accents
from jobbot.profile import Profile
from jobbot.tracking.mailbox import Message

MAX_MESSAGE_CHARS = 400
INTEREST = re.compile(r"(tem|possui|mantem) interesse|confirm\w* (o |seu |sua )?interesse|still interested|confirm your interest")
AVAILABILITY = re.compile(r"disponibilidade|availability")
OTHER_REQUEST = re.compile(
    r"salar|pretens|remunera|\bcpf\b|\brg\b|document|comprovante|senha|password|clique|click|link|formulario|\bform\b"
    r"|anex|attach|envi[ea]|send|banco|bank|pagamento|payment|\bpix\b|taxa|\bfee\b|endereco|address|nascimento|birth"
    r"|foto|photo|telefone|whatsapp|curriculo|resume|\bcv\b|portfolio|https?://|login|cadastr|codigo|code|token"
)
NO_REPLY = re.compile(r"no-?reply|nao-?responda|naoresponda|do-?not-?reply|mailer-daemon|notifica")
STAGES_FOR_REPLY = (None, "screening")


def plan_reply(message: Message, profile: Profile, job_title: str, stage: str | None) -> str | None:
    """O texto da resposta, ou None quando a mensagem não é só logística simples."""
    if stage not in STAGES_FOR_REPLY or not message.sender_address or NO_REPLY.search(message.sender_address):
        return None
    text = re.sub(r"\s+", " ", strip_accents(f"{message.subject} {message.text}")).strip()
    if len(text) > MAX_MESSAGE_CHARS or OTHER_REQUEST.search(text):
        return None
    lines = []
    if INTEREST.search(text):
        lines.append(f"Confirmo meu interesse na vaga {job_title}.")
    if AVAILABILITY.search(text):
        if not profile.availability:
            return None
        lines.append(f"Minha disponibilidade: {profile.availability}")
    if not lines:
        return None
    return "Olá,\n\n" + "\n".join(lines) + f"\n\nAtenciosamente,\n{profile.personal.name}\n"


def reply_subject(subject: str) -> str:
    return subject if subject.lower().startswith("re:") else f"Re: {subject}"
