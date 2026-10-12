-- Idempotent: safe to run on every start.

CREATE TABLE IF NOT EXISTS articles (
  id                BIGSERIAL   PRIMARY KEY,
  fingerprint       TEXT        NOT NULL UNIQUE,
  source            TEXT        NOT NULL,
  source_kind       TEXT        NOT NULL CHECK (source_kind IN ('arxiv', 'rss')),
  title             TEXT        NOT NULL,
  url               TEXT        NOT NULL,
  authors           TEXT,
  published_at      TIMESTAMPTZ,
  abstract          TEXT,
  content           TEXT,
  status            TEXT        NOT NULL DEFAULT 'new'
                    CHECK (status IN ('new', 'relevant', 'skipped', 'summarized', 'failed')),
  attempts          INT         NOT NULL DEFAULT 0,
  relevance_score   SMALLINT,
  relevance_reason  TEXT,
  summary           TEXT,
  key_points        JSONB,
  tags              TEXT[],
  model             TEXT,
  fetched_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  summarized_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS articles_status_idx ON articles (status);
CREATE INDEX IF NOT EXISTS articles_summarized_at_idx ON articles (summarized_at DESC);

-- Delivery ledger, same shape as the one proposed in the repo README roadmap.
CREATE TABLE IF NOT EXISTS sent_items (
  fingerprint  TEXT        NOT NULL,
  destination  TEXT        NOT NULL,
  sent_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (fingerprint, destination)
);

CREATE TABLE IF NOT EXISTS digests (
  digest_date    DATE        PRIMARY KEY,
  intro          TEXT,
  body_markdown  TEXT        NOT NULL,
  article_count  INT         NOT NULL,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
