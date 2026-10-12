# broxworx-n8n

Self-hosted [n8n](https://n8n.io) automation stack, deployed with Docker Compose and exposed
through a Cloudflare Tunnel — no inbound ports opened on the host.

## Architecture

| Service | Image | Role |
| --- | --- | --- |
| `n8n` | `n8nio/n8n:latest` | Workflow engine and editor UI |
| `postgres` | `postgres:16` | Persistent store for workflows, credentials, and execution history |
| `cloudflared` | `cloudflare/cloudflared:latest` | Outbound-only tunnel publishing n8n at a public hostname |

A few deliberate choices worth calling out:

- **Postgres over the default SQLite.** n8n ships with SQLite, which is fine for evaluation but
  serializes writes and makes execution history expensive to query once it grows. Postgres keeps
  concurrent executions from contending on a single writer.
- **Health-gated startup.** `n8n` declares `depends_on: postgres: condition: service_healthy`, and
  Postgres defines a `pg_isready` healthcheck. Without this, n8n races the database on boot and
  crash-loops until Postgres finishes initializing.
- **Cloudflare Tunnel instead of port forwarding.** `cloudflared` dials out to Cloudflare, so the
  host needs no inbound firewall rule and no public IP. TLS terminates at Cloudflare's edge, which
  is why the container itself runs `N8N_PROTOCOL=http`.
- **Named volumes** (`db_storage`, `n8n_storage`) so container replacement — including image
  upgrades — never takes the data with it.

## Workflows

Four workflows running on this stack live in [`workflows/`](workflows/), exported as JSON and
importable via **Workflows → Import from File**.

### [`obsidian-agent.json`](workflows/obsidian-agent.json)

A Discord-fronted agent over an Obsidian vault. A webhook and a Discord listener both feed a
LangChain agent whose conversation state lives in **Postgres-backed chat memory**, so context
survives container restarts rather than dying with the process. The agent is given a Discord tool
and an MCP client tool, letting it both read from and reply into the vault and chat.

### [`racing-news.json`](workflows/racing-news.json)

Scheduled motorsport digest: RSS ingest → filter → LLM summarization → Discord.

The substantive piece is the parser that turns Discord's markdown race calendar into structured
rows. It walks the message line by line, tracking the current series and track across `###`
headings, strips Discord custom emoji and regional-indicator flags, and splits each entry into
series / subseries / event type / event name — classifying a fragment as a *type* rather than a
*name* by matching against a keyword set (`qualifying`, `practice`, `sprint`, …).

Event times arrive as Discord `<t:unix:F>` tokens and are rendered in `America/Chicago` via
`Intl.DateTimeFormat` with `timeZoneName: "longOffset"`, so the emitted offset follows CST/CDT
instead of being pinned to one of them.

### [`flipping-leads.json`](workflows/flipping-leads.json)

Scrapes listings daily, parses the HTML, runs them past an agent, and emails the result before
upserting back into Postgres.

The pipeline is shaped for database-level dedupe — `select existing rows → filter → act → upsert` —
but the filter predicate is not yet configured, so every scraped listing currently reaches the
agent whether or not it has been seen before. Wiring that up is the first item under
[Roadmap](#roadmap) below.

### [`dev-subs-summaries.json`](workflows/dev-subs-summaries.json)

Two parallel RSS pipelines — release notes and engineering blogs — each filtered and summarized by
its own agent, then routed to a different Discord channel (`#dev-release-notes`, `#dev-blogs`).

## Other scheduled jobs

### [`research-digest/`](research-digest/)

A Python service that runs alongside n8n for jobs that are easier to write as code. It pulls new
arXiv papers and AI lab blog posts, has an LLM triage and summarize them, posts each summary to
Discord, serves a read-only web page of summaries, and sends a morning digest by Discord and
email. See its [plan](research-digest/PLAN.md) for design and progress.

## Roadmap

### 1. A delivery-state store, so nothing sends twice

Every scheduled workflow here decides what to send by filtering on a **time window relative to the
run** — and each window is at least as wide as the interval between runs:

| Workflow | Runs every | Selection window | Consequence |
| --- | --- | --- | --- |
| `racing-news` (news) | 1 hour | `pubDate > $now - 2h` | every article falls in two consecutive runs, so each is summarized and posted twice |
| `racing-news` (races) | 1 hour | `event_time ∈ [$now - 5m, $now + 65m]` | a 70-minute window on a 60-minute cadence — races starting in the 10-minute overlap alert twice |
| `dev-subs-summaries` | 2 hours | `pubDate > $now - 2h` | window and interval are exactly equal, so correctness depends on the scheduler never drifting or retrying |

Widening a window to tolerate a missed run makes duplicates worse; narrowing it to avoid duplicates
means a single failed execution drops items permanently. The window cannot solve both, because
**"has this already been sent?" is a question about history, not about time.**

The fix is to persist delivery state — a table recording what has gone out:

```sql
CREATE TABLE sent_items (
  fingerprint  TEXT        NOT NULL,   -- stable hash of RSS guid / listing URL / race event id
  destination  TEXT        NOT NULL,   -- discord channel, email address, ...
  sent_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (fingerprint, destination)
);
```

Note the key choice: `(fingerprint, destination)` rather than `fingerprint` alone, so the same item
can legitimately go to two different channels while still never repeating in either.

The important detail is *how* the table gets consulted. The obvious approach — `SELECT` known
fingerprints, then filter the batch — is what `flipping-leads` is shaped for, but it leaves a
window between the read and the send: two overlapping executions can both read "not sent" and both
deliver. Doing the claim in one atomic statement closes that:

```sql
INSERT INTO sent_items (fingerprint, destination)
VALUES ($1, $2)
ON CONFLICT DO NOTHING
RETURNING fingerprint;
```

Rows come back only for fingerprints this execution actually claimed; anything already recorded
returns nothing and is dropped. Dedupe and claim happen in a single round trip, so correctness no
longer depends on runs not overlapping — which in turn means the time windows can be widened
generously for catch-up without any risk of double-sending.

This lands in three places: the empty filter in `flipping-leads`, and the send steps of
`racing-news` and `dev-subs-summaries`.

### 2. Rebuilding race notifications on persisted events

Race alerts currently live inside `racing-news` as a chain of filters over a freshly re-parsed
Discord message. The parser produces well-structured rows — series, subseries, event type, event
name, track, start time — and then discards them at the end of every run. Two limitations follow:

- **Missed runs are unrecoverable.** A race is announced only if its start time happens to land in
  the `[$now - 5m, $now + 65m]` window at the moment a run fires. If the stack is down or an
  execution fails during that hour, the notification is not late — it never happens.
- **Only one alert is possible per race.** There is nowhere to record that a 24-hour warning
  already went out, so a 24h / 1h / at-start ladder cannot be expressed.

Splitting ingest from notification fixes both. Parsed events get upserted into a table keyed on a
deterministic event id, so re-parsing the same calendar updates rows rather than duplicating them
and schedule changes propagate on their own:

```sql
CREATE TABLE race_events (
  event_id    TEXT PRIMARY KEY,        -- e.g. slug(series || track || event_time)
  series      TEXT,
  subseries   TEXT,
  event_type  TEXT,                    -- practice / qualifying / race / sprint
  event_name  TEXT,
  race_track  TEXT,
  event_time  TIMESTAMPTZ NOT NULL
);
```

Notification then becomes a separate scheduled reader: select events whose start falls inside the
next reminder horizon, and use the `sent_items` claim above with a fingerprint of
`event_id + reminder_stage`. Each stage fires exactly once, a late run still delivers rather than
silently skipping, and adding a new reminder tier is a new stage value rather than new workflow
branches.

Persisting events also makes the schedule multi-source. The Discord calendar becomes one ingest
path among several — series APIs can upsert into the same table, with the primary key collapsing
any overlap between them, and notification logic stays untouched regardless of where an event
came from.

### A note on the exports

Credentials are **not** included. n8n stores only a reference (`{"id": "...", "name": "..."}`) in
exported JSON and keeps secret material in its own encrypted store, so importing these will prompt
you to attach your own credentials. Personal identifiers (email addresses, Discord guild IDs) have
been replaced with placeholders, and each workflow is exported with `active: false` so nothing
arms itself on import.

## Running it

```bash
cp .env.example .env    # then fill in real values
docker compose up -d
```

n8n is then reachable at `WEBHOOK_URL`, or on `localhost:${N8N_PORT}` directly.

## Configuration

All configuration is environment-driven; see [`.env.example`](.env.example) for the full set.

| Variable | Purpose |
| --- | --- |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | Database credentials, shared by both the Postgres and n8n services |
| `CLOUDFLARE_TUNNEL_TOKEN` | Tunnel credential from Cloudflare Zero Trust |
| `WEBHOOK_URL` | Public base URL n8n advertises for webhook endpoints and editor links |
| `N8N_HOST` / `N8N_PORT` | Hostname and port the n8n container binds |

`NODE_FUNCTION_ALLOW_EXTERNAL=cheerio` whitelists `cheerio` for use inside Code nodes, for
workflows that parse HTML.

The real `.env` is gitignored. Rotate any credential that has been committed anywhere.
