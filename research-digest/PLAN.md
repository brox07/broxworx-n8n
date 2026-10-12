# Research Digest — Plan

A scheduled Python service that lives next to n8n in this stack. It collects new AI research,
has an LLM pick out what's relevant and summarize it, posts each summary to Discord, serves a
small web page listing everything, and sends one roll-up every morning by Discord and email.

This file records what we agreed on. It is the reference for scope, and it gets updated when a
decision changes.

## Decisions

| Topic | Decision |
| --- | --- |
| Sources | **arXiv categories** (via the arXiv API) and **AI lab / company blogs** (via RSS/Atom). Both configured in `config/sources.yaml`. |
| LLM | **OpenRouter**, the provider the n8n workflows already use. One API key, and the model is set per task in config. |
| Volume control | **LLM relevance filter.** A cheap model scores each arXiv abstract against an interests profile. Only items above a threshold get a full summary and a Discord post. Lab blog posts skip triage because there are only a few a day. Everything is stored and searchable in the web app either way. |
| Per-article delivery | A **Discord webhook** posting one embed per summarized item: title, 2–3 sentence summary, key points, and a link. |
| Daily roll-up | **Discord + email** every morning at 07:00 America/Chicago. It covers everything summarized since the previous digest went out, not "the last 24h", so a missed morning catches up instead of dropping items. |
| Web app | **FastAPI + server-rendered Jinja templates**, read-only. It shows a list of summaries, each linking to the source article. No JS build step. |
| Hosting | **The same Docker host.** Two new compose services built from one image (`research-worker` for the scheduler, `research-web` for the page). It reuses the existing Postgres container with a separate `research` database. |
| Web exposure | Published through the **existing Cloudflare Tunnel** at its own hostname and gated by **Cloudflare Access** (email login), so the app itself has no auth code. |
| Dedupe | Uses the `sent_items (fingerprint, destination)` atomic-claim table described in the repo README roadmap, so nothing goes to the same destination twice even if runs overlap or retry. |

## Architecture

```
                 ┌──────────────── research-worker (APScheduler) ────────────────┐
 arXiv API ─┐    │                                                                │
            ├──▶ │ ingest ──▶ triage (cheap LLM) ──▶ summarize (better LLM) ──▶ notify ──▶ Discord webhook (#research)
 Lab RSS  ──┘    │  hourly        arXiv only           relevant items           │
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
2. **Triage** (arXiv only): send title + abstract to the triage model with the interests
   profile. It returns `{score 0–10, reason}`. If the score is at or above the threshold the item
   becomes `relevant`, otherwise `skipped`. Lab blog posts go straight to `relevant`.
3. **Summarize:** for arXiv, use the abstract (later, optionally the full text from arxiv.org/html).
   For blogs, extract the article body from the page. The summarize model returns
   `{summary, key_points[], tags[]}` and the item becomes `summarized`. Failures increment an
   attempt counter and become `failed` after 3 attempts.
4. **Notify:** for each `summarized` item, atomically claim `(fingerprint, 'discord:articles')`
   in `sent_items`, then post. The claim happens first, so overlapping runs can't double-post.
   If the post fails, the claim is released so the next run retries.
5. **Digest** (daily): claim every summarized item not yet claimed for `digest`. Group by source,
   have the LLM write a short "what mattered today" intro, render Markdown (Discord) and HTML
   (email), send both, and store the digest in `digests` so the web app can show past ones.

### Data model

```sql
articles(
  id, fingerprint UNIQUE, source, source_kind ('arxiv'|'rss'),
  title, url, authors, published_at, abstract, content,
  status ('new'|'relevant'|'skipped'|'summarized'|'failed'), attempts,
  relevance_score, relevance_reason,
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
| `OPENROUTER_API_KEY` | LLM access |
| `DISCORD_ARTICLES_WEBHOOK_URL` | Per-article posts |
| `DISCORD_DIGEST_WEBHOOK_URL` | Morning roll-up |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `DIGEST_EMAIL_FROM` / `DIGEST_EMAIL_TO` | Digest email. Any SMTP provider works (Gmail app password, Resend, Fastmail, …). |
| `RESEARCH_WEB_HOST` | Public hostname routed by the tunnel |

`config/sources.yaml` holds the arXiv categories, the RSS feeds, the interests profile, the
relevance threshold, the model names, and the schedule times.

## Phases

- [x] **0. Plan**: this document.
- [x] **1. Skeleton + ingest**: package layout, config loading, schema/migrations, arXiv and RSS
      fetchers, idempotent upsert, `python -m research_digest ingest` CLI, tests.
- [ ] **2. LLM triage + summarization**: OpenRouter client, prompts with JSON output, status
      transitions, retry/attempt cap, cost guard (max items per run).
- [ ] **3. Discord per-article posts**: webhook embeds, `sent_items` claim/release, rate-limit
      handling.
- [ ] **4. Web app**: list and filter by source/tag/date, per-article detail, digest archive, health
      endpoint.
- [ ] **5. Daily digest**: grouping, LLM intro, Discord message (split under the 2000-char limit),
      HTML + plain-text email.
- [ ] **6. Deploy**: Dockerfile, compose services, one-time database creation, tunnel hostname +
      Cloudflare Access setup notes, README section.

## Open questions / later

- Full-text summarization for arXiv (HTML version) once abstract-only quality has been checked.
- Pull the interests profile from feedback, e.g. 👍/👎 reactions on Discord posts adjusting it.
- Whether the n8n workflows should eventually adopt the same `sent_items` table (README roadmap
  item 1). The schema is deliberately the same so that stays possible.
