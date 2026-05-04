#!/usr/bin/env python3
"""
Side-by-side: Jon O'Neill (batsman_id 20972) vs Raj Hothi (batsman_id 5572853).

Scope: League + Cup matches only (Friendlies excluded). All formats / all
Rainham teams within that scope. Reads stats/data/rainham.db; writes
stats/reports/ad-hoc/oneill_vs_hothi.md (and refreshes the top-level
reports/index.html).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "rainham.db"
OUT_MD = ROOT / "reports" / "ad-hoc" / "oneill_vs_hothi.md"

PLAYERS = [
    (20972,  "Jon O'Neill"),
    (5572853, "Raj Hothi"),
]
COMP_FILTER = "m.competition_type IN ('League','Cup')"


# ---------------------------------------------------------------- helpers ---

def overs_to_balls(s: str | None) -> int:
    """Convert cricket overs (e.g. '9.3' = 9 overs and 3 balls) to total balls."""
    if not s:
        return 0
    s = s.strip()
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


def balls_to_overs(balls: int) -> str:
    full, rem = divmod(balls, 6)
    return f"{full}.{rem}"


def fmt(v, dp=2):
    if v is None or v == "":
        return "—"
    if isinstance(v, float):
        return f"{v:.{dp}f}"
    return str(v)


def winner_marker(a, b, higher_is_better=True):
    """Return a tuple (a_str, b_str) with a ⬆ on whichever wins."""
    if a is None or b is None or a == b:
        return ("", "")
    a_wins = (a > b) if higher_is_better else (a < b)
    return ("⬆", "") if a_wins else ("", "⬆")


# ---------------------------------------------------------------- queries ---

def batting_stats(conn: sqlite3.Connection, pid: int) -> dict:
    cur = conn.cursor()
    row = cur.execute(f"""
        SELECT
          COUNT(DISTINCT b.match_id)                              AS matches,
          COUNT(*)                                                AS innings,
          SUM(CASE WHEN lower(coalesce(b.how_out,''))
                        IN ('not out','retired not out') THEN 1 ELSE 0 END) AS not_outs,
          SUM(coalesce(b.runs,0))                                 AS runs,
          MAX(b.runs)                                             AS hs,
          SUM(coalesce(b.balls,0))                                AS balls_total,
          SUM(CASE WHEN coalesce(b.balls,0)>0 THEN b.balls ELSE 0 END) AS balls_known,
          SUM(CASE WHEN coalesce(b.balls,0)>0 THEN coalesce(b.runs,0) ELSE 0 END)
                                                                  AS runs_when_balls,
          SUM(coalesce(b.fours,0))                                AS fours,
          SUM(coalesce(b.sixes,0))                                AS sixes,
          SUM(CASE WHEN b.runs>=50 AND b.runs<100 THEN 1 ELSE 0 END) AS fifties,
          SUM(CASE WHEN b.runs>=100               THEN 1 ELSE 0 END) AS hundreds,
          SUM(CASE WHEN b.runs=0 AND lower(coalesce(b.how_out,''))
                        IN ('ct','b','lbw','run out','st','hit wicket',
                            'retired out','pairs inning')
                   THEN 1 ELSE 0 END)                             AS ducks
        FROM batting b
        JOIN matches m USING(match_id)
        WHERE b.batsman_id = ?
          AND b.team_batting_club_id = '5251'
          AND {COMP_FILTER}
          AND lower(coalesce(b.how_out,'')) NOT IN ('did not bat','absent')
    """, (pid,)).fetchone()
    keys = ("matches","innings","not_outs","runs","hs","balls_total",
            "balls_known","runs_when_balls","fours","sixes","fifties",
            "hundreds","ducks")
    d = dict(zip(keys, row))

    # Was the HS a not-out?
    hs_no = cur.execute(f"""
        SELECT MAX(CASE WHEN lower(coalesce(b.how_out,''))
                            IN ('not out','retired not out') THEN 1 ELSE 0 END)
        FROM batting b JOIN matches m USING(match_id)
        WHERE b.batsman_id=? AND b.team_batting_club_id='5251' AND {COMP_FILTER}
          AND b.runs = ?
    """, (pid, d["hs"])).fetchone()[0] if d["hs"] is not None else 0
    d["hs_not_out"] = hs_no or 0

    # Career span
    span = cur.execute(f"""
        SELECT MIN(m.season), MAX(m.season)
        FROM batting b JOIN matches m USING(match_id)
        WHERE b.batsman_id=? AND b.team_batting_club_id='5251' AND {COMP_FILTER}
          AND lower(coalesce(b.how_out,'')) NOT IN ('did not bat','absent')
    """, (pid,)).fetchone()
    d["first_season"], d["last_season"] = span
    return d


def bowling_stats(conn: sqlite3.Connection, pid: int) -> dict:
    cur = conn.cursor()
    rows = cur.execute(f"""
        SELECT bo.match_id, bo.innings_seq, bo.overs, bo.maidens, bo.runs,
               bo.wides, bo.no_balls, bo.wickets
        FROM bowling bo JOIN matches m USING(match_id)
        WHERE bo.bowler_id=? AND bo.team_bowling_club_id='5251' AND {COMP_FILTER}
    """, (pid,)).fetchall()

    balls = 0
    runs_conceded = 0
    wickets = 0
    maidens = 0
    matches = set()
    innings_bowled = 0
    fivers = 0
    fourers = 0
    best = None  # (wickets desc, runs asc) tuple
    for mid, seq, overs, m_, r, w_, nb, wkts in rows:
        b = overs_to_balls(overs)
        if b == 0 and (r or 0) == 0 and (wkts or 0) == 0 and (m_ or 0) == 0:
            continue  # blank row
        balls += b
        runs_conceded += int(r or 0)
        wickets += int(wkts or 0)
        maidens += int(m_ or 0)
        matches.add(mid)
        innings_bowled += 1
        if (wkts or 0) >= 5:
            fivers += 1
        elif (wkts or 0) == 4:
            fourers += 1
        candidate = (int(wkts or 0), -int(r or 0), b)
        if best is None or candidate > best:
            best = candidate

    avg = (runs_conceded / wickets) if wickets else None
    econ = (runs_conceded / (balls/6)) if balls else None
    sr = (balls / wickets) if wickets else None
    bb = None
    if best is not None:
        bb = f"{best[0]}/{-best[1]}"

    return {
        "matches_bowled": len(matches),
        "innings_bowled": innings_bowled,
        "balls": balls,
        "overs_str": balls_to_overs(balls),
        "maidens": maidens,
        "runs_conceded": runs_conceded,
        "wickets": wickets,
        "avg": avg,
        "econ": econ,
        "sr": sr,
        "bb": bb,
        "fivers": fivers,
        "fourers": fourers,
    }


def fielding_stats(conn: sqlite3.Connection, pid: int) -> dict:
    cur = conn.cursor()
    catches = cur.execute(f"""
        SELECT COUNT(*) FROM batting b JOIN matches m USING(match_id)
        WHERE b.fielder_id=? AND b.team_batting_club_id<>'5251'
          AND lower(b.how_out)='ct' AND {COMP_FILTER}
    """, (pid,)).fetchone()[0]
    stumpings = cur.execute(f"""
        SELECT COUNT(*) FROM batting b JOIN matches m USING(match_id)
        WHERE b.fielder_id=? AND b.team_batting_club_id<>'5251'
          AND lower(b.how_out)='st' AND {COMP_FILTER}
    """, (pid,)).fetchone()[0]
    runouts = cur.execute(f"""
        SELECT COUNT(*) FROM batting b JOIN matches m USING(match_id)
        WHERE b.fielder_id=? AND b.team_batting_club_id<>'5251'
          AND lower(b.how_out)='run out' AND {COMP_FILTER}
    """, (pid,)).fetchone()[0]
    return {"catches": catches, "stumpings": stumpings, "runouts": runouts}


def head_to_head(conn: sqlite3.Connection) -> dict:
    cur = conn.cursor()
    matches_together = cur.execute(f"""
        SELECT COUNT(DISTINCT a.match_id)
        FROM match_players a
        JOIN match_players b ON a.match_id=b.match_id
        JOIN matches m       ON a.match_id=m.match_id
        WHERE a.player_id=? AND b.player_id=? AND {COMP_FILTER}
    """, (20972, 5572853)).fetchone()[0]
    return {"shared_matches": matches_together}


# ---------------------------------------------------------------- main ------

def main() -> int:
    conn = sqlite3.connect(DB)

    bat = {}
    bowl = {}
    field = {}
    for pid, _name in PLAYERS:
        bat[pid] = batting_stats(conn, pid)
        bowl[pid] = bowling_stats(conn, pid)
        field[pid] = fielding_stats(conn, pid)
    h2h = head_to_head(conn)

    pid_a, name_a = PLAYERS[0]
    pid_b, name_b = PLAYERS[1]
    a_bat, b_bat = bat[pid_a], bat[pid_b]
    a_bowl, b_bowl = bowl[pid_a], bowl[pid_b]
    a_fld, b_fld = field[pid_a], field[pid_b]

    md: list[str] = []
    md.append(f"# {name_a} vs {name_b} — head-to-head (League + Cup)")
    md.append("")
    md.append(
        f"_Source: `data/rainham.db`. Filter: `competition_type IN ('League','Cup')` — "
        f"friendlies excluded. {h2h['shared_matches']} matches in the cache "
        f"feature **both** in the squad._"
    )
    md.append("")
    md.append("Markers: ⬆ = better on this metric (higher is better unless noted).")
    md.append("")

    def section(title: str):
        md.append(f"## {title}")
        md.append("")
        md.append(f"| Metric | {name_a} | | {name_b} | |")
        md.append("|--------|---------:|:-:|---------:|:-:|")

    def row(label, a, b, higher=True, fmt_a=None, fmt_b=None, dp=2):
        # comparison key
        ka = a; kb = b
        if isinstance(a, str) or isinstance(b, str):
            ka, kb = None, None  # strings: don't auto-mark winner
        ma, mb = winner_marker(ka, kb, higher_is_better=higher)
        a_disp = fmt_a if fmt_a is not None else fmt(a, dp)
        b_disp = fmt_b if fmt_b is not None else fmt(b, dp)
        md.append(f"| {label} | **{a_disp}** | {ma} | **{b_disp}** | {mb} |")

    # -------- Career span -------------------------------------------------
    section("Career span (League + Cup)")
    row("First season", a_bat["first_season"], b_bat["first_season"], higher=False)
    row("Last season",  a_bat["last_season"],  b_bat["last_season"], higher=True)
    seasons_a = (a_bat["last_season"] - a_bat["first_season"] + 1) \
        if a_bat["first_season"] else None
    seasons_b = (b_bat["last_season"] - b_bat["first_season"] + 1) \
        if b_bat["first_season"] else None
    row("Span (seasons)", seasons_a, seasons_b)
    md.append("")

    # -------- Batting -----------------------------------------------------
    section("Batting")
    row("Matches",        a_bat["matches"],   b_bat["matches"])
    row("Innings",        a_bat["innings"],   b_bat["innings"])
    row("Not outs",       a_bat["not_outs"],  b_bat["not_outs"])
    row("Runs",           a_bat["runs"],      b_bat["runs"])
    a_hs = f"{a_bat['hs']}{'*' if a_bat['hs_not_out'] else ''}"
    b_hs = f"{b_bat['hs']}{'*' if b_bat['hs_not_out'] else ''}"
    row("Highest score",  a_bat["hs"] or 0, b_bat["hs"] or 0, fmt_a=a_hs, fmt_b=b_hs)
    avg_a = (a_bat["runs"] / (a_bat["innings"] - a_bat["not_outs"])
             if (a_bat["innings"] - a_bat["not_outs"]) else None)
    avg_b = (b_bat["runs"] / (b_bat["innings"] - b_bat["not_outs"])
             if (b_bat["innings"] - b_bat["not_outs"]) else None)
    row("Average",        avg_a, avg_b)
    sr_a = (100*a_bat["runs_when_balls"]/a_bat["balls_known"]
            if a_bat["balls_known"] else None)
    sr_b = (100*b_bat["runs_when_balls"]/b_bat["balls_known"]
            if b_bat["balls_known"] else None)
    row("Strike rate (where balls recorded)", sr_a, sr_b)
    row("Balls faced (recorded)", a_bat["balls_known"], b_bat["balls_known"])
    row("50s",            a_bat["fifties"],  b_bat["fifties"])
    row("100s",           a_bat["hundreds"], b_bat["hundreds"])
    row("50+ scores",     a_bat["fifties"]+a_bat["hundreds"],
                          b_bat["fifties"]+b_bat["hundreds"])
    row("Fours",          a_bat["fours"],    b_bat["fours"])
    row("Sixes",          a_bat["sixes"],    b_bat["sixes"])
    row("Ducks (out for 0)", a_bat["ducks"], b_bat["ducks"], higher=False)
    md.append("")

    # -------- Bowling -----------------------------------------------------
    section("Bowling")
    row("Innings bowled", a_bowl["innings_bowled"], b_bowl["innings_bowled"])
    row("Overs",          a_bowl["balls"]/6 if a_bowl["balls"] else 0,
                          b_bowl["balls"]/6 if b_bowl["balls"] else 0,
                          fmt_a=a_bowl["overs_str"], fmt_b=b_bowl["overs_str"])
    row("Maidens",        a_bowl["maidens"], b_bowl["maidens"])
    row("Runs conceded",  a_bowl["runs_conceded"], b_bowl["runs_conceded"], higher=False)
    row("Wickets",        a_bowl["wickets"], b_bowl["wickets"])
    row("Bowling avg",    a_bowl["avg"], b_bowl["avg"], higher=False)
    row("Economy",        a_bowl["econ"], b_bowl["econ"], higher=False)
    row("Strike rate (balls/wkt)", a_bowl["sr"], b_bowl["sr"], higher=False)
    row("Best (wkts/runs)", a_bowl["bb"] or "—", b_bowl["bb"] or "—",
        fmt_a=a_bowl["bb"] or "—", fmt_b=b_bowl["bb"] or "—")
    row("5-wicket hauls", a_bowl["fivers"],  b_bowl["fivers"])
    row("4-wicket hauls", a_bowl["fourers"], b_bowl["fourers"])
    md.append("")

    # -------- Fielding ----------------------------------------------------
    section("Fielding (vs opposition batters)")
    row("Catches",    a_fld["catches"],   b_fld["catches"])
    row("Stumpings",  a_fld["stumpings"], b_fld["stumpings"])
    row("Run outs (credited as fielder)", a_fld["runouts"], b_fld["runouts"])
    md.append("")

    # ----------- Verdict --------------------------------------------------
    md.append("## The brutal verdict")
    md.append("")
    a_wins = sum(1 for line in md if "| ⬆ |" in line and line.startswith(f"| ") and line.split("|")[3].strip() == "⬆")
    # Easier: just recount from data we computed.
    cmp_pairs = [
        ("Runs",          a_bat["runs"], b_bat["runs"], True),
        ("Average",       avg_a, avg_b, True),
        ("Strike rate",   sr_a, sr_b, True),
        ("100s",          a_bat["hundreds"], b_bat["hundreds"], True),
        ("50+ scores",    a_bat["fifties"]+a_bat["hundreds"],
                          b_bat["fifties"]+b_bat["hundreds"], True),
        ("HS",            a_bat["hs"] or 0, b_bat["hs"] or 0, True),
        ("Fewer ducks",   a_bat["ducks"], b_bat["ducks"], False),
        ("Wickets",       a_bowl["wickets"], b_bowl["wickets"], True),
        ("Bowling avg",   a_bowl["avg"] or 9999, b_bowl["avg"] or 9999, False),
        ("Economy",       a_bowl["econ"] or 9999, b_bowl["econ"] or 9999, False),
        ("5wi",           a_bowl["fivers"], b_bowl["fivers"], True),
        ("Catches",       a_fld["catches"], b_fld["catches"], True),
    ]
    a_score = b_score = 0
    md.append("| Metric | Winner |")
    md.append("|--------|--------|")
    for label, av, bv, higher in cmp_pairs:
        if av is None or bv is None or av == bv:
            md.append(f"| {label} | tie |"); continue
        a_better = (av > bv) if higher else (av < bv)
        if a_better:
            a_score += 1; md.append(f"| {label} | {name_a} |")
        else:
            b_score += 1; md.append(f"| {label} | {name_b} |")
    md.append("")
    md.append(f"**Tally: {name_a} {a_score} — {b_score} {name_b}**")
    md.append("")

    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text("\n".join(md))
    try:
        import build_index
        build_index.build()
    except Exception as e:
        print(f"index refresh skipped: {e}")

    # console summary
    print(f"{name_a} vs {name_b} (League + Cup)")
    print(f"  shared squads: {h2h['shared_matches']}")
    print()
    print(f"{'Metric':<24} {name_a:>16} {name_b:>16}")
    rows_console = [
        ("Career span", f"{a_bat['first_season']}–{a_bat['last_season']}",
                        f"{b_bat['first_season']}–{b_bat['last_season']}"),
        ("Matches", a_bat["matches"], b_bat["matches"]),
        ("Innings", a_bat["innings"], b_bat["innings"]),
        ("Runs", a_bat["runs"], b_bat["runs"]),
        ("HS", a_hs, b_hs),
        ("Average", f"{avg_a:.2f}" if avg_a else "—",
                    f"{avg_b:.2f}" if avg_b else "—"),
        ("Strike rate", f"{sr_a:.1f}" if sr_a else "—",
                        f"{sr_b:.1f}" if sr_b else "—"),
        ("50s", a_bat["fifties"], b_bat["fifties"]),
        ("100s", a_bat["hundreds"], b_bat["hundreds"]),
        ("4s / 6s", f"{a_bat['fours']}/{a_bat['sixes']}",
                    f"{b_bat['fours']}/{b_bat['sixes']}"),
        ("Ducks", a_bat["ducks"], b_bat["ducks"]),
        ("--- Bowling ---", "", ""),
        ("Overs", a_bowl["overs_str"], b_bowl["overs_str"]),
        ("Wickets", a_bowl["wickets"], b_bowl["wickets"]),
        ("Bowl avg",
         f"{a_bowl['avg']:.2f}" if a_bowl['avg'] else "—",
         f"{b_bowl['avg']:.2f}" if b_bowl['avg'] else "—"),
        ("Economy",
         f"{a_bowl['econ']:.2f}" if a_bowl['econ'] else "—",
         f"{b_bowl['econ']:.2f}" if b_bowl['econ'] else "—"),
        ("Best", a_bowl["bb"] or "—", b_bowl["bb"] or "—"),
        ("5wi / 4wi", f"{a_bowl['fivers']}/{a_bowl['fourers']}",
                       f"{b_bowl['fivers']}/{b_bowl['fourers']}"),
        ("--- Fielding ---", "", ""),
        ("Catches", a_fld["catches"], b_fld["catches"]),
        ("Stumpings", a_fld["stumpings"], b_fld["stumpings"]),
        ("Run outs", a_fld["runouts"], b_fld["runouts"]),
    ]
    for label, av, bv in rows_console:
        print(f"{label:<24} {str(av):>16} {str(bv):>16}")
    print()
    print(f"VERDICT: {name_a} {a_score} — {b_score} {name_b}")
    print(f"Wrote {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
