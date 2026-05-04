# `stats/data/` — Rainham CC dataset

Source-controlled cache + database derived from the Play-Cricket public API.

## What lives here

| Path | What | Source of truth? |
| --- | --- | --- |
| `raw/matches/<site_id>/<season>.json` | Per-season fixture list (`matches.json`), one folder per `site_id` we've fetched | Yes (cache, can re-derive from API) |
| `raw/match_detail/<match_id>.json` | Full scorecard payload (`match_detail.json`) — shared across sites since match IDs are global | Yes (cache, can re-derive from API) |
| `rainham.db` | SQLite database, built from the JSON cache by `../build_db.py`. Despite the filename, it contains **all clubs we've fetched**, not just Rainham. | Derived |

The raw JSON files are committed so the database is fully reproducible without
hitting the API again. `rainham.db` is committed for convenience (so reports
can run with no toolchain beyond `sqlite3`).

## Source pipeline

```
Play-Cricket API
  matches.json?site_id=5251&season=YYYY  →  raw/matches/YYYY.json
  match_detail.json?match_id=N           →  raw/match_detail/N.json
                                              ↓ build_db.py
                                            rainham.db
                                              ↓ top_run_scorers.py
                                            ../top_run_scorers.md
```

API base: `https://play-cricket.com/api/v2/`
Auth: `api_token` query param (see `php/globals.php`).
Site ID: `5251` (Rainham CC, Essex).

## Schema (`rainham.db`)

### `matches` — one row per match
| Column | Type | Notes |
| --- | --- | --- |
| `match_id` (PK) | INT | Play-Cricket match id |
| `season` | INT | Year (1990–latest); back-filled from per-season file when missing on the detail payload |
| `match_date` | TEXT | `dd/mm/yyyy` |
| `match_time` | TEXT | `HH:MM` |
| `league_id`, `league_name`, `competition_id`, `competition_name`, `competition_type` | TEXT | Friendly / League / Cup etc. |
| `match_type` | TEXT | `Limited Overs` / `Multi-Day Match` / etc. |
| `game_type` | TEXT | `Standard` / `Pairs` / etc. |
| `ground_id`, `ground_name` | TEXT | |
| `home_*`, `away_*` (`club_id`, `club_name`, `team_id`, `team_name`) | TEXT | Both sides of the match |
| `toss_won_by_team_id`, `toss`, `batted_first` | TEXT | |
| `no_of_overs`, `no_of_innings`, `no_of_players` | INT | |
| `result`, `result_description`, `result_applied_to` | TEXT | |
| `rainham_team_side` (derived) | TEXT | `home` / `away` / `both` (intra-club) / `none` |

### `innings` — one row per (match, innings_seq)
Standard innings totals — runs, wickets, overs, balls, all extra categories,
declared / forfeited flags, revised target.

> ⚠️ **Why `innings_seq`?** The Play-Cricket API frequently sets
> `innings_number = 1` for **both** sides of a limited-overs match. Of the
> 2,254 cached matches with innings data, 2,252 had duplicate
> `innings_number` values. We therefore key all innings-level tables on
> `innings_seq` (1-based ordinal of the innings within the match payload)
> and keep `innings_number` as a non-key reference column.

### `batting` — one row per (match, innings, batting position)
| Column | Notes |
| --- | --- |
| `position` | Batting order (1 = opener) |
| `batsman_id`, `batsman_name` | Player IDs are stable across teams/seasons |
| `how_out`, `fielder_*`, `bowler_*` | Dismissal mode + agents |
| `runs`, `fours`, `sixes`, `balls` | `balls` is missing for many older / junior cards |
| `team_batting_id`, `team_batting_name` | The batsman's team in this innings |
| `is_rainham_player` (derived) | `1` if `team_batting_id` is one of the Rainham team IDs in that match |

### `bowling` — one row per (match, innings, bowling position)
Symmetric to `batting`. `team_bowling_*` is the team that's NOT batting in that
innings; `is_rainham_player` is derived the same way.

### `fall_of_wickets` — one row per (match, innings, wicket)

### `match_players` — one row per (match, team_side, player_id)
Lineups (incl. captain / wicket-keeper flags) regardless of whether the
player ended up batting.

## Allowed values

| Column | Values |
| --- | --- |
| `matches.rainham_team_side` | `home`, `away`, `both`, `none` |
| `match_players.team_side` | `home`, `away` |
| `batting.how_out` | `ct`, `b`, `lbw`, `run out`, `st`, `not out`, `retired not out`, `retired out`, `did not bat`, `absent`, `pairs inning`, `hit wicket`, `''` (unknown) |
| `is_rainham_player`, `captain`, `wicket_keeper`, `declared`, `forfeited_innings` | `0` / `1` |

## What is NOT in the cache

- League tables (`league_table.json`) — separate API endpoint, not used yet.
- News articles, kit orders, club admin docs — not derived from the API.
- Live in-flight matches mid-innings — we only see what's been published to
  Play-Cricket at fetch time. The current 2026 season is partial; refetching
  will fill it in.
- Matches before ~2005 are sparse on Play-Cricket; some have no innings data
  recorded at all (so they appear in `matches` but contribute zero rows to
  `batting` / `bowling` / `innings`).
- We do **not** reconcile player IDs across separate Play-Cricket
  registrations (e.g. junior + senior IDs for the same human).

## Refreshing the cache

```bash
# Refetch only the most recent season's summary (cheap, picks up new fixtures)
python3 stats/fetch.py --only-recent 1

# Refetch every match's detail unconditionally (slow ~2500 calls)
python3 stats/fetch.py --force-details

# Rebuild the SQLite DB from whatever's on disk
python3 stats/build_db.py

# Regenerate the top-scorers report
python3 stats/top_run_scorers.py
```

Set `PC_API_TOKEN` in the environment to override the default token from
`php/globals.php`.
