"""arXiv daily announcements via rss.arxiv.org.

The RSS feed carries exactly the papers announced in the latest daily mailing, which is
what a morning digest wants, and one request covers every category.
"""
import re

import feedparser
import httpx

from ..config import ArxivConfig
from ..models import Article
from . import struct_to_datetime

FEED_URL = "https://rss.arxiv.org/rss/{categories}"

# guid looks like "oai:arXiv.org:2410.01234v1"; older ids look like "oai:arXiv.org:hep-th/9901001v2".
_ID_RE = re.compile(r"(?:oai:arXiv\.org:)?(?P<id>[\w.\-/]+?)(?:v\d+)?$")
# description looks like "arXiv:2410.01234v1 Announce Type: new \nAbstract: We present ..."
_ABSTRACT_RE = re.compile(r"Abstract:\s*(?P<abstract>.*)", re.DOTALL)


def feed_url(cfg: ArxivConfig) -> str:
    return FEED_URL.format(categories="+".join(cfg.categories))


def parse(content: bytes, cfg: ArxivConfig) -> list[Article]:
    feed = feedparser.parse(content)
    wanted = set(cfg.announce_types)
    articles = []
    for entry in feed.entries:
        if entry.get("arxiv_announce_type", "new") not in wanted:
            continue
        match = _ID_RE.search(entry.get("id") or entry.get("link", "").rsplit("/", 1)[-1])
        if not match:
            continue
        arxiv_id = match.group("id")
        description = entry.get("summary", "")
        abstract_match = _ABSTRACT_RE.search(description)
        abstract = abstract_match.group("abstract") if abstract_match else description
        articles.append(
            Article(
                fingerprint=f"arxiv:{arxiv_id}",
                source="arXiv",
                source_kind="arxiv",
                title=" ".join(entry.get("title", "").split()),
                url=f"https://arxiv.org/abs/{arxiv_id}",
                authors=entry.get("author"),
                published_at=struct_to_datetime(entry.get("published_parsed")),
                abstract=" ".join(abstract.split()) or None,
            )
        )
    return articles


def fetch(client: httpx.Client, cfg: ArxivConfig) -> list[Article]:
    response = client.get(feed_url(cfg))
    response.raise_for_status()
    return parse(response.content, cfg)
