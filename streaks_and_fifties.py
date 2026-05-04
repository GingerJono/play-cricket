#!/usr/bin/env python3
"""
Three batting reports for Rainham CC, all from stats/data/rainham.db:

  1. Most 50+ scores (half-centuries + hundreds combined) by player.
  2. Longest streak of consecutive ducks (out for 0).
  3. Longest streak of consecutive scores in double digits or more (>= 10).

Streaks are computed in chronological order across all formats / teams,
ignoring 'did not bat' and 'absent' rows (they are not innings; they
neither extend nor break a streak).

Writes stats/streaks_and_fifties.md.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "rainham.db"
OUT_MD = ROOT / "streaks_and_fifties.md"

# Dismissal modes that count as "out" (i.e. would make a 0 a duck)
OUT_DISMISSALS = {
    "ct", "b", "lbw", "run out", "st", "hit wicket",
    "retired out", "pairs inning",
}
# Modes that mean the batsman did not get an innings — skip entirely
NON_INNINGS = {"did not bat", "absent"}
# Not-out modes — count as innings but never as a dismissal
NOT_OUT = {"not out", "retired not out"}


# ---------------------------------------------------------------- top 50+ ---

QUERY_FIFTIES = """
WITH rainham_bat AS (
    SELECT b.batsman_id, b.batsman_name, b.runs, b.how_out
    FROM batting b
    WHERE b.team_batting_club_id = '5251'
      AND b.batsman_id IS NOT NULL
      AND lower(coalesce(b.how_out, '')) NOT IN ('did not bat', 'absent')
),
canonical_name AS (
    SELECT batsman_id, batsman_name FROM (
        SELECT b.batsman_id, b.batsman_name,
               ROW_NUMBER() OVER (
                   PARTITION BY b.batsman_id
                   ORDER BY m.season DESC, m.match_date DESC, b.match_id DESC
               ) AS rn
        FROM batting b JOIN matches m USING(match_id)
        WHERE b.team_batting_club_id='5251' AND b.batsman_name<>''
    ) WHERE rn=1
)
SELECT
    rb.batsman_id,
    cn.batsman_name AS name,
    SUM(CASE WHEN rb.runs >= 50 AND rb.runs < 100 THEN 1 ELSE 0 END) AS fifties,
    SUM(CASE WHEN rb.runs >= 100             THEN 1 ELSE 0 END)      AS hundreds,
    SUM(CASE WHEN rb.runs >= 50              THEN 1 ELSE 0 END)      AS fifty_plus,
    MAX(rb.runs)                                                     AS hs,
    SUM(rb.runs)                                                     AS runs,
    COUNT(*)                                                         AS innings
FROM rainham_bat rb
JOIN canonical_name cn USING(batsman_id)
GROUP BY rb.batsman_id, cn.batsman_name
HAVING fifty_plus > 0
ORDER BY fifty_plus DESC, hundreds DESC, hs DESC
LIMIT 25;
"""


# ----------------------------------------------------- streak helpers -------

QUERY_INNINGS_BY_PLAYER = """
SELECT
    b.batsman_id,
    -- ISO date for ordering
    substr(m.match_date,7,4) || '-' || substr(m.match_date,4,2)
        || '-' || substr(m.match_date,1,2) AS iso_date,
    m.match_id,
    b.innings_seq,
    b.position,
    b.runs,
    lower(coalesce(b.how_out,'')) AS how_out_norm,
    m.season,
    m.match_date,
    m.competition_type,
    m.match_type,
    m.home_team_name,
    m.away_team_name,
    b.team_batting_name
FROM batting b
JOIN matches m USING(match_id)
WHERE b.team_batting_club_id = '5251'
  AND b.batsman_id IS NOT NULL
  AND b.runs IS NOT NULL          -- need a recorded score
  AND lower(coalesce(b.how_out,'')) NOT IN ('did not bat', 'absent')
ORDER BY b.batsman_id, iso_date, m.match_id, b.innings_seq, b.position;
"""


def canonical_name_map(conn: sqlite3.Connection) -> dict[int, str]:
    rows = conn.execute("""
        SELECT batsman_id, batsman_name FROM (
            SELECT b.batsman_id, b.batsman_name,
                   ROW_NUMBER() OVER (
                       PARTITION BY b.batsman_id
                       ORDER BY m.season DESC, m.match_date DESC, b.match_id DESC
                   ) AS rn
            FROM batting b JOIN matches m USING(match_id)
            WHERE b.team_batting_club_id='5251' AND b.batsman_name<>''
        ) WHERE rn=1
    """).fetchall()
    return {bid: name for bid, name in rows}


def is_duck(runs: int | None, how_out: str) -> bool:
    return runs == 0 and how_out in OUT_DISMISSALS


def is_double_digit(runs: int | None, how_out: str) -> bool:
    # Innings counts whether out or not out — the "score" is the score.
    return runs is not None and runs >= 10


def find_streaks(rows: list[tuple], predicate) -> tuple[int, list[tuple]]:
    """Return (longest_length, list_of_rows_in_longest_run)."""
    best_len = 0
    best_run: list[tuple] = []
    cur_run: list[tuple] = []
    for r in rows:
        runs, how_out = r["runs"], r["how_out_norm"]
        if predicate(runs, how_out):
            cur_run.append(r)
            if len(cur_run) > best_len:
                best_len = len(cur_run)
                best_run = list(cur_run)
        else:
            cur_run = []
    return best_len, best_run


# ----------------------------------------------------- main -----------------

def main() -> int:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    md: list[str] = []
    md.append("# Rainham CC — Top 50+ Scorers, Duck Streaks, 10+ Streaks")
    md.append("")
    md.append("_All formats / all teams. Source: `data/rainham.db`._")
    md.append("")
    md.append("Innings inclusion: rows where `how_out` is anything other than "
              "_did not bat_ / _absent_, with a recorded runs value. "
              "Streaks are taken in chronological order (`match_date`, then "
              "`match_id`, then `innings_seq`, then batting `position`).")
    md.append("")

    # ------------------------------------------------------ Most 50+ scores -
    md.append("## 1. Most 50+ scores (half-centuries + hundreds)")
    md.append("")
    md.append("Ranked by total 50+ scores; ties broken by hundreds, then highest score.")
    md.append("")
    md.append("| # | Player | 50+ | 50s | 100s | HS | Runs | Inns |")
    md.append("|--:|--------|--:|--:|--:|---:|-----:|-----:|")
    print("Top 25 — Most 50+ scores")
    print(f"{'#':>2} {'Player':<24} {'50+':>4} {'50s':>4} {'100s':>5} "
          f"{'HS':>4} {'Runs':>6} {'Inns':>5}")
    rows = list(cur.execute(QUERY_FIFTIES))
    for i, r in enumerate(rows, 1):
        bid, name, fifties, hundreds, fifty_plus, hs, runs, innings = r
        md.append(f"| {i} | {name} | **{fifty_plus}** | {fifties} | {hundreds} | "
                  f"{hs} | {runs} | {innings} |")
        if i <= 25:
            print(f"{i:>2} {name[:24]:<24} {fifty_plus:>4} {fifties:>4} "
                  f"{hundreds:>5} {hs:>4} {runs:>6} {innings:>5}")
    md.append("")

    # ------------------------------------------------------ Streaks ---------
    name_map = canonical_name_map(conn)
    rows = cur.execute(QUERY_INNINGS_BY_PLAYER).fetchall()

    by_player: dict[int, list] = {}
    for r in rows:
        by_player.setdefault(r["batsman_id"], []).append(r)

    duck_streaks: list[tuple[int, int, list]] = []   # (length, batsman_id, rows)
    dd_streaks: list[tuple[int, int, list]] = []
    for bid, prows in by_player.items():
        d_len, d_run = find_streaks(prows, is_duck)
        if d_len >= 2:
            duck_streaks.append((d_len, bid, d_run))
        dd_len, dd_run = find_streaks(prows, is_double_digit)
        if dd_len >= 2:
            dd_streaks.append((dd_len, bid, dd_run))

    duck_streaks.sort(key=lambda x: (-x[0], x[1]))
    dd_streaks.sort(key=lambda x: (-x[0], x[1]))

    def fmt_score(runs: int, how_out: str) -> str:
        return f"{runs}{'*' if how_out in NOT_OUT else ''}"

    def fmt_streak_table(title: str, streaks, top_n: int, score_label: str):
        md.append(f"## {title}")
        md.append("")
        md.append(f"Top {top_n}. _Score format_: `runs` (with `*` for not-out). "
                  "Innings between the first and last in the streak are listed "
                  "in chronological order.")
        md.append("")
        md.append("| # | Player | Streak | Span | Scores |")
        md.append("|--:|--------|------:|------|--------|")
        print()
        print(f"-- {title} --")
        for rank, (length, bid, srun) in enumerate(streaks[:top_n], 1):
            name = name_map.get(bid, f"player {bid}")
            first = srun[0]["match_date"]
            last = srun[-1]["match_date"]
            scores = " ".join(
                fmt_score(int(r["runs"]), r["how_out_norm"]) for r in srun
            )
            # Truncate if huge for the table
            scores_disp = scores if len(scores) < 240 else scores[:237] + "..."
            md.append(f"| {rank} | {name} | **{length}** | {first} → {last} | "
                      f"{scores_disp} |")
            if rank <= top_n:
                print(f"{rank:>2} {name[:24]:<24} len={length:>2} "
                      f"{first} -> {last}  scores: {scores[:80]}")
        md.append("")

    fmt_streak_table(
        "2. Longest streaks of consecutive ducks (out for 0)",
        duck_streaks, top_n=15, score_label="0",
    )
    md.append("Definition of a duck: batsman dismissed (`how_out` ∈ "
              "{ct, b, lbw, run out, st, hit wicket, retired out, pairs inning}) "
              "for 0 runs. Not-out 0s and unrecorded dismissals do **not** "
              "count as ducks but they **do** break the streak (only "
              "_did not bat_ / _absent_ are skipped).")
    md.append("")

    fmt_streak_table(
        "3. Longest streaks of consecutive 10+ scores",
        dd_streaks, top_n=15, score_label="10+",
    )
    md.append("Definition: an innings counts toward the streak when `runs >= 10`, "
              "regardless of how out. _Did not bat_ / _absent_ rows are skipped "
              "(they neither extend nor break the streak); any innings under 10 "
              "breaks it, including not-outs.")
    md.append("")

    OUT_MD.write_text("\n".join(md))
    print()
    print(f"Wrote {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
