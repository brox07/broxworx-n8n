import logging
from datetime import datetime, timedelta, timezone

import httpx
import psycopg

from . import db
from .config import Config
from .sources import arxiv, rss

log = logging.getLogger(__name__)

USER_AGENT = "research-digest/0.1 (+https://github.com/brox07/broxworx-n8n)"


def make_client() -> httpx.Client:
    return httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": USER_AGENT})


def run_ingest(cfg: Config, conn: psycopg.Connection, client: httpx.Client) -> dict[str, int]:
    """Fetch every configured source and store new items.

    Returns new-item counts per source; a source that errors is logged and recorded as -1
    so one broken feed never blocks the rest.
    """
    not_before = datetime.now(timezone.utc) - timedelta(days=cfg.max_age_days)
    jobs = []
    if cfg.arxiv:
        jobs.append(("arXiv", lambda: arxiv.fetch(client, cfg.arxiv)))
    for feed in cfg.feeds:
        jobs.append((feed.name, lambda feed=feed: rss.fetch(client, feed, not_before)))

    results = {}
    for name, fetch in jobs:
        try:
            articles = fetch()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("ingest %s failed: %s", name, exc)
            results[name] = -1
            continue
        results[name] = db.upsert_articles(conn, articles)
        log.info("ingest %s: %d fetched, %d new", name, len(articles), results[name])
    return results
