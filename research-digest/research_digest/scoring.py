"""Relevance scoring with Cloudflare's Clef decision model on Workers AI.

Clef answers typed questions with probabilities instead of generating text. Each paper is
one request with a single yes/no ("noul") question, so the result is directly the
probability that the paper matches the interests profile.
"""
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

import httpx
import psycopg

from .config import Config

log = logging.getLogger(__name__)

WORKERS_AI_URL = "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"
STATE_MAX_BYTES = 2000  # Clef's limit on the text state, measured after JSON serialization
MAX_ATTEMPTS = 3


class ScorerError(Exception):
    pass


class Scorer(Protocol):
    name: str

    def score(self, title: str, abstract: str | None) -> float: ...


def build_state(title: str, abstract: str | None, max_bytes: int = STATE_MAX_BYTES) -> str:
    """Title + abstract, with the abstract trimmed until the JSON-encoded state fits.

    Size is measured with json.dumps' default ASCII escaping, the most conservative reading
    of the limit, since non-ASCII characters count as six bytes each.
    """
    head = f"Title: {title}"

    def fits(state: str) -> bool:
        return len(json.dumps(state).encode()) <= max_bytes

    if not abstract:
        return head
    full = f"{head}\n\nAbstract: {abstract}"
    if fits(full):
        return full
    # Binary search for the longest abstract prefix that still fits with an ellipsis.
    lo, hi = 0, len(abstract)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if fits(f"{head}\n\nAbstract: {abstract[:mid].rstrip()}…"):
            lo = mid
        else:
            hi = mid - 1
    return f"{head}\n\nAbstract: {abstract[:lo].rstrip()}…" if lo else head


def _probability(answer) -> float:
    # A noul answer is documented as a bare probability; accept an object form too in
    # case the REST envelope wraps it.
    if isinstance(answer, dict):
        answer = answer.get("probability", answer.get("p"))
    if isinstance(answer, (int, float)) and 0 <= answer <= 1:
        return float(answer)
    raise ScorerError(f"unexpected noul answer: {answer!r}")


class ClefScorer:
    def __init__(self, client: httpx.Client, account_id: str, api_token: str, model: str, interests: str):
        self.client = client
        self.url = WORKERS_AI_URL.format(account_id=account_id, model=model)
        self.headers = {"Authorization": f"Bearer {api_token}"}
        self.name = model
        self.question = {
            "type": "noul",
            "instructions": (
                "Would a reader with these research interests want to read this paper? "
                f"Interests: {' '.join(interests.split())}"
            ),
        }

    def score(self, title: str, abstract: str | None) -> float:
        response = self.client.post(
            self.url,
            headers=self.headers,
            json={"state": build_state(title, abstract), "questions": {"relevant": self.question}},
        )
        try:
            body = response.json()
        except ValueError:
            response.raise_for_status()
            raise ScorerError(f"non-JSON response ({response.status_code})")
        if not response.is_success or not body.get("success", True):
            raise ScorerError(f"{response.status_code}: {body.get('errors') or body}")
        answers = (body.get("result") or {}).get("answers") or {}
        if "relevant" not in answers:
            raise ScorerError(f"no 'relevant' answer in response: {body}")
        return _probability(answers["relevant"])


def make_scorer(cfg: Config, client: httpx.Client) -> Scorer:
    if not (cfg.cloudflare_account_id and cfg.cloudflare_ai_token):
        raise RuntimeError("CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_AI_API_TOKEN must be set to score")
    return ClefScorer(client, cfg.cloudflare_account_id, cfg.cloudflare_ai_token, cfg.scorer_model, cfg.interests)


def run_scoring(
    cfg: Config, conn: psycopg.Connection, scorer: Scorer, limit: int | None = None, workers: int = 8
) -> dict[str, int]:
    """Move every 'new' article to relevant / skipped (or failed after repeated errors).

    Blog posts are few and hand-picked by their source, so they skip scoring.
    """
    rows = conn.execute(
        """
        SELECT id, source_kind, title, abstract FROM articles
        WHERE status = 'new' ORDER BY id LIMIT %s
        """,
        (limit,),
    ).fetchall()
    counts = {"relevant": 0, "skipped": 0, "error": 0}

    blog_ids = [r[0] for r in rows if r[1] != "arxiv"]
    if blog_ids:
        conn.execute("UPDATE articles SET status = 'relevant' WHERE id = ANY(%s)", (blog_ids,))
        counts["relevant"] += len(blog_ids)

    papers = [r for r in rows if r[1] == "arxiv"]

    def score_one(row):
        try:
            return row[0], scorer.score(row[2], row[3]), None
        except (ScorerError, httpx.HTTPError) as exc:
            return row[0], None, exc

    # HTTP calls run in parallel; database writes stay on this thread.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for article_id, probability, error in pool.map(score_one, papers):
            if error is not None:
                log.warning("scoring article %s failed: %s", article_id, error)
                conn.execute(
                    """
                    UPDATE articles SET attempts = attempts + 1,
                      status = CASE WHEN attempts + 1 >= %s THEN 'failed' ELSE status END
                    WHERE id = %s
                    """,
                    (MAX_ATTEMPTS, article_id),
                )
                counts["error"] += 1
                continue
            status = "relevant" if probability >= cfg.relevance_threshold else "skipped"
            conn.execute(
                "UPDATE articles SET relevance_score = %s, scorer = %s, status = %s WHERE id = %s",
                (probability, scorer.name, status, article_id),
            )
            counts[status] += 1
    conn.commit()
    return counts
