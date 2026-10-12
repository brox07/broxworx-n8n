# Research Digest — Plan

A scheduled Python service that lives next to n8n in this stack. It collects new AI research,
has a decision model pick out what's relevant and an LLM summarize it, serves a small web page
listing everything, and sends one roll-up every morning by Discord and email.

This file records what we agreed on. It is the reference for scope, and it gets updated when a
decision changes.

## Decisions

| Topic | Decision |
| --- | --- |
| Sources | **arXiv categories** (via the arXiv API) and **AI lab / company blogs** (via RSS/Atom). Both configured in `config/sources.yaml`. |
| Relevance scoring | A **decision model, Jev (TypeSafe) or Clef (Cloudflare)**, rather than a chat LLM. These models answer a structured question ("is this paper relevant to this interests profile?") with a probability in one pass, which is quicker, cheaper and easier to put a threshold on than parsing a 0–10 score out of generated text. It sits behind a small `Scorer` interface, so switching between Jev, Clef or an LLM fallback is a config change. **Open:** which one, and its API details (see Open questions). |
| Summaries | **OpenRouter**, the provider the n8n workflows already use. Only items that pass scoring get summarized. |
| Volume control | arXiv papers whose relevance probability is below `relevance_threshold` are stored but left out of the digest. Lab blog posts skip scoring because there are only a few a day. Everything stays searchable in the web app either way. |
| Delivery | **One morning digest, no per-article messages.** arXiv publishes once a day, around 20:00 ET Sunday through Thursday, so a day's papers arrive together anyway. At 07:00 America/Chicago a single run sends the Discord message and the email. It covers everything summarized since the previous digest went out, not "the last 24h", so a missed morning catches up instead of dropping items. Friday and Saturday nights have no arXiv mailing, so weekend digests carry only blog posts, and an empty digest is skipped. |
| Web app | **FastAPI + server-rendered Jinja templates**, read-only. It shows a list of summaries, each linking to the source article. No JS build step. |
| Hosting | **The same Docker host.** Two new compose services built from one image (`research-worker` for the scheduler, `research-web` for the page). It reuses the existing Postgres container with a separate `research` database. |
| Web exposure | Published through the **existing Cloudflare Tunnel** at its own hostname and gated by **Cloudflare Access** (email login), so the app itself has no auth code. |
| Dedupe | Uses the `sent_items (fingerprint, destination)` atomic-claim table described in the repo README roadmap, so nothing goes to the same destination twice even if runs overlap or retry. |

## Architecture

```
                 ┌──────────────── research-worker (APScheduler) ────────────────┐
 arXiv RSS ─┐    │                                                                │
            ├──▶ │ ingest ──▶ score (Jev/Clef) ──▶ summarize (OpenRouter)           │
 Lab RSS  ──┘    │  hourly      arXiv only           relevant items               │
                 │                                                                │
                 │ digest (07:00 CT) ──────────────────────────────────────────▶ Discord webhook (#research-digest)
                 │                                                            └──▶ SMTP email
                 └──────────────────────────────┬─────────────────────────────────┘
                                                │
                                         Postgres `research` db
                                                │
                 research-web (FastAPI) ◀───────┘ ◀── cloudflared ◀── Cloudflare Access ◀── you
```

### Pipeline stages

Each stage is idempotent and driven by an article's `status`, so a crash mid-run just resumes
on the next tick.

1. **Ingest:** fetch every configured source. Upsert into `articles` keyed on a stable
   fingerprint (arXiv id without version, or the RSS guid/link), so re-fetching never duplicates.
   New rows start as `new`.
2. **Score** (arXiv only): ask the decision model whether title + abstract match the interests
   profile and get back a relevance probability. Scoring is batched, since the whole evening drop
   (500+ papers) lands at once. At or above `relevance_threshold` the item becomes `relevant`,
   otherwise `skipped`. Lab blog posts go straight to `relevant`.
3. **Summarize:** for arXiv, use the abstract (later, optionally the full text from arxiv.org/html).
   For blogs, extract the article body from the page. The summarize model returns
   `{summary, key_points[], tags[]}` and the item becomes `summarized`. Failures increment an
   attempt counter and become `failed` after 3 attempts.
4. **Digest** (daily): claim every summarized item not yet claimed for `digest` in
   `sent_items`. The claim is atomic, so overlapping runs can't double-send, and claims are
   released if sending fails. Group items by source, most relevant first, and have the LLM write
   a short "what mattered today" intro. Then send:
   - **Discord:** the intro plus one line per item (title, one-sentence takeaway, link). Discord
     caps messages at 2,000 characters, so a busy day is split into several consecutive
     messages, with a link to that day's page in the web app at the end.
   - **Email:** the full version, with each item's summary, key points and link, as HTML with a
     plain-text alternative.

   Finally the digest is stored in `digests` so the web app can show past ones.

### Data model

```sql
articles(
  id, fingerprint UNIQUE, source, source_kind ('arxiv'|'rss'),
  title, url, authors, published_at, abstract, content,
  status ('new'|'relevant'|'skipped'|'summarized'|'failed'), attempts,
  relevance_score REAL (0–1 probability), scorer,
  summary, key_points JSONB, tags TEXT[], model,
  fetched_at, summarized_at
)
sent_items(fingerprint, destination, sent_at, PRIMARY KEY (fingerprint, destination))
digests(digest_date PRIMARY KEY, intro, body_markdown, article_count, created_at)
```

## Configuration

Secrets come from `.env`. The rest lives in `config/sources.yaml`.

| Variable | Purpose |
| --- | --- |
| `RESEARCH_DATABASE_URL` | Postgres DSN for the `research` database |
| `SCORER_API_KEY` (name TBD per provider) | Jev or Clef access |
| `OPENROUTER_API_KEY` | Summaries and digest intro |
| `DISCORD_DIGEST_WEBHOOK_URL` | Morning roll-up |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `DIGEST_EMAIL_FROM` / `DIGEST_EMAIL_TO` | Digest email. Any SMTP provider works (Gmail app password, Resend, Fastmail, …). |
| `RESEARCH_WEB_HOST` | Public hostname routed by the tunnel |

`config/sources.yaml` holds the arXiv categories, the RSS feeds, the interests profile, the
relevance threshold, the model names, and the schedule times.

## Phases

- [x] **0. Plan**: this document.
- [x] **1. Skeleton + ingest**: package layout, config loading, schema/migrations, arXiv and RSS
      fetchers, idempotent upsert, `python -m research_digest ingest` CLI, tests.
- [ ] **2. Relevance scoring**: `Scorer` interface, a Jev or Clef adapter, batching, and status
      transitions. Blocked on the Open question below.
- [ ] **3. Summarization**: OpenRouter client, JSON-output prompt, retry/attempt cap, cost guard
      (max items per run).
- [ ] **4. Daily digest**: grouping, LLM intro, Discord messages split under the 2,000-char
      limit, HTML + plain-text email over SMTP, `sent_items` claim/release.
- [ ] **5. Web app**: list and filter by source/tag/date, per-article detail, digest archive, health
      endpoint.
- [ ] **6. Deploy**: Dockerfile, compose services, one-time database creation, tunnel hostname +
      Cloudflare Access setup notes, README section.

## Open questions / later

- **Jev or Clef, and how to call it.** These are decision models: they return probabilities
  over a fixed set of options instead of generating text. Jev comes from TypeSafe and is closed,
  behind an API. Clef comes from Cloudflare (released 2026-10-01) in 27B and 9B "flash" sizes as
  open weights under Apache 2.0. I haven't been able to confirm either one's API: the endpoint,
  request schema and auth. Clef might be served on Workers AI, but that isn't confirmed yet.
  Self-hosting Clef-flash would need a GPU, which this Docker host may not have. Needed before
  Phase 2: the choice of model and a link to its API docs or model card.
- **Which SMTP provider** for the digest email.

- Full-text summarization for arXiv (HTML version) once abstract-only quality has been checked.
- Pull the interests profile from feedback, e.g. 👍/👎 reactions on Discord posts adjusting it.
- Whether the n8n workflows should eventually adopt the same `sent_items` table (README roadmap
  item 1). The schema is deliberately the same so that stays possible.
