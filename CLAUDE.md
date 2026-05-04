# `stats/` — Play-Cricket data + reports

This folder owns Rainham CC's **and any opposition's** Play-Cricket cache.
Built from `php/epizy/cron_api_matches_master.php` + `php/cron_api_matches_listener.php`,
which is the same API the live site uses (see `php/globals.php` for the
canonical token / site_id).

## When to read this file

- The user asks for any **stat about a Rainham player or match** (runs,
  wickets, streaks, comparisons, history).
- The user asks for an **opposition / scouting report**, or a comparison
  involving a non-Rainham club.
- The user asks for a **new report** that should be repeatable.

If the question is "ad-hoc, one-off SQL against the existing DB" → just
write the query against `stats/data/rainham.db`. If the answer needs the
DB to grow / be refreshed → see _Workflow_ below.

## What's in the cache

```
stats/
  fetch.py           pulls Play-Cricket JSON; --site-id and --division-ids
  build_db.py        loads cached JSON into SQLite (data/rainham.db)
  scout.py           generic opposition scouting report (any club)
                     emits both <slug>.md and a mobile-friendly <slug>.html
  top_run_scorers.py     Rainham-specific report
  streaks_and_fifties.py Rainham-specific report
  oneill_vs_hothi.py     example head-to-head
  PLAN.md            full schema + scoring rules + caveats
  data/
    README.md
    rainham.db       SQLite DB (committed)
    raw/
      matches/<site_id>/<season>.json   per-season fixture lists
      match_detail/<match_id>.json      full scorecards (shared)
      league_table/<division_id>.json   cached league tables (shared)
```

## Schema cheat sheet

The DB is **club-agnostic** — every batting / bowling row carries
`team_batting_club_id` / `team_bowling_club_id`, so any report can pivot
to any club just by changing the WHERE clause. There is no "is rainham"
column.

| Table | One row per | Key columns |
| --- | --- | --- |
| `clubs` | club seen in any match | `club_id` |
| `matches` | match | `match_id`, `home_club_id`, `away_club_id`, `home/away_team_id` |
| `match_players` | (match, side, player) | `match_id`, `team_side`, `club_id`, `player_id` |
| `innings` | (match, innings_seq) | `match_id`, `innings_seq`, `team_batting_club_id` |
| `batting` | (match, innings_seq, position) | + `team_batting_club_id` |
| `bowling` | (match, innings_seq, bowl_position) | + `team_bowling_club_id` |
| `fall_of_wickets` | (match, innings_seq, wicket) | |

> **Always use `innings_seq`, never `innings_number`.** The Play-Cricket
> API returns `innings_number = 1` for both sides of a limited-overs match
> in ~99% of fixtures. `innings_seq` is the array-position ordinal we
> assign at load time. See PLAN.md.

Common filters:

```sql
-- Rainham batting
WHERE team_batting_club_id = '5251'
-- Spartans batting
WHERE team_batting_club_id = '14366'
-- A specific player across all clubs they've played for
WHERE batsman_id = 20972
-- Played matches only (exclude future fixtures)
WHERE result <> ''   -- or join on a date <= today filter
-- Senior 1st-XI only
WHERE team_batting_id IN (SELECT team_id FROM ... WHERE team_name='1st XI' AND club_id='14366')
```

Innings-counting convention used throughout the reports:
- Excludes `how_out` IN (`did not bat`, `absent`).
- Not-outs = `not out` / `retired not out`.
- Duck = `runs = 0` AND `how_out` is one of the dismissal modes (NOT
  `not out`, `retired not out`, empty, `did not bat`, `absent`).

## Workflow for new questions

### Ad-hoc Rainham question
1. Open `stats/data/rainham.db` with `sqlite3` and answer.
2. If the question is interesting and likely to be re-asked, promote it
   into a new `stats/<name>.py` script + committed `<name>.md` report.

### "Top X" / streak / head-to-head report
Pattern: `top_run_scorers.py`, `streaks_and_fifties.py`, `oneill_vs_hothi.py`.
Read one of them as a template. Always:
- Filter by `team_batting_club_id` (or `team_bowling_club_id`).
- Use `innings_seq`, not `innings_number`.
- Excludes `did not bat` / `absent`.
- Pre-canonicalise player names from the most-recent non-empty
  `batsman_name` for each `batsman_id`.

### Scouting an opposition club

The output is dated and namespaced by club:
```
stats/reports/<YYYY-MM-DD>/<slug>/scout.md
stats/reports/<YYYY-MM-DD>/<slug>/scout.html  ← mobile-optimised
```

The HTML version is what the user shares — open on a phone, scroll-screenshot,
paste into WhatsApp.

The user will name a club ("Spartans", "Hornchurch", etc.). Steps:

1. **Find the club_id** in the existing DB:
   ```sql
   SELECT club_id, club_name FROM clubs WHERE club_name LIKE '%Spartan%';
   ```
   If the club is not yet in `clubs`, you almost certainly haven't
   fetched their site yet.

2. **Confirm site_id**. Most clubs use `site_id == club_id` on
   Play-Cricket, but verify with one curl call:
   ```bash
   curl -s "https://play-cricket.com/api/v2/matches.json?site_id=<id>&api_token=$TOKEN&season=2026" \
     | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('matches',[])))"
   ```

3. **Fetch their data + league tables**:
   ```bash
   python3 stats/fetch.py --site-id <id> --first-season 2018 --workers 12
   python3 stats/build_db.py
   # find division_ids the 1st XI played in
   sqlite3 stats/data/rainham.db "
     SELECT DISTINCT season, league_name, competition_id, competition_name
     FROM matches
     WHERE (home_team_id IN (<1st XI team_ids>) OR away_team_id IN (...))
       AND competition_type='League'
       AND season BETWEEN 2023 AND 2026
     ORDER BY season"
   # cache the league tables
   python3 stats/fetch.py --site-id <id> --first-season 2026 --last-season 2026 \
     --division-ids <id1>,<id2>,<id3>,<id4>
   ```
   Expect ~30 KB of JSON per match. The fetch is idempotent — only new
   matches will hit the API.

4. **Generate the scout report**:
   ```bash
   python3 stats/scout.py --club-name "Spartans" --season 2026
   ```
   Sections (1st XI only, League + Cup throughout unless noted):
   - **0. Current league table** — position, P, W, Pts + last-5 form pills
     (their row highlighted). Falls back to last completed season if the
     current is empty.
   - **1. Last 3 seasons finishing positions** — League games only
     (so P aligns with the published league table). Includes league
     name + division, finishing pos and W/L/D/NR.
   - **2/3. Top run scorers / wicket takers** across the last 3 seasons,
     1st XI L+C.
   - **4. Head-to-head vs Rainham 1st XI** in cache, with upcoming
     fixtures listed.
   - **5. Last 10 1st XI played matches** with innings scores and
     a Play-Cricket match link on each date.
   - **6. Patterns** — bar charts comparing them vs Rainham 1st XI:
     bat 1st vs 2nd, home vs away, when winning the toss; plus a
     per-season batting / bowling avg comparison.
   - **7. Web / video links** — manually curated (the API has no
     `video_url` field on matches).

5. Commit the new raw JSON + the rebuilt DB + the report.

### Mobile-friendly HTML for sharing

`scout.py` writes a `<slug>.html` next to the markdown. It's a single
self-contained file (inline CSS, no JS, no external assets) sized for a
~520 px viewport — open it on a phone, scroll to top, and use the
"long screenshot" / "scrolling capture" feature in the screenshot tool
to capture the whole report as a single image suitable for WhatsApp.

If you need to tweak the HTML look (fonts, table density, colour pills),
edit `render_html()` in `scout.py` — everything is one function.

### Adding a video link manually

If the user surfaces a YouTube link or NV Play stream, paste it into the
`data["video_links"]` block in `scout.py` for that specific club, then
re-run. The Play-Cricket API does **not** expose per-match video URLs,
so this is a manual list.

### Comparing players across clubs
Use `oneill_vs_hothi.py` as the template. The bow/bat queries already
take `team_batting_club_id` / `team_bowling_club_id`, so you can compare
any two players regardless of which clubs they play for.

## Cache refresh rhythm

- Run `python3 stats/fetch.py --only-recent 1 --force-seasons` at the
  start of any session that asks for a "current season" or "latest"
  question. This re-fetches just the most recent season summary, then
  pulls any new match details that have appeared.
- Run `python3 stats/build_db.py` after any fetch.
- Don't refetch the entire history — the cache is exhaustive.

## Validation discipline

Before quoting any headline number from a v1 query, sanity-check it
against the equivalent on `rainhamcc.play-cricket.com` (or the opposition
club's Play-Cricket page). The first build of this DB came in at ~50% of
the correct totals because of the `innings_number` bug. One reference
comparison would have caught it. Don't skip that step.

## Don't

- Don't add the API token to a new file unless you really need to. It's
  in `php/globals.php` as `pcapikey` and `fetch.py` reads it via
  `PC_API_TOKEN` env var (with `globals.php` fallback).
- Don't bypass the cache. If a stat needs a new field, **extend
  `build_db.py`** to populate it from the raw JSON, then rebuild — don't
  call the API from a report.
- Don't reintroduce `is_rainham_player` / `rainham_team_side`. The DB is
  club-agnostic.
- Don't use string-style season comparisons. `match_date` is `dd/mm/yyyy`;
  convert to `yyyymmdd` for ordering / filtering.
- Don't trust `result` alone — also check `result_applied_to` to know
  whose perspective the W/L is from. See `result_for(...)` in `scout.py`.
