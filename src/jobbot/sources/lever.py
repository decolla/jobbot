"""Lever: API pública de postings."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from jobbot.models import Job, detect_modality
from jobbot.sources.base import Source, html_to_text

POSTINGS_URL = "https://api.lever.co/v0/postings/{}?mode=json"
WORKPLACE = {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite"}


def _description(item: dict) -> str:
    parts = [item.get("descriptionPlain") or ""]
    for block in item.get("lists") or []:
        parts += [block.get("text") or "", html_to_text(block.get("content") or "")]
    parts.append(item.get("additionalPlain") or "")
    return "\n".join(p.strip() for p in parts if p and p.strip())


def parse_postings(data: list[dict], company: str) -> list[Job]:
    jobs = []
    for item in data:
        location = (item.get("categories") or {}).get("location") or ""
        created = item.get("createdAt")
        jobs.append(
            Job(
                title=item["text"],
                company=company,
                location=location,
                modality=WORKPLACE.get(item.get("workplaceType"), detect_modality(location)),
                description=_description(item),
                url=item["hostedUrl"],
                posted_at=datetime.fromtimestamp(created / 1000, timezone.utc).isoformat() if created else None,
                source="lever",
            )
        )
    return jobs


class LeverSource(Source):
    name = "lever"

    def fetch_jobs(self) -> list[Job]:
        jobs: list[Job] = []
        for company in self.config.companies:
            try:
                jobs += parse_postings(self.http.get(POSTINGS_URL.format(company)).json(), company)
            except httpx.HTTPStatusError as exc:
                self.warnings.append(f"lever: empresa {company} respondeu HTTP {exc.response.status_code}")
        return jobs
