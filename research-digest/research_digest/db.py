from collections.abc import Iterable
from importlib.resources import files

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from .models import Article


def ensure_database(database_url: str) -> None:
    """Create the target database if it doesn't exist.

    The Postgres container's init scripts only run against an empty volume, and the
    n8n stack's volume already exists, so the research database is created here instead.
    """
    params = conninfo_to_dict(database_url)
    dbname = params.get("dbname")
    if not dbname:
        raise ValueError("database URL must name a database")
    with psycopg.connect(make_conninfo(database_url, dbname="postgres"), autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)).fetchone()
        if not exists:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))


def connect(database_url: str) -> psycopg.Connection:
    return psycopg.connect(database_url)


def migrate(conn: psycopg.Connection) -> None:
    conn.execute(files("research_digest").joinpath("schema.sql").read_text())
    conn.commit()


def upsert_articles(conn: psycopg.Connection, articles: Iterable[Article]) -> int:
    """Insert articles not seen before; returns how many were new.

    Existing rows are left untouched so a re-fetch never resets pipeline status.
    """
    inserted = 0
    with conn.cursor() as cur:
        for a in articles:
            cur.execute(
                """
                INSERT INTO articles
                  (fingerprint, source, source_kind, title, url, authors, published_at, abstract)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (fingerprint) DO NOTHING
                """,
                (a.fingerprint, a.source, a.source_kind, a.title, a.url,
                 a.authors, a.published_at, a.abstract),
            )
            inserted += cur.rowcount
    conn.commit()
    return inserted


def claim(conn: psycopg.Connection, fingerprint: str, destination: str) -> bool:
    """Atomically mark an item as sent to a destination.

    True only for the caller that actually inserted the row, so overlapping runs can't
    both deliver the same item.
    """
    row = conn.execute(
        """
        INSERT INTO sent_items (fingerprint, destination) VALUES (%s, %s)
        ON CONFLICT DO NOTHING
        RETURNING fingerprint
        """,
        (fingerprint, destination),
    ).fetchone()
    conn.commit()
    return row is not None


def release(conn: psycopg.Connection, fingerprint: str, destination: str) -> None:
    """Undo a claim after a failed delivery so the next run retries it."""
    conn.execute(
        "DELETE FROM sent_items WHERE fingerprint = %s AND destination = %s",
        (fingerprint, destination),
    )
    conn.commit()
