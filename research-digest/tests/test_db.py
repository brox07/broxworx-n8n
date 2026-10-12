from research_digest import db
from research_digest.models import Article


def _article(fp="arxiv:1", title="T"):
    return Article(fingerprint=fp, source="arXiv", source_kind="arxiv", title=title, url="https://x")


def test_upsert_is_idempotent_and_preserves_status(conn):
    assert db.upsert_articles(conn, [_article("arxiv:1"), _article("arxiv:2")]) == 2
    conn.execute("UPDATE articles SET status = 'summarized' WHERE fingerprint = 'arxiv:1'")
    conn.commit()

    assert db.upsert_articles(conn, [_article("arxiv:1", title="changed"), _article("arxiv:3")]) == 1
    status, title = conn.execute(
        "SELECT status, title FROM articles WHERE fingerprint = 'arxiv:1'"
    ).fetchone()
    assert (status, title) == ("summarized", "T")


def test_claim_succeeds_once_per_destination_and_release_allows_retry(conn):
    assert db.claim(conn, "arxiv:1", "discord:articles")
    assert not db.claim(conn, "arxiv:1", "discord:articles")
    assert db.claim(conn, "arxiv:1", "digest")

    db.release(conn, "arxiv:1", "discord:articles")
    assert db.claim(conn, "arxiv:1", "discord:articles")
