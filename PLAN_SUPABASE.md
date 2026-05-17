# PLAN_SUPABASE.md — migrating off the static architecture

> **Status:** planning. Nothing built yet. This document supersedes the
> "no backend, ever" stance in `CLAUDE.md` — that file must be rewritten
> as part of Phase 0 (see §Phase 0) or it will mislead every future
> session.

## Why

The project is moving from its static-site architecture (SQLite +
committed JSON + mailto/WhatsApp submissions, GitHub Pages) to a real
backend on **Supabase**. Postgres becomes the single source of truth;
the frontend is rebuilt as a **Next.js** app; submissions write
directly to the database.

## Decisions (locked)

| Question | Decision |
| --- | --- |
| Frontend framework | **Next.js** |
| Frontend hosting | **The DigitalOcean droplet** (`134.122.55.238`) |
| Scouting reports | **Keep as Python artefacts** — `scout.py` stays, reads Postgres; PNGs go to Supabase Storage |
| Raw JSON cache | **Local-only on the droplet**, gitignored |
| Database | **Supabase Postgres** (Pro plan — see §Cost) |

## Data reality (measured 2026-05-17)

| Table | Rows |
| --- | ---: |
| `balls` | 21,353,132 |
| `match_players` | 1,618,810 |
| `batting` | 1,592,501 |
| `fall_of_wickets` | 989,816 |
| `bowling` | 807,155 |
| `innings` | 153,711 |
| `match_bbb` | 44,779 |
| `matches` | 82,525 |
| `clubs` | 1,584 |

- `rainham.db` = **4.4 GB** SQLite. A Postgres copy with indexes is
  expected at **8–15 GB**.
- Raw JSON cache = **255,806 files / 14 GB** — this is what bloats
  `.git` to 12 GB. It stays a **local-only cache on the droplet**,
  gitignored. Never goes into Postgres or git.
- Metadata is barely started: 14 player files, 14 submissions, 0
  videos — trivial to migrate.
- The DB is **club-agnostic** (82k matches, 1,584 clubs). The whole
  thing migrates, not just Rainham.

## Target architecture

The droplet is the single box. It runs:

- **nginx** — reverse proxy on :80 → Next.js on :3000 (and, until
  Phase 7, still serves the legacy static site).
- **Next.js** — `output: 'standalone'`, run by a systemd service.
- **Ingestion pipeline** — `fetch.py` + the rewritten loader, on a
  systemd timer; writes straight to Supabase Postgres.
- **Raw JSON cache** — 14 GB on local disk.

Supabase provides:

- **Postgres** — single source of truth: analytics + metadata +
  submissions + live matchweek state.
- **Auth** — Jono as admin; submitters anonymous or magic-link.
- **Realtime** — live matchweek push to clients (replaces `cf_worker`).
- **Storage** — scout PNGs (and any other generated artefacts).

**Droplet sizing:** confirm the droplet has headroom — the 14 GB raw
cache grows over time, plus Node/Next build artefacts and the OS.
Target **≥ 50 GB disk, ≥ 2 GB RAM**. Check `df -h` / `free -m` before
Phase 2; resize the droplet if needed.

## Phases

Each phase is independently shippable. The legacy static site keeps
working until Phase 7.

### Phase 0 — Foundations
- Create the Supabase project (Pro plan).
- Install the Supabase CLI; scaffold `supabase/migrations/`.
- **Rewrite `CLAUDE.md`** — remove the "no backend" mandate and the
  mailto/WhatsApp architecture notes; document the Supabase setup so
  future sessions are not misled.
- Confirm droplet disk/RAM headroom.

### Phase 1 — Schema + one-time data load
- Translate the SQLite schema to Postgres migrations: explicit types,
  primary keys, foreign keys, and the indexes from `CLAUDE.md`
  (`idx_balls_bowler`, `idx_balls_batter`, `idx_balls_match`, …).
- Keep `innings_seq` as a real column. Keep the club-agnostic shape
  (`team_batting_club_id` / `team_bowling_club_id`; no `is_rainham`).
- Metadata tables: `players` (canonical, player-keyed),
  `player_aliases`, `player_seen_clubs` (both **derived** — a view or
  a rebuild job, never hand-edited), `videos` (match-keyed),
  `submissions`.
- One-time bulk loader: SQLite → Postgres via `COPY` (21M `balls`
  rows = minutes, not an `INSERT` loop).
- Re-run the `SUM(runs_bat + runs_extra)` per-innings validation
  against Postgres. Reference-check headline numbers against
  Play-Cricket (the validation discipline from `CLAUDE.md`).

### Phase 2 — Ingestion pipeline cutover
- Rewrite the `build_db.py` loader to **upsert into Postgres** instead
  of writing SQLite. `fetch.py` is unchanged (still hits the
  Play-Cricket API).
- Raw JSON cache: keep on the droplet's local disk; add `data/raw/`
  to `.gitignore`. This is what finally shrinks the repo.
- Schedule: a systemd timer on the droplet runs
  `fetch.py --only-recent 1 --force-seasons` + the loader. Replaces
  "run it manually at the start of a session".

### Phase 3 — Frontend rebuild (Next.js)
- Scaffold the Next.js app (App Router, `output: 'standalone'`).
- Port pages: data repository, clubs browse, club roster, player
  page, matchweek, RCC dashboard, reports index.
- Preserve the design system — mobile-first `max-width: 540px`, hero
  + cards + pills, tabular numerics (`FRONTEND_DESIGN.md`). Port the
  CSS from `_app_lib.CSS`.
- Data access via Supabase server components / queries.
- Deploy: build on the GitHub runner, rsync the standalone bundle to
  the droplet, restart the systemd service (extends the existing
  `deploy-droplet.yml` rsync pattern — see Phase 7).

### Phase 4 — Auth + submissions + RLS
- Supabase Auth: Jono = admin.
- RLS: anon may `INSERT` into `submissions`; only admin may `UPDATE`
  / approve; `players` is writable only via an `approve_submission()`
  Postgres function.
- The submit form does `supabase.from('submissions').insert(...)` —
  this **removes the mailto/WhatsApp flow**.
- Admin approval UI in the app — replaces "Jono pastes the submission
  into Claude Code".

### Phase 5 — Live matchweek
- Replace `cf_worker`: a scheduled poller (droplet cron or a Supabase
  edge function) writes live match state into a `matchweek` table.
- Clients subscribe via Supabase Realtime for live updates — replaces
  the `?worker=` LIVE mode fetch in `matchweek.html`.

### Phase 6 — Reports
- Repoint `scout.py` and the ad-hoc generators (`top_run_scorers.py`,
  `streaks_and_fifties.py`, `oneill_vs_hothi.py`) at Postgres.
- Reports stay as Python-generated artefacts; PNGs upload to Supabase
  Storage. `build_index.py` still produces `reports/index.html`.

### Phase 7 — Cutover & decommission
- nginx serves only the Next.js app; retire the legacy static site.
- Change `deploy-droplet.yml` from "rsync static `_site`" to
  "build Next.js → rsync standalone bundle → restart service".
- Retire the GitHub Pages workflow (`static.yml`).
- Delete the committed JSON bundles (`app/data/players.json`,
  `app/data/matchweek/*.json`) and the generated `app/` pages.

## Cost

- **Supabase Pro ≈ $25/mo**, plus a probable compute/disk add-on for a
  10–15 GB database. The free tier (500 MB) is not an option.
- Droplet: existing cost; may need a resize for disk headroom.
- Next.js hosting: none extra — it runs on the droplet.

## Open risks / watch-list

- **Postgres size** — confirm the loaded DB size early (Phase 1); it
  drives the Supabase plan tier.
- **Droplet as a single point of failure** — app + pipeline + cache
  all on one box. Acceptable for this project; note it.
- **Pipeline code on the droplet** — deploy via a shallow, sparse
  checkout (exclude `data/`) or rsync the `.py` files; never a full
  clone (12 GB `.git`).
- **`CLAUDE.md` drift** — until it is rewritten (Phase 0), it actively
  contradicts this plan.
