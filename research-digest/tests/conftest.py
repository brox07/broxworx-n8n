import os
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_bytes():
    return lambda name: (FIXTURES / name).read_bytes()


@pytest.fixture
def conn():
    """A migrated connection to a throwaway database.

    Set RESEARCH_TEST_DATABASE_URL to a database the tests may freely wipe.
    """
    url = os.environ.get("RESEARCH_TEST_DATABASE_URL")
    if not url:
        pytest.skip("RESEARCH_TEST_DATABASE_URL not set")
    from research_digest import db

    db.ensure_database(url)
    with db.connect(url) as c:
        c.execute("DROP TABLE IF EXISTS articles, sent_items, digests")
        db.migrate(c)
        yield c
