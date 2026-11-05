"""Greenhouse: API pública de job boards."""

from __future__ import annotations

import re

import httpx

from jobbot.models import Job, detect_modality
from jobbot.sources.base import Source, html_to_text

BOARD_URL = "https://boards-api.greenhouse.io/v1/boards/{}/jobs?content=true"


def parse_board(data: dict, board: str) -> list[Job]:
    jobs = []
    for item in data["jobs"]:
        location = (item.get("location") or {}).get("name") or ""
        # O nome pode vir com o canal de divulgação, como "Stone - Linkedin".
        company = re.split(r"\s+-\s+", item.get("company_name") or board)[0]
        jobs.append(
            Job(
                title=item["title"],
                company=company,
                location=location,
                modality=detect_modality(f"{item['title']} {location}"),
                description=html_to_text(item.get("content") or ""),
                url=item["absolute_url"],
                posted_at=item.get("first_published") or item.get("updated_at"),
                source="greenhouse",
            )
        )
    return jobs


class GreenhouseSource(Source):
    name = "greenhouse"

    def fetch_jobs(self) -> list[Job]:
        jobs: list[Job] = []
        for board in self.config.boards:
            try:
                jobs += parse_board(self.http.get(BOARD_URL.format(board)).json(), board)
            except httpx.HTTPStatusError as exc:
                self.warnings.append(f"greenhouse: board {board} respondeu HTTP {exc.response.status_code}")
        return jobs
