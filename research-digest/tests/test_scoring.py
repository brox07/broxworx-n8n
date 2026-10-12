import json

import httpx
import pytest

from research_digest import db
from research_digest.config import Config
from research_digest.models import Article
from research_digest.scoring import STATE_MAX_BYTES, ClefScorer, ScorerError, build_state, run_scoring


def _clef(handler, interests="LLM agents"):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return ClefScorer(client, "acct123", "tok", "@cf/cloudflare/clef-flash", interests)


def _ok(probability):
    return httpx.Response(200, json={"success": True, "errors": [], "result": {"answers": {"relevant": probability}}})


def test_clef_request_shape_and_probability():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return _ok(0.93)

    assert _clef(handler).score("Agents That Plan", "We plan over tool calls.") == pytest.approx(0.93)
    assert seen["url"] == "https://api.cloudflare.com/client/v4/accounts/acct123/ai/run/@cf/cloudflare/clef-flash"
    assert seen["auth"] == "Bearer tok"
    assert seen["body"]["state"] == "Title: Agents That Plan\n\nAbstract: We plan over tool calls."
    question = seen["body"]["questions"]["relevant"]
    assert question["type"] == "noul"
    assert "LLM agents" in question["instructions"]


def test_clef_accepts_object_shaped_answer():
    assert _clef(lambda r: _ok({"probability": 0.4})).score("T", None) == pytest.approx(0.4)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(400, json={"success": False, "errors": [{"message": "bad schema"}]}),
        httpx.Response(200, json={"success": True, "result": {"answers": {}}}),
        httpx.Response(200, json={"success": True, "result": {"answers": {"relevant": "yes"}}}),
        httpx.Response(502, text="<html>bad gateway</html>"),
    ],
)
def test_clef_errors_raise(response):
    with pytest.raises((ScorerError, httpx.HTTPStatusError)):
        _clef(lambda r: response).score("T", "A")


def test_state_is_trimmed_to_clef_limit():
    abstract = 'Quotes "and" backslashes \\ and ünïcode. ' * 200
    state = build_state("A Title", abstract)
    assert len(json.dumps(state).encode()) <= STATE_MAX_BYTES
    assert state.startswith("Title: A Title\n\nAbstract: Quotes")
    assert state.endswith("…")
    assert build_state("Short", "Fits.") == "Title: Short\n\nAbstract: Fits."


class FakeScorer:
    name = "fake"

    def __init__(self, scores):
        self.scores = scores  # title -> probability, or an Exception to raise

    def score(self, title, abstract):
        result = self.scores[title]
        if isinstance(result, Exception):
            raise result
        return result


def _cfg():
    return Config(database_url="unused", arxiv=None, feeds=[], interests="x",
                  relevance_threshold=0.7, max_age_days=3)


def test_run_scoring_transitions(conn):
    db.upsert_articles(conn, [
        Article("arxiv:hi", "arXiv", "arxiv", "hi", "u"),
        Article("arxiv:lo", "arXiv", "arxiv", "lo", "u"),
        Article("arxiv:err", "arXiv", "arxiv", "err", "u"),
        Article("rss:blog", "Lab", "rss", "blog", "u"),
    ])
    scorer = FakeScorer({"hi": 0.9, "lo": 0.2, "err": ScorerError("boom")})

    assert run_scoring(_cfg(), conn, scorer) == {"relevant": 2, "skipped": 1, "error": 1}
    rows = {r[0]: r[1:] for r in conn.execute("SELECT fingerprint, status, relevance_score, attempts FROM articles")}
    assert rows["arxiv:hi"] == ("relevant", pytest.approx(0.9), 0)
    assert rows["arxiv:lo"] == ("skipped", pytest.approx(0.2), 0)
    assert rows["arxiv:err"] == ("new", None, 1)
    assert rows["rss:blog"] == ("relevant", None, 0)

    # The erroring paper is retried each run and gives up after MAX_ATTEMPTS.
    run_scoring(_cfg(), conn, scorer)
    run_scoring(_cfg(), conn, scorer)
    status, attempts = conn.execute("SELECT status, attempts FROM articles WHERE fingerprint = 'arxiv:err'").fetchone()
    assert (status, attempts) == ("failed", 3)
    assert run_scoring(_cfg(), conn, scorer) == {"relevant": 0, "skipped": 0, "error": 0}
