"""Vagas.com: páginas públicas de busca.

Desligada por padrão: os termos de uso proíbem reproduzir o conteúdo do site.
A candidatura exige conta, então fica manual.
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from jobbot.models import Job, detect_modality
from jobbot.sources.base import Source, SourceFormatChanged

BASE_URL = "https://www.vagas.com.br"
SEARCH_URL = BASE_URL + "/vagas-de-{}"


def _text(item, selector: str) -> str:
    element = item.select_one(selector)
    return element.get_text(" ", strip=True) if element else ""


def _iso_date(value: str) -> str | None:
    try:
        return datetime.strptime(value, "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None


def parse_list(page: str) -> list[Job]:
    soup = BeautifulSoup(page, "html.parser")
    if soup.select_one("#todasVagas") is None:
        raise SourceFormatChanged("vagas: a página de busca não tem mais a lista de vagas")
    jobs = []
    for item in soup.select("li.vaga"):
        link = item.select_one("a.link-detalhes-vaga")
        local = item.select_one(".vaga-local")
        location = next(local.stripped_strings, "") if local else ""
        level = _text(item, ".nivelVaga")
        jobs.append(
            Job(
                title=link["title"],
                company=_text(item, ".emprVaga"),
                location=location,
                modality=detect_modality(location),
                description=f"Nível: {level}. {_text(item, '.detalhes')}",
                url=urljoin(BASE_URL, link["href"]),
                posted_at=_iso_date(_text(item, ".data-publicacao")),
                source="vagas",
                apply_path="manual",
            )
        )
    return jobs


class VagasSource(Source):
    name = "vagas"

    def fetch_jobs(self) -> list[Job]:
        jobs: list[Job] = []
        for query in self.config.queries:
            jobs += parse_list(self.http.get(SEARCH_URL.format(query)).text)
        return jobs
