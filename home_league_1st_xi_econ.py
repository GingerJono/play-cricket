#!/usr/bin/env python3
"""
Home League 1st XI bowling — last 5 seasons, ordered by economy rate,
qualifying threshold 50+ overs.

Reads stats/data/rainham.db and writes
stats/reports/ad-hoc/home_league_1st_xi_econ.md (and refreshes the
top-level reports/index.html).

Filters:
  - team_bowling_club_id = 5251 (Rainham CC)
  - team_bowling_id      = 51207 (Rainham 1st XI)
  - matches.home_club_id = 5251 (home games only)
  - competition_type     = 'League' (no Cup, no friendlies)
  - season IN (this year .. this year - 4)  (i.e. the past 5 seasons)
  - bowler delivered >= 300 balls (50.0 overs)
  - bowler_id NOT NULL

Order: economy ascending (lower = better). Avg / SR shown as tiebreakers.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "rainham.db"
OUT_MD = ROOT / "reports" / "ad-hoc" / "home_league_1st_xi_econ.md"

RAINHAM_CLUB_ID = "5251"
RAINHAM_1ST_XI_TEAM_ID = "51207"
QUAL_BALLS = 300            # 50.0 overs
SEASONS_BACK = 5


def overs_to_balls(s) -> int:
    if not s:
        return 0
    s = str(s).strip()
    if not s:
        return 0
    if "." in s:
        whole, frac = s.split(".", 1)
    else:
        whole, frac = s, "0"
    try:
        return int(whole) * 6 + int(frac or 0)
    except ValueError:
        return 0


def balls_to_overs(b: int) -> str:
    if not b:
        return "0"
    full, rem = divmod(b, 6)
    return f"{full}.{rem}" if rem else str(full)


def main():
    today = dt.date.today()
    cur_season = today.year
    seasons = tuple(range(cur_season - SEASONS_BACK + 1, cur_season + 1))

    conn = sqlite3.connect(DB)
    rows = conn.execute(f"""
        SELECT bo.bowler_id, bo.bowler_name, bo.match_id, bo.overs,
               coalesce(bo.maidens,0), coalesce(bo.runs,0),
               coalesce(bo.wickets,0), m.season
        FROM bowling bo JOIN matches m USING(match_id)
        WHERE bo.team_bowling_club_id = ?
          AND bo.team_bowling_id = ?
          AND m.home_club_id = ?
          AND m.competition_type = 'League'
          AND m.season IN ({','.join('?' * len(seasons))})
          AND bo.bowler_id IS NOT NULL
    """, (RAINHAM_CLUB_ID, RAINHAM_1ST_XI_TEAM_ID, RAINHAM_CLUB_ID, *seasons)
    ).fetchall()

    # Aggregate per bowler. Keep canonical name = most-recent non-empty
    # bowler_name across the rows we see.
    agg = {}
    name_choice = {}
    for bid, name, mid, overs, maid, runs, wkts, season in rows:
        balls = overs_to_balls(overs)
        # Skip purely-zero rows (placeholder where the player was on the
        # card but didn't bowl).
        if balls == 0 and runs == 0 and wkts == 0 and maid == 0:
            continue
        a = agg.setdefault(bid, {
            "balls": 0, "maidens": 0, "runs": 0, "wickets": 0,
            "matches": set(), "fivers": 0, "fourers": 0,
            "best": None, "seasons": set(),
        })
        if name and name.strip():
            # Newer row wins as canonical name.
            name_choice.setdefault(bid, []).append((season, name))
        a["balls"] += balls
        a["maidens"] += maid
        a["runs"] += runs
        a["wickets"] += wkts
        a["matches"].add(mid)
        a["seasons"].add(season)
        if wkts >= 5:
            a["fivers"] += 1
        elif wkts == 4:
            a["fourers"] += 1
        cand = (wkts, -runs)
        if a["best"] is None or cand > a["best"]:
            a["best"] = cand

    rows_out = []
    for bid, a in agg.items():
        if a["balls"] < QUAL_BALLS:
            continue
        nm = name_choice.get(bid, [])
        nm.sort(reverse=True)
        name = nm[0][1] if nm else f"id {bid}"
        econ = (a["runs"] / (a["balls"] / 6)) if a["balls"] else None
        avg = (a["runs"] / a["wickets"]) if a["wickets"] else None
        sr = (a["balls"] / a["wickets"]) if a["wickets"] else None
        bb = f"{a['best'][0]}/{-a['best'][1]}" if a["best"] else "—"
        rows_out.append({
            "name": name,
            "matches": len(a["matches"]),
            "overs": balls_to_overs(a["balls"]),
            "balls": a["balls"],
            "maidens": a["maidens"],
            "runs": a["runs"],
            "wickets": a["wickets"],
            "avg": avg, "sr": sr, "econ": econ,
            "best": bb,
            "fivers": a["fivers"], "fourers": a["fourers"],
            "seasons": sorted(a["seasons"]),
        })

    rows_out.sort(key=lambda r: (
        r["econ"] if r["econ"] is not None else 9e9,
        r["avg"] if r["avg"] is not None else 9e9,
    ))

    md = []
    md.append("# Rainham 1st XI — home league bowling, ordered by economy")
    md.append("")
    md.append(f"_Past {SEASONS_BACK} seasons "
              f"({seasons[0]}–{seasons[-1]}); "
              f"Rainham 1st XI bowling at home in League fixtures only; "
              f"bowlers with **{QUAL_BALLS // 6}+ overs** delivered "
              f"qualify; ordered by economy (asc), then average (asc). "
              f"Generated {today.isoformat()}._")
    md.append("")
    md.append("| # | Bowler | M | Overs | Mdns | Runs | Wkts | Avg | SR | "
              "**Econ** | Best | 5wi | 4wi | Seasons |")
    md.append("|--:|--------|--:|------:|----:|----:|----:|----:|----:|"
              "--------:|------|----:|----:|---------|")
    for i, r in enumerate(rows_out, 1):
        avg_s = f"{r['avg']:.2f}" if r["avg"] is not None else "—"
        sr_s = f"{r['sr']:.1f}" if r["sr"] is not None else "—"
        econ_s = f"**{r['econ']:.2f}**" if r["econ"] is not None else "—"
        seasons_s = ", ".join(str(s) for s in r["seasons"])
        md.append(
            f"| {i} | {r['name']} | {r['matches']} | {r['overs']} | "
            f"{r['maidens']} | {r['runs']} | **{r['wickets']}** | "
            f"{avg_s} | {sr_s} | {econ_s} | {r['best']} | "
            f"{r['fivers']} | {r['fourers']} | {seasons_s} |"
        )
    md.append("")
    md.append(f"_{len(rows_out)} bowler"
              f"{'s' if len(rows_out) != 1 else ''} qualified._")
    md.append("")
    md.append("## Notes")
    md.append("")
    md.append(f"- **Scope.** Only Rainham 1st XI (`team_id = "
              f"{RAINHAM_1ST_XI_TEAM_ID}`) when fielding at home (Rainham as "
              f"`home_club_id`). League fixtures only — Cup and friendlies "
              f"excluded.")
    md.append(f"- **Qualification.** {QUAL_BALLS // 6} overs of total "
              f"deliveries across the window. Bowlers under that bar can "
              f"distort an economy table with one or two flat spells.")
    md.append("- **Economy** = `runs_conceded / (balls / 6)`. Maidens are "
              "carried for context but don't change the order.")
    md.append("- Avg = `runs / wickets`; SR = `balls / wickets`. Both blank "
              "for bowlers who never took a wicket at home in the window.")
    md.append("- The rebuild is deterministic — re-run after a fetch / DB "
              "rebuild to refresh.")
    md.append("")

    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text("\n".join(md))
    print(f"Wrote {OUT_MD}")
    print()
    for i, r in enumerate(rows_out, 1):
        econ_s = f"{r['econ']:.2f}" if r["econ"] is not None else "  -"
        avg_s = f"{r['avg']:.2f}" if r["avg"] is not None else "—"
        print(f"  {i:>2}. {r['name'][:24]:<24} "
              f"econ {econ_s}  ({r['overs']} overs, "
              f"{r['wickets']}w avg {avg_s})")

    try:
        import build_index
        build_index.build()
    except Exception as e:
        print(f"index refresh skipped: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
