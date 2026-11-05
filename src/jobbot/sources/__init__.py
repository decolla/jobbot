"""Registro das fontes de vagas."""

from __future__ import annotations

from jobbot.profile import ConfigError, SourceSettings
from jobbot.sources.base import PoliteClient, Source
from jobbot.sources.email_alerts import EmailAlertsSource
from jobbot.sources.greenhouse import GreenhouseSource
from jobbot.sources.gupy import GupySource
from jobbot.sources.lever import LeverSource
from jobbot.sources.programathor import ProgramathorSource
from jobbot.sources.vagas import VagasSource

SOURCES: dict[str, type[Source]] = {
    source.name: source
    for source in (GreenhouseSource, LeverSource, GupySource, ProgramathorSource, VagasSource, EmailAlertsSource)
}


def build_sources(settings: dict[str, SourceSettings], http: PoliteClient) -> list[Source]:
    sources = []
    for name, config in settings.items():
        if not config.enabled:
            continue
        if name not in SOURCES:
            raise ConfigError(f"fonte desconhecida em settings.yaml: {name}")
        sources.append(SOURCES[name](config, http))
    return sources
