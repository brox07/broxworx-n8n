# research-digest

Scheduled jobs that don't live in n8n. This one collects new AI research (arXiv and lab blogs),
summarizes the relevant items with an LLM, posts them to Discord, serves a small web page of
summaries, and sends a morning digest.

See [PLAN.md](PLAN.md) for the agreed design and phase checklist.

## Status

Phase 1 is done: sources are fetched and stored idempotently in Postgres. Triage,
summarization, delivery, the web app, and deployment are still to come.

## Local development

```bash
cd research-digest
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

export RESEARCH_DATABASE_URL=postgresql://user:pass@localhost:5432/research
python -m research_digest init-db   # creates the database if missing, applies schema
python -m research_digest ingest    # fetch every source once
```

Edit sources, interests, and thresholds in [`config/sources.yaml`](config/sources.yaml), or point
`RESEARCH_CONFIG` at a different file.

## Tests

```bash
pytest                                                    # parser tests only
RESEARCH_TEST_DATABASE_URL=postgresql://.../research_test pytest   # plus DB tests (wipes that db)
```
