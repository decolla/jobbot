"""Vagas do LinkedIn e do Indeed sem automar os sites: lê os alertas que chegam no e-mail do usuário.

O formato dos e-mails foi escrito a partir de exemplos sintéticos. Se um alerta não render vagas,
a fonte avisa, para o parser ser ajustado com um e-mail real.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup

from jobbot.models import Job, detect_modality
from jobbot.profile import ConfigError, load_env
from jobbot.sources.base import Source, SourceError
from jobbot.tracking.mailbox import Mailbox, MailboxError, Message

LINKEDIN_JOB = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(\d+)")
INDEED_JOB = re.compile(r"(indeed\.[a-z.]+)/(?:rc/clk|viewjob|pagead/clk)")
SEPARATOR = re.compile(r"\s+[·•|]\s+")


def _job_link(href: str) -> tuple[str, str] | None:
    """(fonte, URL canônica) do link de vaga, sem os parâmetros de rastreio."""
    if match := LINKEDIN_JOB.search(href):
        return "linkedin", f"https://www.linkedin.com/jobs/view/{match[1]}"
    if match := INDEED_JOB.search(href):
        job_key = parse_qs(urlsplit(href).query).get("jk", [""])[0]
        if job_key:
            host = urlsplit(href).netloc or match[1]
            return "indeed", f"https://{host}/viewjob?jk={job_key}"
    return None


def _texts_after(anchor, limit: int = 2) -> list[str]:
    texts = []
    for text in anchor.find_all_next(string=True):
        clean = text.strip()
        if clean and clean != anchor.get_text(strip=True):
            texts.append(clean)
        if len(texts) == limit:
            break
    return texts


def parse_alert(message: Message) -> list[Job]:
    soup = BeautifulSoup(message.html or "", "html.parser")
    jobs: dict[str, Job] = {}
    for anchor in soup.find_all("a", href=True):
        link = _job_link(anchor["href"])
        title = anchor.get_text(" ", strip=True)
        if link is None or not title or link[1] in jobs:
            continue
        source, url = link
        after = _texts_after(anchor)
        parts = SEPARATOR.split(after[0], maxsplit=1) if after else []
        if len(parts) == 2:
            company, location = parts
        else:
            company, location = (after + ["", ""])[:2]
        jobs[url] = Job(title=title, company=company, location=location, modality=detect_modality(location),
                        url=url, source=source, apply_path="manual")
    return list(jobs.values())


class EmailAlertsSource(Source):
    name = "email_alerts"

    def __init__(self, config, http, mailbox=None):
        super().__init__(config, http)
        self.mailbox = mailbox

    def fetch_jobs(self) -> list[Job]:
        try:
            mailbox = self.mailbox or Mailbox.from_env(load_env())
            messages = mailbox.fetch_recent(self.config.lookback_days, senders=self.config.senders)
        except (ConfigError, MailboxError) as exc:
            raise SourceError(str(exc)) from exc
        jobs = []
        for message in messages:
            found = parse_alert(message)
            if not found:
                self.warnings.append(f'alerta "{message.subject}" de {message.sender_address} sem vagas reconhecidas')
            jobs += found
        return jobs
