import httpx

from research_digest.config import ArxivConfig, Config, FeedConfig
from research_digest.ingest import run_ingest


def test_ingest_stores_items_and_isolates_failing_feeds(conn, fixture_bytes):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "rss.arxiv.org":
            return httpx.Response(200, content=fixture_bytes("arxiv.xml"))
        if request.url.host == "lab.example.com":
            return httpx.Response(200, content=fixture_bytes("blog.xml"))
        return httpx.Response(503)

    cfg = Config(
        database_url="unused",
        arxiv=ArxivConfig(categories=["cs.AI"]),
        feeds=[
            FeedConfig(name="Broken", url="https://down.example.com/feed"),
            FeedConfig(name="Example Lab", url="https://lab.example.com/feed"),
        ],
        interests="",
        relevance_threshold=7,
        max_age_days=36500,
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))

    assert run_ingest(cfg, conn, client) == {"arXiv": 2, "Broken": -1, "Example Lab": 2}
    assert run_ingest(cfg, conn, client) == {"arXiv": 0, "Broken": -1, "Example Lab": 0}
    assert conn.execute("SELECT count(*) FROM articles WHERE status = 'new'").fetchone()[0] == 4
