"""Gupy: lista de vagas embutida na página de busca do portal.

Desligada por padrão: os termos de uso proíbem "agregar, copiar ou duplicar partes" da plataforma.
A candidatura é sempre manual, porque exige conta e pode envolver testes da própria plataforma.
"""

from __future__ import annotations

import json
from urllib.parse import quote

from bs4 import BeautifulSoup

from jobbot.models import Job, detect_modality, normalize_url
from jobbot.sources.base import Source, SourceFormatChanged, html_to_text

SEARCH_URL = "https://portal.gupy.io/job-search/term={}"
WORKPLACE = {"remote": "remote", "hybrid": "hybrid", "on-site": "onsite"}


def parse_search(page: str) -> list[Job]:
    script = BeautifulSoup(page, "html.parser").find("script", id="__NEXT_DATA__")
    try:
        items = json.loads(script.string)["props"]["pageProps"]["initialJobList"]["data"]
    except (AttributeError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise SourceFormatChanged("gupy: a página de busca não tem mais a lista de vagas") from exc
    jobs = []
    for item in items:
        location = ", ".join(p for p in (item.get("city"), item.get("state")) if p)
        jobs.append(
            Job(
                title=item["name"],
                company=item.get("careerPageName") or "",
                location=location,
                modality=WORKPLACE.get(item.get("workplaceType"), detect_modality(location)),
                description=html_to_text(item.get("description") or ""),
                url=normalize_url(item["jobUrl"]),
                posted_at=item.get("publishedDate"),
                source="gupy",
                apply_path="manual",
            )
        )
    return jobs


class GupySource(Source):
    name = "gupy"

    def fetch_jobs(self) -> list[Job]:
        jobs: list[Job] = []
        for query in self.config.queries:
            jobs += parse_search(self.http.get(SEARCH_URL.format(quote(query))).text)
        return jobs
