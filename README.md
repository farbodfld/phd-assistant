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

## Phase 4: scheduled position discovery

On the Lightsail host, configure the server's `.env` with the model key and
PostgreSQL credentials. Run `docker compose build app`, then check that the
host can read both a real listing and a project before relying on daily coverage:

```sh
docker compose run --rm --no-deps app phd probe-findaphd
docker compose up -d
docker compose logs app
```

`phd serve` schedules jobs.ac.uk followed by FindAPhD at `DAILY_RUN_TIME`
(default 09:00 in `Europe/London`). Each source has a separate run report
and same-day restart marker. A failure in one leaves the other scheduled.
The same `phd serve` process checks Gmail at `INBOX_CHECK_TIMES` (default
08:00, 14:00, 17:00 and 20:00), stores and analyses new PhD email, writes
reply drafts and retries emails whose analysis failed. Discovery runs in its
own thread, so a long sweep never delays an inbox check. Gmail needs the
OAuth files: run `phd gmail-auth` on your computer once, then copy the
`secrets/` folder next to `docker-compose.yml` on the server (it is mounted
into the container and the token is refreshed there).
Run both manually with `phd discover` (jobs.ac.uk, then FindAPhD), or one
source with `--source jobsac` or `--source findaphd`. FindAPhD reads both
single projects (`/phds/project/`) and doctoral programmes (`/phds/program/`,
such as DTPs, DLAs and CDTs). A programme listed under several partner
institutions is stored and screened once. FindAPhD saves validated
HTML on the server's `findaphd` volume, reuses details for seven days, and
records project IDs, suitable positions and run reports in the database.
The worker launches the same Patchright and headed Chrome combination as the
original scraper. Docker supplies a virtual display (Xvfb) and the server keeps
its own browser profile on the archive volume. The default image uses Google
Chrome on x86-64 and Debian Chromium on ARM. The scheduled worker waits up to
40 seconds for real listing or advert markup on each navigation, including an
automatic reload after a challenge. On a Windows laptop, install Chrome and
the project's Python dependencies before running `phd probe-findaphd`.

An unresolved challenge, denied request or unrecognised results page produces an
incomplete report. Existing tracker rows stay active. The next scheduled run
tries again. Persistent challenges require a machine-access route from
FindAPhD for full coverage from the server. The probe uses no database or model
and exits with a non-zero status on failure.

### If FindAPhD asks for a human on Lightsail

To review the actual server browser, run the following on Lightsail outside an
active sweep. It opens a temporary VNC session for the probe, shares the
worker's profile, and prompts for a temporary VNC password. The published port
is bound to the server's loopback interface. The container exits when the
probe finishes. To run the sweep in that same session, replace the final
`phd probe-findaphd` with `phd discover --source findaphd` and have the
database service running.

```sh
docker compose run --rm --no-deps -p 127.0.0.1:5901:5901 \
  -e FINDAPHD_PAGE_WAIT_SECONDS=180 app sh -ec '
    x11vnc -storepasswd /tmp/findaphd-vnc.pass
    x11vnc -display "$DISPLAY" -rfbport 5901 -rfbauth /tmp/findaphd-vnc.pass -bg -forever -o /tmp/findaphd-vnc.log
    exec phd probe-findaphd'
```

Before starting the probe, open an SSH tunnel from your computer using your
usual Lightsail SSH login (replace `USER@HOST` with that login):

```sh
ssh -N -L 5901:127.0.0.1:5901 USER@HOST
```

Connect a VNC viewer to local TCP port 5901, enter the temporary password, and
complete the check if the site offers one. The probe proceeds as soon as the
content appears. The server reuses the profile in future runs; the site
controls the validity of the session. If the site consistently requires
human input, ask FindAPhD for an approved automated feed or access for the
server IP. Keep port 5901 closed in the Lightsail firewall.

## Phase 5: first emails, reminders, statements and proposals

Right after the daily discovery, `phd serve` drafts:

- a first email for every open position whose route includes emailing the supervisor
  (SOP 10), ordered by deadline then score, with the master CV attached;
- a statement or proposal (Word and PDF, in the position folder) only when the advert asks
  for one with the enquiry; universities that ban AI writing get a reference draft that is
  never attached;
- reminders 14 days after the last email, as replies in the project's own thread (SOP 20).

Every email is a draft. Until the Telegram bot (phase 6), each draft is also created in Gmail
Drafts: read it, edit if needed and press Send. The next inbox check or outreach run sees it
in Sent and records it on the row (Email ✓, Email Sent, Reminder 1/2). A Gmail draft you
delete counts as denied. Positions without a supervisor address become a task for you.
The daily LLM budget (`DAILY_BUDGET_USD`) spreads a large backlog over several days.

Put the master CV in `data/documents/` (the name in `knowledge/profile.yaml`,
`documents.master_cv`). On the server, put it in `documents/` next to `docker-compose.yml`.

```sh
phd outreach --limit 3          # run now, at most 3 first emails
phd drafts                      # what is waiting
phd draft-show 12               # one draft in full
phd draft-initial 42            # first email for one position
phd write-statement 42          # statement for one position (Word and PDF)
phd write-proposal 42           # proposal for one position
phd sync-drafts                 # push to Gmail Drafts and record what you sent
```

## Phase 6: Telegram approval and deployment

Drafts are reviewed in stages, in this order: replies to supervisors, reminders, new first emails
(portals follow in phase 8). The bot first asks to start; each stage then shows its drafts numbered
("3 of 14 · draft #27") with **Approve** (sends it, the Gmail draft as it is now or directly with its
attachments), **Edit** (full new text starting with "Dear", or an instruction such as "make it
shorter") and **Deny** (discarded for good; not drafted again by itself). The stage ends with
"finished" and, from two drafts up, "Approve all" (asks once more). On "finished" the bot checks the
database and Gmail; only when nothing is left does it offer the next stage. To talk about one draft,
Reply to its message, or send its number (`#27`). Reports, tasks and email summaries arrive grouped
in one digest. Persian lines are shaped right to left with English words isolated, and the English
email sits in a quote block. Only `TELEGRAM_OWNER_ID` is answered. Commands: `/drafts`,
`/show 27`, `/status`, `/tasks`, `/cancel`.

### Set up the bot (once)

1. In Telegram, talk to @BotFather, `/newbot`, and put the token in `.env` as
   `TELEGRAM_BOT_TOKEN=...`.
2. Run `phd bot`, send `/start` to your bot; it replies with your id. Put it in `.env` as
   `TELEGRAM_OWNER_ID=...`, stop the bot (Ctrl+C) and run `phd telegram-test`.
3. `phd bot` runs the bot alone; `phd serve` runs it together with discovery, outreach and
   inbox checks. The first start does not replay old reports.

### Deploy on Lightsail

Use an x86 Ubuntu instance with at least 2 GB of memory, install Docker, and clone the repo.
Next to `docker-compose.yml` put:

- `.env` (model key, Telegram token and owner id, and `POSTGRES_PASSWORD`);
- `secrets/` (Gmail OAuth files from your computer);
- `documents/` (the master CV and transcripts);
- `import/` with a copy of your local `data/phd.db`, `data/findaphd/` and `data/positions/`
  (stop `phd serve` on your computer first).

```sh
docker compose build app
docker compose up -d db
docker compose run --rm --no-deps -v ./import:/import app phd copy-db sqlite:////import/phd.db
docker compose run --rm --no-deps -v ./import:/import app sh -c \
  'cp -r /import/findaphd/. /data/findaphd/ && cp -r /import/positions/. /data/positions/'
docker compose run --rm --no-deps app phd probe-findaphd
docker compose up -d
docker compose logs -f app
```

Run only one copy of `phd serve` (or `phd bot`) at a time: two bots with the same token
would fight over updates. After moving to the server, stop running them on your computer.

