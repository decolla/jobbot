import httpx
import pytest

from jobbot.sources.base import BlockedByRobots, PoliteClient, SourceBlocked


class Site:
    """Servidor falso: conta chamadas reais feitas pelo httpx."""

    def __init__(self, robots="User-agent: *\nDisallow: /privado\n", robots_status=200, page_status=200):
        self.robots, self.robots_status, self.page_status = robots, robots_status, page_status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/robots.txt":
            return httpx.Response(self.robots_status, text=self.robots)
        return httpx.Response(self.page_status, text="ok")

    def pages(self):
        return [r for r in self.requests if r.url.path != "/robots.txt"]


def _client(site, **over):
    sleeps: list[float] = []
    client = PoliteClient(min_delay=2, max_delay=6, transport=httpx.MockTransport(site), sleep=sleeps.append, **over)
    return client, sleeps


def test_disallowed_url_raises_without_requesting_the_page():
    site = Site()
    client, _ = _client(site)
    with pytest.raises(BlockedByRobots):
        client.get("https://a.io/privado/1")
    assert site.pages() == []


def test_allowed_url_is_fetched():
    client, _ = _client(Site())
    assert client.get("https://a.io/publico").text == "ok"


def test_robots_404_allows_everything():
    site = Site(robots="", robots_status=404)
    client, _ = _client(site)
    assert client.get("https://a.io/privado/1").status_code == 200


@pytest.mark.parametrize("status", [401, 403])
def test_robots_forbidden_blocks_everything(status):
    site = Site(robots="", robots_status=status)
    client, _ = _client(site)
    with pytest.raises(BlockedByRobots):
        client.get("https://a.io/qualquer")


def test_robots_server_error_is_treated_as_blocked():
    site = Site(robots="", robots_status=500)
    client, _ = _client(site)
    with pytest.raises(SourceBlocked):
        client.get("https://a.io/qualquer")
    assert site.pages() == []


def test_robots_is_fetched_once_per_host():
    site = Site()
    client, _ = _client(site)
    client.get("https://a.io/1")
    client.get("https://a.io/2")
    assert [r.url.path for r in site.requests].count("/robots.txt") == 1


def test_waits_a_random_delay_between_requests_to_same_host():
    client, sleeps = _client(Site())
    client.get("https://a.io/1")
    client.get("https://a.io/2")
    client.get("https://a.io/3")
    assert len(sleeps) == 2
    assert all(2 <= s <= 6 for s in sleeps)


def test_does_not_wait_between_different_hosts():
    client, sleeps = _client(Site())
    client.get("https://a.io/1")
    client.get("https://b.io/1")
    assert sleeps == []


def test_crawl_delay_raises_the_minimum_wait():
    site = Site(robots="User-agent: *\nCrawl-delay: 30\n")
    client, sleeps = _client(site)
    client.get("https://a.io/1")
    client.get("https://a.io/2")
    assert sleeps[0] >= 30


@pytest.mark.parametrize("status", [403, 429, 503])
def test_antibot_status_raises_source_blocked(status):
    client, _ = _client(Site(page_status=status))
    with pytest.raises(SourceBlocked):
        client.get("https://a.io/1")


def test_identifies_itself_in_user_agent():
    site = Site()
    client, _ = _client(site)
    client.get("https://a.io/1")
    assert "jobbot" in site.pages()[0].headers["user-agent"]
