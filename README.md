# PhD Assistant

Farbod's agentic workflow for the PhD search: finds positions, keeps the tracker,
drafts emails and documents, and asks him on Telegram before anything is sent.

## Phase 1 (done): foundation and data migration

| Part | Where |
|---|---|
| Settings (.env) | `app/config.py`, `.env.example` |
| Database schema | `app/db/models.py` |
| Tracker layout and colours | `app/tracker/layout.py` |
| One-off import (tracker, folders, applications) | `app/tracker/importer.py`, `seed/` |
| Excel export in the exact current format | `app/tracker/exporter.py` |
| Claude client, model routing, caching, cost guard | `app/llm/` |

```bash
pip install -e ".[dev]"
phd import "/path/to/Farbod_PhD_Position_Matches.xlsx"
phd export exports/Farbod_PhD_Position_Matches.xlsx
TRACKER_XLSX=/path/to/tracker.xlsx pytest
```

Locally the database is SQLite (`data/phd.db`); in production `DATABASE_URL`
points at the PostgreSQL service in `docker-compose.yml`.

### Model routing

`app/llm/router.py` maps every task to a tier. Cheap (Haiku 5.5): classifying,
screening, scoring, summaries. Strong (Opus 5.5): every email, reply, statement,
proposal, risk check, portal step and chat. Change a task's tier there only.
Stable context is cached; every call's tokens and cost are logged in
`llm_calls`, and calls stop once `DAILY_BUDGET_USD` is reached.

## Phase 2 (done): knowledge and rules

| Part | Where |
|---|---|
| Verified profile (only facts the agent may state) | `knowledge/profile.yaml` |
| Personal identifiers for portals (local only, gitignored) | `knowledge/private.yaml` (template: `private.example.yaml`) |
| Standing rules as SOPs | `sops/*.md` |
| Per-university rules (limits, AI policy, English, portal traps) | `knowledge/institutions.yaml` |
| Insights from supervisor replies | `seed/insights.yaml` |
| Context builder for LLM tasks | `app/knowledge/loader.py` |
| Text checks (dash connectors, fabrication, tone) | `app/rules/text_checks.py` |
| Funding classifier | `app/rules/funding.py` |
| Rules engine: flags with inform / followup / decide / block | `app/rules/engine.py` |

```bash
phd seed-knowledge     # load insights
phd check              # refresh flags; prints decisions that need Farbod
```

## Phase 3 (done): Gmail, replies and insights

| Part | Where |
|---|---|
| Gmail OAuth and API wrapper | `app/gmail/client.py` (client JSON and token in `secrets/`, gitignored) |
| Message parsing, quoted-text stripping | `app/gmail/parse.py` |
| Relevance filter and position matching | `app/mail/matching.py` |
| History backfill and inbox processing | `app/mail/inbox.py` |
| Structured analysis (category, insights, closures, tasks, reply) | `app/mail/analysis.py` |
| Due reminders (14 days, max two) | `app/mail/reminders.py` |
| Fixed-time scheduler (09:00, 14:00, 17:00, 20:00 London) | `app/scheduler.py` |

```bash
phd gmail-auth                 # once, opens the browser
phd backfill --since 2026-07-01
phd inbox-check
phd reminders-due
```

Every reply draft and notification is stored (`drafts`, `notifications`) for the
Telegram bot in phase 6. Nothing is sent from this phase.

### Export notes

The export follows the tracker rules: sorted by score with Rank renumbered,
score colours on Rank and Score, Apply Method colours, closed rows greyed from
column C, links in the Link column, the "Checked and excluded" block, and the
Summary counts and next deadlines recomputed from the data.
