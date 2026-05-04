#!/usr/bin/env python3
"""
Top run scorers across all formats / teams for Rainham CC.

Reads stats/data/rainham.db and writes
stats/reports/ad-hoc/top_run_scorers.md (and refreshes the top-level
reports/index.html).

Definitions (also documented in PLAN.md):
  innings   = batting rows where how_out != 'did not bat' AND how_out != 'absent'
  not_outs  = how_out IN ('not out', 'retired not out')
  runs      = SUM(batting.runs) over those rows
  HS        = highest single-innings score (with '*' if it was a not-out)
  average   = runs / (innings - not_outs)  ('inf' if no dismissals)
  strike_rt = 100 * runs / balls (NULL if balls is 0/missing)
  matches   = COUNT(DISTINCT match_id) where the batsman has a Rainham batting row
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "rainham.db"
OUT_MD = ROOT / "reports" / "ad-hoc" / "top_run_scorers.md"

NOT_OUT = ("not out", "retired not out")
DID_NOT_BAT = ("did not bat", "absent")

QUERY_RANK = """
WITH rainham_bat AS (
    SELECT b.*
    FROM batting b
    WHERE b.team_batting_club_id = '5251'
      AND b.batsman_id IS NOT NULL
      AND lower(coalesce(b.how_out, '')) NOT IN ('did not bat', 'absent')
),
canonical_name AS (
    -- Most-recently-played name for each batsman_id
    SELECT batsman_id,
           batsman_name
    FROM (
        SELECT b.batsman_id,
               b.batsman_name,
               m.match_date,
               m.season,
               b.match_id,
               ROW_NUMBER() OVER (
                   PARTITION BY b.batsman_id
                   ORDER BY m.season DESC, m.match_date DESC, b.match_id DESC
               ) AS rn
        FROM rainham_bat b
        JOIN matches m USING (match_id)
        WHERE b.batsman_name <> ''
    ) t
    WHERE rn = 1
),
agg AS (
    SELECT
        rb.batsman_id,
        SUM(coalesce(rb.runs, 0))                                  AS runs,
        COUNT(*)                                                   AS innings,
        SUM(CASE WHEN lower(coalesce(rb.how_out,''))
                      IN ('not out', 'retired not out') THEN 1 ELSE 0 END) AS not_outs,
        SUM(coalesce(rb.balls, 0))                                 AS balls,
        SUM(CASE WHEN coalesce(rb.balls,0) > 0 THEN rb.balls ELSE 0 END)
            AS balls_with_data,
        SUM(CASE WHEN coalesce(rb.balls,0) > 0 THEN coalesce(rb.runs,0) ELSE 0 END)
            AS runs_when_balls_known,
        SUM(coalesce(rb.fours, 0))                                 AS fours,
        SUM(coalesce(rb.sixes, 0))                                 AS sixes,
        MAX(rb.runs)                                               AS hs_runs,
        COUNT(DISTINCT rb.match_id)                                AS matches
    FROM rainham_bat rb
    GROUP BY rb.batsman_id
),
hs AS (
    -- Resolve whether the highest score was a not out
    SELECT a.batsman_id,
           a.hs_runs,
           MAX(CASE WHEN lower(coalesce(rb.how_out,''))
                       IN ('not out', 'retired not out') THEN 1 ELSE 0 END) AS hs_not_out
    FROM agg a
    JOIN rainham_bat rb
      ON rb.batsman_id = a.batsman_id
     AND rb.runs = a.hs_runs
    GROUP BY a.batsman_id, a.hs_runs
),
fifties AS (
    SELECT batsman_id,
           SUM(CASE WHEN runs >= 50 AND runs < 100 THEN 1 ELSE 0 END) AS fifties,
           SUM(CASE WHEN runs >= 100 THEN 1 ELSE 0 END)               AS hundreds
    FROM rainham_bat
    GROUP BY batsman_id
)
SELECT
    a.batsman_id,
    cn.batsman_name AS name,
    a.matches,
    a.innings,
    a.not_outs,
    a.runs,
    hs.hs_runs,
    hs.hs_not_out,
    a.balls,
    a.balls_with_data,
    a.runs_when_balls_known,
    a.fours,
    a.sixes,
    f.fifties,
    f.hundreds
FROM agg a
JOIN canonical_name cn USING (batsman_id)
JOIN hs USING (batsman_id)
JOIN fifties f USING (batsman_id)
ORDER BY a.runs DESC, a.innings ASC
LIMIT 25;
"""


def fmt_avg(runs: int, innings: int, not_outs: int) -> str:
    dismissals = innings - not_outs
    if dismissals <= 0:
        return "—"
    return f"{runs / dismissals:.2f}"


def fmt_sr(runs_when_balls_known: int, balls_with_data: int) -> str:
    if not balls_with_data:
        return "—"
    return f"{100.0 * runs_when_balls_known / balls_with_data:.1f}"


def fmt_hs(hs_runs: int, hs_not_out: int) -> str:
    if hs_runs is None:
        return "—"
    return f"{hs_runs}{'*' if hs_not_out else ''}"


def main() -> int:
    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    cur.execute("""SELECT MIN(season), MAX(season) FROM matches
                   WHERE home_club_id='5251' OR away_club_id='5251'""")
    min_season, max_season = cur.fetchone()
    cur.execute("""SELECT COUNT(*) FROM matches
                   WHERE home_club_id='5251' OR away_club_id='5251'""")
    rh_match_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM batting WHERE team_batting_club_id='5251'")
    rh_bat_rows = cur.fetchone()[0]

    rows = list(cur.execute(QUERY_RANK))

    md = []
    md.append("# Rainham CC — Top 25 Run Scorers (All Formats / All Teams)")
    md.append("")
    md.append(
        f"_Source: `data/rainham.db` (built from `data/raw/match_detail/*.json`).  "
        f"Rainham matches in cache: **{rh_match_count}** across seasons "
        f"**{min_season}–{max_season}**.  Rainham batting rows: **{rh_bat_rows:,}**._"
    )
    md.append("")
    md.append(
        "Innings counted when `how_out` is anything other than _did not bat_ / _absent_. "
        "Not-outs are `not out` and `retired not out`. Averages use "
        "`runs / (innings − not_outs)`. `HS*` denotes the highest score was a not-out."
    )
    md.append("")
    md.append(
        "| # | Player | M | I | NO | Runs | HS | Avg | SR | 4s | 6s | 50s | 100s |"
    )
    md.append(
        "|--:|--------|--:|--:|--:|-----:|---:|----:|---:|---:|---:|----:|-----:|"
    )

    print(f"Top 10 run scorers — Rainham CC (all formats, all teams)")
    print(f"  matches in cache: {rh_match_count}, seasons: {min_season}-{max_season}")
    print()
    print(f"{'#':>2} {'Player':<24} {'M':>4} {'I':>4} {'NO':>3} "
          f"{'Runs':>6} {'HS':>6} {'Avg':>7} {'SR':>6} {'4s':>4} {'6s':>4} "
          f"{'50s':>4} {'100s':>5}")

    for i, r in enumerate(rows, start=1):
        (batsman_id, name, matches, innings, not_outs, runs,
         hs_runs, hs_not_out, balls, balls_with_data, runs_when_balls_known,
         fours, sixes, fifties, hundreds) = r
        avg = fmt_avg(runs, innings, not_outs)
        sr = fmt_sr(runs_when_balls_known, balls_with_data)
        hs = fmt_hs(hs_runs, hs_not_out)
        if i <= 10:
            print(f"{i:>2} {name[:24]:<24} {matches:>4} {innings:>4} {not_outs:>3} "
                  f"{runs:>6} {hs:>6} {avg:>7} {sr:>6} {fours:>4} {sixes:>4} "
                  f"{fifties:>4} {hundreds:>5}")
        marker = "" if i > 10 else ""
        md.append(
            f"| {i} | {name} | {matches} | {innings} | {not_outs} | "
            f"**{runs}** | {hs} | {avg} | {sr} | {fours} | {sixes} | "
            f"{fifties} | {hundreds} |"
        )

    md.append("")
    md.append("> Top 10 are rows 1–10. The next 15 are included for context.")
    md.append("")
    md.append("## Definitions")
    md.append("")
    md.append("- **Rainham match** = a match where `home_club_id = 5251` or "
              "`away_club_id = 5251` (Rainham CC, Essex).")
    md.append("- **Rainham batting row** = a `batting` row whose `team_batting_id` "
              "is one of the Rainham team IDs in that match.")
    md.append("- **Player aggregation key** = `batsman_id` from the Play-Cricket API. "
              "Stable across teams and seasons. Display name is the most-recent "
              "non-empty `batsman_name` for that ID.")
    md.append("")
    md.append("## Caveats")
    md.append("")
    md.append("- Pre-2005 coverage is sparse on Play-Cricket; results before then "
              "rely on whatever has been retrospectively entered.")
    md.append("- A small number of pairs/junior matches record `pairs inning` as "
              "the dismissal mode; these are counted as innings batted with the "
              "scored runs, with no not-out flag.")
    md.append("- If a player ever appeared as a Rainham team-mate but never had "
              "a batting row (e.g. only fielded/bowled), they will not appear here. "
              "See `match_players` table for the fuller appearance list.")
    md.append("")

    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text("\n".join(md))
    print()
    print(f"Wrote {OUT_MD}")
    try:
        import build_index
        build_index.build()
    except Exception as e:
        print(f"index refresh skipped: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
