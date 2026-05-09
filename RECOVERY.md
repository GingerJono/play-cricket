# Recovery — how to rebuild this project from scratch

If this session breaks (or you switch machines), here's the exact sequence
to bring the project back to life. Everything except `data/rainham.db` is
committed to git, and the DB is fully reproducible from the raw JSON.

## Source of truth

```
data/raw/                          ← committed, don't ever delete
  matches/<site_id>/<season>.json     per-league season summaries
  match_detail/<match_id>.json        per-match scorecards
  balls/<match_id>/<innings>.json     per-match BBB rows (RV)
  rv_match/<match_id>.json            RV match-id mapping
  nv_match/<match_id>.json            NV Play scorecard payload
data/skill_snapshot.csv            ← committed
model/bench/<slug>.json            ← committed (per-league bench result)
model/bench/universe.json          ← committed (top-N senior tier scrape)
```

Anything not in that list is derived. In particular:

- `data/rainham.db` — gitignored. Local-only. Rebuild from raw JSON.
- Any `*.csv` / `*.parquet` exports — derived. Rebuild from the DB.

## Recovery steps

```bash
# 1. clone
git clone git@github.com:GingerJono/play-cricket.git
cd play-cricket
git checkout claude/review-handover-docs-LYMAn   # or whichever branch

# 2. install deps (one-off)
pip install numpy pandas scikit-learn lightgbm boto3

# 3. (optional) restore latest DB snapshot from S3 — see backup_db.py
python3 model/ops/restore_db.py

# OR rebuild the DB from raw JSON (5-30 min depending on cache size)
python3 build_db.py

# 4. resume the all-leagues fetch (idempotent — skips cached files)
python3 model/bench/run_all.py

# 5. refresh the skill snapshot
python3 model/skill/build_snapshot.py

# 6. re-run the POC
python3 model/poc/run_poc_v2.py
```

Step 4 only does network work for matches that aren't already cached.
Steps 5 / 6 are pure CPU — no API calls.

## What CAN be lost vs what CAN'T

| Thing | At risk if session dies? | Recovery |
|---|---|---|
| Raw JSON cache | No — committed in git | clone the repo |
| Per-league bench JSONs | No — committed | clone |
| Skill snapshot CSV | No — committed | regenerate via `build_snapshot.py` |
| Rainham DB | Yes — gitignored, local | rebuild via `build_db.py`, OR restore from S3 |
| Background `run_all.py` process | Yes — process dies | re-run `run_all.py` (idempotent) |
| In-memory state of POC training | Yes | retrain via `run_poc_v2.py` (~1 min) |

## S3 backup (optional)

`model/ops/backup_db.py` pushes `data/rainham.db` to an S3 bucket with a
timestamp suffix. `restore_db.py` pulls the most recent snapshot back.

Required environment variables (configure once, e.g. in your shell rc or
`.envrc`):

```
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=...
RCC_S3_BUCKET=...
RCC_S3_PREFIX=play-cricket/db/   # optional, defaults to db/
```

The credentials are NEVER committed. The bucket name + prefix CAN be
committed (they're not secret), or kept as env vars if preferred.

## Supabase Postgres (planned, not yet wired)

A Supabase Postgres instance has been provisioned for hosting a
public-facing slim copy of the dataset (skill snapshots, match summaries,
league/division metadata — NOT the full ~10GB ball-by-ball corpus, which
exceeds the free-tier 500 MB cap).

```
host:     db.pfmajgetbhdcerkqswkc.supabase.co
port:     5432
database: postgres
user:     postgres
```

Connection string (set the password as an env var; never commit):

```
postgresql://postgres:${SUPABASE_DB_PASSWORD}@db.pfmajgetbhdcerkqswkc.supabase.co:5432/postgres
```

Or set `SUPABASE_DB_URL` env var with the full URL including the password.

Optional Supabase agent skills helper (run once locally):

```
npx skills add supabase/agent-skills
```

What we'll push to Supabase eventually (slim model output, not raw JSON):

- `player_skill_snapshot` table (~1M rows, tiny)
- `matches` slim view (no ball-level data)
- `ball_features` aggregated to (match, ball-position-bucket) — not raw

## Watching repo size

GitHub's soft cap is ~5 GB; hard cap ~10 GB. The committed raw JSON
will grow:

- after 4 leagues: ~1.5 GB
- after all 33 leagues: ~30 GB (estimated)

Once it crosses ~3 GB, plan to migrate the bulkiest JSONs (the per-ball
files) to Git LFS or an external blob store. Until then, regular `git
push` works fine.

## Rebuild times (rough)

- `build_db.py` on the current ~12k matches: ~5 min
- `build_db.py` on full 50k+ matches: ~30 min
- `run_all.py` on a fully-cached repo: ~5 min (just verifies)
- `run_all.py` from scratch: 5-10 hours (API-bound)
- `build_snapshot.py`: ~1 min
- `run_poc_v2.py`: ~1 min training
