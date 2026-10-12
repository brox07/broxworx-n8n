from datetime import datetime, timezone

from research_digest.config import ArxivConfig, FeedConfig
from research_digest.sources import arxiv, rss


def test_arxiv_parses_new_and_cross_and_skips_replacements(fixture_bytes):
    cfg = ArxivConfig(categories=["cs.AI", "cs.CL"], announce_types=["new", "cross"])
    articles = arxiv.parse(fixture_bytes("arxiv.xml"), cfg)

    assert [a.fingerprint for a in articles] == ["arxiv:2610.01234", "arxiv:2610.05555"]
    first = articles[0]
    assert first.title == "Agents That Plan: Test-Time Search for Tool Use"
    assert first.url == "https://arxiv.org/abs/2610.01234"
    assert first.abstract == "We present a method for planning over tool calls."
    assert first.authors == "Alice Smith, Bob Jones"
    assert first.published_at == datetime(2026, 10, 12, 4, tzinfo=timezone.utc)


def test_arxiv_feed_url_joins_categories():
    assert arxiv.feed_url(ArxivConfig(categories=["cs.AI", "cs.LG"])) == "https://rss.arxiv.org/rss/cs.AI+cs.LG"


def test_rss_strips_html_and_drops_entries_older_than_cutoff(fixture_bytes):
    feed = FeedConfig(name="Example Lab", url="https://lab.example.com/feed")
    cutoff = datetime(2026, 10, 1, tzinfo=timezone.utc)
    articles = rss.parse(fixture_bytes("blog.xml"), feed, not_before=cutoff)

    assert len(articles) == 1
    post = articles[0]
    assert post.source == "Example Lab"
    assert post.source_kind == "rss"
    assert post.abstract == "Today we release a new model."
    assert post.fingerprint.startswith("rss:")


def test_rss_fingerprint_is_stable_across_parses(fixture_bytes):
    feed = FeedConfig(name="Example Lab", url="https://lab.example.com/feed")
    a = rss.parse(fixture_bytes("blog.xml"), feed)
    b = rss.parse(fixture_bytes("blog.xml"), feed)
    assert [x.fingerprint for x in a] == [x.fingerprint for x in b]
