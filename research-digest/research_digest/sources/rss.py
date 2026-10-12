"""Generic RSS/Atom source, used for lab and company research blogs."""
import hashlib
import re
from datetime import datetime

import feedparser
import httpx

from ..config import FeedConfig
from ..models import Article
from . import struct_to_datetime

_TAG_RE = re.compile(r"<[^>]+>")


def _plain_text(html: str) -> str:
    return " ".join(_TAG_RE.sub(" ", html).split())


def parse(content: bytes, feed_cfg: FeedConfig, not_before: datetime | None = None) -> list[Article]:
    feed = feedparser.parse(content)
    articles = []
    for entry in feed.entries:
        link = entry.get("link")
        if not link:
            continue
        published = struct_to_datetime(entry.get("published_parsed") or entry.get("updated_parsed"))
        if not_before and published and published < not_before:
            continue
        # guid is the feed's own stable id; fall back to the link when a feed omits it.
        identity = entry.get("id") or link
        digest = hashlib.sha256(identity.encode()).hexdigest()[:24]
        summary = _plain_text(entry.get("summary", ""))
        articles.append(
            Article(
                fingerprint=f"rss:{digest}",
                source=feed_cfg.name,
                source_kind="rss",
                title=" ".join(entry.get("title", link).split()),
                url=link,
                authors=entry.get("author"),
                published_at=published,
                abstract=summary or None,
            )
        )
    return articles


def fetch(client: httpx.Client, feed_cfg: FeedConfig, not_before: datetime | None = None) -> list[Article]:
    response = client.get(feed_cfg.url)
    response.raise_for_status()
    return parse(response.content, feed_cfg, not_before)
