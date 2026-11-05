import json
from pathlib import Path

import httpx
import pytest

from jobbot.profile import SourceSettings
from jobbot.sources import SOURCES, build_sources
from jobbot.sources.base import PoliteClient, SourceFormatChanged
from jobbot.sources.greenhouse import GreenhouseSource, parse_board
from jobbot.sources.gupy import parse_search as parse_gupy
from jobbot.sources.lever import LeverSource, parse_postings
from jobbot.sources.programathor import ProgramathorSource, parse_detail, parse_list
from jobbot.sources.vagas import parse_list as parse_vagas

FIX = Path(__file__).parent / "fixtures" / "sources"


def _text(name):
    return (FIX / name).read_text(encoding="utf-8")


def _json(name):
    return json.loads(_text(name))


def _http(handler):
    def route(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return handler(request)

    return PoliteClient(min_delay=0, max_delay=0, transport=httpx.MockTransport(route), sleep=lambda _: None)


def test_greenhouse_maps_real_board_fields():
    job = parse_board(_json("greenhouse_board.json"), "stone")[0]
    assert job.title == "(VITTA) Analista Júnior de Atendimento (Benefícios) | Híbrido/SP"
    assert job.company == "Stone"
    assert job.location == "Sao Paulo, Sao Paulo, Brasil"
    assert job.modality == "hybrid"
    assert job.url == "https://job-boards.greenhouse.io/stone/jobs/8196023003"
    assert job.posted_at == "2026-11-04T15:34:14-05:00"
    assert job.source == "greenhouse"
    assert job.apply_path == "form"


def test_greenhouse_description_is_plain_text_without_markup():
    description = parse_board(_json("greenhouse_board.json"), "stone")[0].description
    assert "Sobre a Stone" in description
    assert "<" not in description and "&lt;" not in description


def test_greenhouse_falls_back_to_board_name_without_company():
    data = _json("greenhouse_board.json")
    data["jobs"][0]["company_name"] = None
    assert parse_board(data, "stone")[0].company == "stone"


def test_greenhouse_one_broken_board_does_not_lose_the_others():
    def handler(request):
        if "/quebrada/" in request.url.path:
            return httpx.Response(404)
        return httpx.Response(200, json=_json("greenhouse_board.json"))

    source = GreenhouseSource(SourceSettings(enabled=True, boards=["quebrada", "stone"]), _http(handler))
    assert len(source.fetch_jobs()) == 2
    assert any("quebrada" in w for w in source.warnings)


def test_lever_maps_real_posting_fields():
    job = parse_postings(_json("lever_postings.json"), "neon")[0]
    assert job.title == "Analista de CRM Sênior | Vaga Afirmativa para Mulheres Pretas e Pardas"
    assert job.company == "neon"
    assert job.location == "Remoto"
    assert job.modality == "remote"
    assert job.url == "https://jobs.lever.co/neon/5cae0695-01bc-4b65-81a9-11fcba5c53ba"
    assert job.posted_at.startswith("2026-10-")
    assert job.apply_path == "form"


def test_lever_description_includes_requirement_lists():
    description = parse_postings(_json("lever_postings.json"), "neon")[0].description
    assert "Sobre a Neon" in description
    assert "Requisitos" in description
    assert "<li>" not in description


def test_lever_fetches_every_company():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json=_json("lever_postings.json"))

    jobs = LeverSource(SourceSettings(enabled=True, companies=["neon", "ciandt"]), _http(handler)).fetch_jobs()
    assert calls == ["/v0/postings/neon", "/v0/postings/ciandt"]
    assert {j.company for j in jobs} == {"neon", "ciandt"}


def test_gupy_maps_embedded_job_list():
    job = parse_gupy(_text("gupy_search.html"))[1]
    assert job.title == "Estágio em Tecnologia | Processos e Suporte Interno"
    assert job.company == "AlmapBBDO"
    assert job.location == "São Paulo, São Paulo"
    assert job.modality == "onsite"
    assert job.url.startswith("https://almapbbdo.gupy.io/job/")
    assert job.source == "gupy"
    assert job.apply_path == "manual"


def test_gupy_description_is_plain_text():
    assert "&nbsp;" not in parse_gupy(_text("gupy_search.html"))[0].description


def test_gupy_changed_layout_raises_instead_of_returning_empty():
    with pytest.raises(SourceFormatChanged):
        parse_gupy("<html><body>nova página</body></html>")


def test_programathor_maps_list_card():
    job = parse_list(_text("programathor_list.html"))[2]
    assert job.title == "Desenvolvedor(a) Full Stack Júnior — React e TypeScript"
    assert job.company == "Viradev"
    assert job.location == "Remoto"
    assert job.modality == "remote"
    assert job.url == "https://programathor.com.br/jobs/33838-desenvolvedor-a-full-stack-junior-react-e-typescript"
    assert "Júnior" in job.description and "JavaScript" in job.description
    assert job.apply_path == "manual"


def test_programathor_skips_expired_jobs():
    titles = [job.title for job in parse_list(_text("programathor_list.html"))]
    assert len(titles) == 3
    assert not any("Vencida" in title for title in titles)


def test_programathor_changed_layout_raises():
    with pytest.raises(SourceFormatChanged):
        parse_list("<html><body>nova página</body></html>")


def test_programathor_detail_returns_description():
    assert parse_detail(_text("programathor_detail.html")).startswith("Descrição da empresa")


def test_programathor_enrich_fetches_detail_page():
    source = ProgramathorSource(
        SourceSettings(enabled=True, max_pages=1),
        _http(lambda request: httpx.Response(200, text=_text("programathor_detail.html"))),
    )
    assert "Ubuntu Future Labs" in source.enrich("https://programathor.com.br/jobs/1-x")


def test_programathor_requests_junior_filter_and_pages():
    paths = []

    def handler(request):
        paths.append(str(request.url))
        return httpx.Response(200, text=_text("programathor_list.html"))

    ProgramathorSource(SourceSettings(enabled=True, max_pages=2), _http(handler)).fetch_jobs()
    assert paths == [
        "https://programathor.com.br/jobs?expertise=J%C3%BAnior",
        "https://programathor.com.br/jobs",
        "https://programathor.com.br/jobs/page/2",
    ]


def test_vagas_maps_list_item():
    job = parse_vagas(_text("vagas_list.html"))[0]
    assert job.title == "Estagiário(a) de Desenvolvimento de Produtos"
    assert job.company == "Atex do Brasil"
    assert job.location == "Belo Horizonte / MG"
    assert job.url == "https://www.vagas.com.br/vagas/v2835211/estagiario-a-de-desenvolvimento-de-produtos"
    assert job.posted_at == "2026-09-16"
    assert job.apply_path == "manual"


def test_vagas_changed_layout_raises():
    with pytest.raises(SourceFormatChanged):
        parse_vagas("<html><body>nova página</body></html>")


def test_build_sources_only_returns_enabled_ones():
    http = _http(lambda request: httpx.Response(200))
    settings = {
        "greenhouse": SourceSettings(enabled=True, boards=[]),
        "gupy": SourceSettings(enabled=False, queries=[]),
    }
    assert [s.name for s in build_sources(settings, http)] == ["greenhouse"]


def test_every_configured_source_is_registered():
    import yaml

    repo_settings = Path(__file__).resolve().parents[1] / "config" / "settings.yaml"
    names = set(yaml.safe_load(repo_settings.read_text(encoding="utf-8"))["sources"])
    assert names <= set(SOURCES)
