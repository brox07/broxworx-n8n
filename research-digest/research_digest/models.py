from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Article:
    """One fetched item, before any LLM work has touched it."""

    fingerprint: str  # stable identity across re-fetches, e.g. "arxiv:2410.01234"
    source: str  # human-facing source name, e.g. "arXiv" or "Google DeepMind"
    source_kind: str  # "arxiv" | "rss"
    title: str
    url: str
    authors: str | None = None
    published_at: datetime | None = None
    abstract: str | None = None
