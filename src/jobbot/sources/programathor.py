"""Programathor: páginas públicas de vagas de tecnologia.

A candidatura exige conta no site, então fica manual: o jobbot prepara o material e o usuário envia.
"""

from __future__ import annotations

from urllib.parse import urljoin

from bs4 import BeautifulSoup

from jobbot.models import Job, detect_modality
from jobbot.sources.base import Source, SourceFormatChanged

BASE_URL = "https://programathor.com.br"
JUNIOR_URL = f"{BASE_URL}/jobs?expertise=J%C3%BAnior"


def _icon_values(card) -> dict[str, str]:
    values = {}
    for span in card.select(".cell-list-content-icon span"):
        icon = span.find("i")
        if icon:
            values[icon["class"][-1]] = span.get_text(strip=True)
    return values


def parse_list(page: str) -> list[Job]:
    soup = BeautifulSoup(page, "html.parser")
    cards = soup.select('.cell-list a[href^="/jobs/"]')
    if not cards:
        raise SourceFormatChanged("programathor: a listagem não tem mais os cartões de vaga")
    jobs = []
    for card in cards:
        title = card.select_one("h3")
        if title is None or "vencida" in title.get_text().lower():
            continue
        values = _icon_values(card)
        location = values.get("fa-map-marker-alt", "")
        tags = ", ".join(t.get_text(strip=True) for t in card.select(".tag-list"))
        summary = f"Nível: {values.get('fa-chart-bar', '')}. Contrato: {values.get('fa-file-alt', '')}. Tecnologias: {tags}."
        jobs.append(
            Job(
                title=title.get_text(strip=True),
                company=values.get("fa-briefcase", ""),
                location=location,
                modality=detect_modality(location),
                description=summary,
                url=urljoin(BASE_URL, card["href"]),
                source="programathor",
                apply_path="manual",
            )
        )
    return jobs


def parse_detail(page: str) -> str:
    block = BeautifulSoup(page, "html.parser").select_one(".line-height-2-4")
    if block is None:
        raise SourceFormatChanged("programathor: a página da vaga não tem mais a descrição")
    return block.get_text("\n", strip=True)


class ProgramathorSource(Source):
    name = "programathor"

    def fetch_jobs(self) -> list[Job]:
        pages = [f"{BASE_URL}/jobs"] + [f"{BASE_URL}/jobs/page/{n}" for n in range(2, self.config.max_pages + 1)]
        jobs: list[Job] = []
        for url in [JUNIOR_URL, *pages]:
            jobs += parse_list(self.http.get(url).text)
        return jobs

    def enrich(self, url: str) -> str | None:
        return parse_detail(self.http.get(url).text)
