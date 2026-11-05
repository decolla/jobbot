"""Interface comum das fontes e cliente HTTP educado (robots.txt e limite de taxa)."""

from __future__ import annotations

import html
import random
import time
from abc import ABC, abstractmethod
from typing import Callable
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from jobbot.models import Job
from jobbot.profile import SourceSettings

USER_AGENT = "jobbot/0.1 (busca pessoal de vagas; uso individual)"
BLOCKING_STATUS = {403, 429, 503}


class SourceError(Exception):
    """Falha de uma fonte que não deve interromper as demais."""


class BlockedByRobots(SourceError):
    """O robots.txt do site não permite acessar a URL."""


class SourceBlocked(SourceError):
    """O site bloqueou o acesso ou pediu para esperar. Não insistimos: o usuário é avisado."""


class SourceFormatChanged(SourceError):
    """A página mudou e o parser não reconhece mais a estrutura."""


class PoliteClient:
    def __init__(self, min_delay: float, max_delay: float, transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self._min_delay, self._max_delay = min_delay, max_delay
        self._sleep = sleep
        self._client = httpx.Client(
            headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=30, transport=transport
        )
        self._robots: dict[str, RobotFileParser] = {}
        self._visited_hosts: set[str] = set()

    def _robots_for(self, url: str) -> RobotFileParser:
        parts = urlsplit(url)
        if parts.netloc in self._robots:
            return self._robots[parts.netloc]
        robots_url = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
        try:
            response = self._client.get(robots_url)
        except httpx.HTTPError as exc:
            raise SourceBlocked(f"{parts.netloc}: robots.txt inacessível ({exc})") from exc
        if response.status_code >= 500:
            raise SourceBlocked(f"{parts.netloc}: robots.txt respondeu HTTP {response.status_code}")
        parser = RobotFileParser()
        if response.status_code in (401, 403):
            parser.disallow_all = True
        elif response.status_code >= 400:
            parser.allow_all = True
        else:
            parser.parse(response.text.splitlines())
        self._robots[parts.netloc] = parser
        return parser

    def _wait(self, host: str, robots: RobotFileParser) -> None:
        if host not in self._visited_hosts:
            return
        crawl_delay = float(robots.crawl_delay(USER_AGENT) or 0)
        self._sleep(random.uniform(max(self._min_delay, crawl_delay), max(self._max_delay, crawl_delay)))

    def get(self, url: str) -> httpx.Response:
        host = urlsplit(url).netloc
        robots = self._robots_for(url)
        if not robots.can_fetch(USER_AGENT, url):
            raise BlockedByRobots(f"robots.txt não permite acessar {url}")
        self._wait(host, robots)
        self._visited_hosts.add(host)
        response = self._client.get(url)
        if response.status_code in BLOCKING_STATUS:
            raise SourceBlocked(f"{host} respondeu HTTP {response.status_code}")
        response.raise_for_status()
        return response


def html_to_text(markup: str) -> str:
    text = BeautifulSoup(html.unescape(markup or ""), "html.parser").get_text("\n", strip=True)
    return text.replace("\xa0", " ")


class Source(ABC):
    name = ""

    def __init__(self, config: SourceSettings, http: PoliteClient):
        self.config = config
        self.http = http
        self.warnings: list[str] = []

    @abstractmethod
    def fetch_jobs(self) -> list[Job]:
        """Busca as vagas da fonte, normalizadas para o modelo único."""

    def enrich(self, url: str) -> str | None:
        """Busca a descrição completa, quando a listagem só traz um resumo."""
        return None
