#!/usr/bin/env python3
"""
Generic 1st-XI scouting report. League + Cup focus throughout.

Usage:
  python3 stats/scout.py --club-name "Spartans"
  python3 stats/scout.py --club-id 14366 --season 2026
  python3 stats/scout.py --club-name "Wickford" --vs-club-id 5251

Writes:
  stats/reports/<YYYY-MM-DD>/<slug>/scout.md
  stats/reports/<YYYY-MM-DD>/<slug>/scout.html

Sections (1st XI only, League + Cup unless otherwise noted):
  0. Current league table (with form)
  1. Last 3 seasons finishing positions (League only)
  2. Top run scorers (last 3 seasons)
  3. Top wicket takers (last 3 seasons)
  4. Head-to-head vs RCC 1st XI (any era in cache)
  5. Last 10 1st XI played matches (with scores + Play-Cricket links)
  6. Charts:
       - W/L/D when batting first vs second  (vs RCC 1st XI baseline)
       - W/L/D home vs away                  (vs RCC 1st XI baseline)
       - W/L/D when winning the toss
       - Team batting & bowling averages per season vs RCC
  7. Web links (Play-Cricket, club site, video search)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "rainham.db"
RAW_DIR = ROOT / "data" / "raw"
LEAGUE_TABLE_DIR = RAW_DIR / "league_table"
REPORTS_DIR = ROOT / "reports"

RAINHAM_CLUB_ID = "5251"
RAINHAM_FIRST_XI_TEAM_ID = "51207"   # Rainham CC, Essex - 1st XI
RAINHAM_NAME = "Rainham CC"

NOT_OUT = ("not out", "retired not out")
DID_NOT_BAT = ("did not bat", "absent")
OUT_DISMISSALS = ("ct", "b", "lbw", "run out", "st", "hit wicket",
                  "retired out", "pairs inning")


# ---------------------------------------------------------------- helpers ---

def slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")
    return s or "club"


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


def to_iso(date_str: str) -> str:
    if not date_str or len(date_str) < 10:
        return ""
    return date_str[6:10] + date_str[3:5] + date_str[0:2]


def fmt_score(runs, hs_no=False):
    if runs is None:
        return "—"
    return f"{runs}{'*' if hs_no else ''}"


def _esc(x) -> str:
    if x is None:
        return ""
    return (str(x).replace("&", "&amp;")
                  .replace("<", "&lt;")
                  .replace(">", "&gt;")
                  .replace('"', "&quot;"))


# ---------------------------------------------------------------- club / team --

def resolve_club(conn, name, club_id):
    cur = conn.cursor()
    if club_id:
        row = cur.execute("SELECT club_id, club_name FROM clubs WHERE club_id=?",
                          (str(club_id),)).fetchone()
        if not row:
            sys.exit(f"club_id {club_id} not in DB. Fetch first?")
        return row
    if not name:
        sys.exit("Pass --club-id or --club-name")
    rows = cur.execute(
        "SELECT club_id, club_name FROM clubs WHERE club_name LIKE ? ORDER BY club_name",
        (f"%{name}%",)).fetchall()
    if not rows:
        sys.exit(f"No club matches '{name}'")
    if len(rows) > 1:
        print("Multiple matches — pick one with --club-id:", file=sys.stderr)
        for cid, cn in rows:
            print(f"  {cid}: {cn}", file=sys.stderr)
        sys.exit(1)
    return rows[0]


def first_xi_team_ids(conn, club_id):
    """Pick the team_id(s) most likely to be the senior 'Saturday 1st XI'.

    Heuristic:
      1. exact 'Saturday 1st XI' (preferred — disambiguates from Sun)
      2. exact '1st XI'
      3. fallback: highest-count team whose name contains '1st XI' but
         isn't obviously a Sun/Junior/Womens variant.
    Returns a list (usually 1 element, sometimes 2 if the team_id was
    re-issued mid-history).
    """
    rows = conn.execute("""
        SELECT team_id, team_name, COUNT(*) c FROM (
          SELECT home_team_id team_id, home_team_name team_name FROM matches
            WHERE home_club_id=?
          UNION ALL
          SELECT away_team_id, away_team_name FROM matches
            WHERE away_club_id=?
        )
        WHERE team_id <> ''
        GROUP BY team_id, team_name
        ORDER BY c DESC
    """, (club_id, club_id)).fetchall()

    excluded_words = ("sunday", "sun ", "womens", "women", "under", "junior",
                       "u11", "u12", "u13", "u14", "u15", "u16", "u17", "u18",
                       "u19", "youth", "indoor", "midweek", "friendly",
                       "wildcats", "twenty20")

    def name_score(name):
        n = (name or "").strip().lower()
        if any(x in n for x in excluded_words):
            return -1
        if n == "saturday 1st xi":     return 100
        if n == "1st xi":              return 90
        if "1st xi" in n:              return 50
        if n == "saturday xi":         return 40
        if "saturday 1st" in n:        return 60
        return 0

    best = sorted(rows, key=lambda r: (name_score(r[1]), r[2]), reverse=True)
    if not best or name_score(best[0][1]) <= 0:
        # No 1st-XI label at all; fall back to highest-count senior-looking team
        return [best[0][0]] if best else []

    primary = best[0]
    # Include any same-named team_id (handles cases where the team_id was reissued)
    out = [r[0] for r in rows if r[1] == primary[1] and name_score(r[1]) >= 40]
    return out or [primary[0]]


def all_club_team_ids(conn, club_id):
    rows = conn.execute("""
        SELECT DISTINCT t FROM (
          SELECT home_team_id t FROM matches WHERE home_club_id=?
          UNION SELECT away_team_id FROM matches WHERE away_club_id=?
        ) WHERE t<>''""", (club_id, club_id)).fetchall()
    return {r[0] for r in rows}


# ---------------------------------------------------------------- result helpers

def result_for(res, result_applied_to, club_team_ids):
    """Return W / L / D / T / A / NR from this club's perspective.

    Play-Cricket also returns 'CON' (conceded / walkover) — we treat
    that as a W for the team it's applied to and L for the other team.
    """
    if res in ("D", "A", "T", ""):
        return res or "NR"
    rat = result_applied_to or ""
    # Normalise result codes
    if res == "CON":
        res = "W"
    if rat in club_team_ids:
        return "W" if res == "W" else "L"
    if rat:
        return "L" if res == "W" else "W"
    return "NR"


def compute_wld(rows, club_team_ids):
    counts = {"W": 0, "L": 0, "D": 0, "T": 0, "A": 0, "NR": 0}
    for r in rows:
        out = result_for(r["result"], r["result_applied_to"], club_team_ids)
        counts[out] = counts.get(out, 0) + 1
    counts["P"] = sum(counts.get(k, 0) for k in ("W","L","D","T","A","NR"))
    return counts


# ---------------------------------------------------------------- queries --

def fetch_1stxi_matches(conn, team_ids, comp_types=("League","Cup"),
                        cutoff_iso=None, season_min=None):
    if not team_ids:
        return []
    sql = """
        SELECT match_id, season, match_date,
               home_club_id, home_club_name, home_team_id, home_team_name,
               away_club_id, away_club_name, away_team_id, away_team_name,
               toss_won_by_team_id, toss, batted_first,
               competition_type, match_type,
               result, result_applied_to, result_description
        FROM matches
        WHERE (home_team_id IN ({tids}) OR away_team_id IN ({tids}))
          AND match_date <> ''
    """.format(tids=",".join("?" * len(team_ids)))
    params = list(team_ids) + list(team_ids)
    if comp_types:
        sql += f" AND competition_type IN ({','.join('?' * len(comp_types))})"
        params.extend(comp_types)
    if cutoff_iso:
        sql += " AND substr(match_date,7,4)||substr(match_date,4,2)||substr(match_date,1,2) <= ?"
        params.append(cutoff_iso)
    if season_min is not None:
        sql += " AND season >= ?"
        params.append(season_min)
    sql += " ORDER BY substr(match_date,7,4)||substr(match_date,4,2)||substr(match_date,1,2) DESC"
    cur = conn.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_innings_summary(conn, match_id):
    """Per-match innings: list of (innings_seq, team_batting_id, team_batting_name,
    runs, wickets, overs)."""
    return conn.execute("""
        SELECT innings_seq, team_batting_id, team_batting_name, runs, wickets, overs
        FROM innings WHERE match_id=? ORDER BY innings_seq
    """, (match_id,)).fetchall()


def top_batters(conn, club_id, team_ids, seasons,
                comp_types=("League","Cup"), limit=15):
    """Top run scorers. team_ids: 1st XI team_ids. seasons: iterable of
    season ints to include."""
    s_ph = ",".join("?" * len(seasons))
    t_ph = ",".join("?" * len(team_ids))
    c_ph = ",".join("?" * len(comp_types))
    return conn.execute(f"""
        WITH bat AS (
            SELECT b.*, m.match_date, m.season FROM batting b
            JOIN matches m USING(match_id)
            WHERE b.team_batting_club_id=?
              AND b.team_batting_id IN ({t_ph})
              AND m.season IN ({s_ph})
              AND m.competition_type IN ({c_ph})
              AND b.batsman_id IS NOT NULL
              AND lower(coalesce(b.how_out,'')) NOT IN ('did not bat','absent')
        )
        SELECT
            batsman_id,
            (SELECT batsman_name FROM bat b2
             WHERE b2.batsman_id=bat.batsman_id AND batsman_name<>''
             ORDER BY season DESC, match_date DESC LIMIT 1) AS name,
            COUNT(*) AS innings,
            SUM(CASE WHEN lower(coalesce(how_out,'')) IN ('not out','retired not out')
                     THEN 1 ELSE 0 END) AS not_outs,
            SUM(coalesce(runs,0)) AS runs,
            MAX(runs) AS hs,
            SUM(CASE WHEN runs>=50 AND runs<100 THEN 1 ELSE 0 END) AS fifties,
            SUM(CASE WHEN runs>=100 THEN 1 ELSE 0 END) AS hundreds,
            SUM(CASE WHEN coalesce(balls,0)>0 THEN balls ELSE 0 END) AS balls_known,
            SUM(CASE WHEN coalesce(balls,0)>0 THEN coalesce(runs,0) ELSE 0 END) AS runs_when_balls,
            COUNT(DISTINCT match_id) AS matches,
            (SELECT position FROM bat b2 WHERE b2.batsman_id=bat.batsman_id
             GROUP BY position ORDER BY COUNT(*) DESC, position ASC LIMIT 1) AS mode_pos
        FROM bat
        GROUP BY batsman_id
        ORDER BY runs DESC
        LIMIT ?
    """, (club_id, *team_ids, *seasons, *comp_types, limit)).fetchall()


def top_bowlers(conn, club_id, team_ids, seasons,
                comp_types=("League","Cup"), limit=15):
    s_ph = ",".join("?" * len(seasons))
    t_ph = ",".join("?" * len(team_ids))
    c_ph = ",".join("?" * len(comp_types))
    rows = conn.execute(f"""
        SELECT bo.bowler_id, bo.bowler_name, bo.match_id, bo.overs,
               coalesce(bo.maidens,0), coalesce(bo.runs,0),
               coalesce(bo.wickets,0)
        FROM bowling bo JOIN matches m USING(match_id)
        WHERE bo.team_bowling_club_id=?
          AND bo.team_bowling_id IN ({t_ph})
          AND m.season IN ({s_ph})
          AND m.competition_type IN ({c_ph})
          AND bo.bowler_id IS NOT NULL
    """, (club_id, *team_ids, *seasons, *comp_types)).fetchall()
    agg = {}
    for bid, name, mid, overs, maid, r, w in rows:
        balls = overs_to_balls(overs)
        if balls == 0 and r == 0 and w == 0 and maid == 0:
            continue
        a = agg.setdefault(bid, {"name": "", "balls": 0, "maidens": 0, "runs": 0,
                                  "wickets": 0, "matches": set(), "fivers": 0,
                                  "fourers": 0, "best": None})
        if name: a["name"] = name
        a["balls"] += balls; a["maidens"] += maid; a["runs"] += r
        a["wickets"] += w; a["matches"].add(mid)
        if w >= 5: a["fivers"] += 1
        elif w == 4: a["fourers"] += 1
        cand = (w, -r)
        if a["best"] is None or cand > a["best"]:
            a["best"] = cand
    out = []
    for bid, a in agg.items():
        avg = (a["runs"] / a["wickets"]) if a["wickets"] else None
        econ = (a["runs"] / (a["balls"]/6)) if a["balls"] else None
        bb = f"{a['best'][0]}/{-a['best'][1]}" if a["best"] else "—"
        out.append({"bowler_id": bid, "name": a["name"], "matches": len(a["matches"]),
                    "overs": balls_to_overs(a["balls"]), "maidens": a["maidens"],
                    "runs": a["runs"], "wickets": a["wickets"],
                    "avg": avg, "econ": econ, "best": bb,
                    "fivers": a["fivers"], "fourers": a["fourers"]})
    out.sort(key=lambda r: (-r["wickets"], r["avg"] or 9e9))
    return out[:limit]


def team_avgs_per_season(conn, club_id, team_ids, seasons,
                          comp_types=("League","Cup")):
    """Returns dict {season: {bat_avg, bowl_avg}}.
    bat_avg = sum(runs)/sum(dismissals) by team's batters
    bowl_avg = sum(runs_conceded)/sum(wickets) by team's bowlers
    """
    out = {}
    s_ph = ",".join("?" * len(seasons))
    t_ph = ",".join("?" * len(team_ids))
    c_ph = ",".join("?" * len(comp_types))
    bat_rows = conn.execute(f"""
        SELECT m.season,
               SUM(coalesce(b.runs,0)) AS runs,
               SUM(CASE WHEN lower(coalesce(b.how_out,''))
                            NOT IN ('did not bat','absent','not out','retired not out','')
                        THEN 1 ELSE 0 END) AS dismissals
        FROM batting b JOIN matches m USING(match_id)
        WHERE b.team_batting_club_id=? AND b.team_batting_id IN ({t_ph})
          AND m.season IN ({s_ph}) AND m.competition_type IN ({c_ph})
        GROUP BY m.season
    """, (club_id, *team_ids, *seasons, *comp_types)).fetchall()
    bowl_rows = conn.execute(f"""
        SELECT m.season,
               SUM(coalesce(bo.runs,0)) AS runs,
               SUM(coalesce(bo.wickets,0)) AS wkts
        FROM bowling bo JOIN matches m USING(match_id)
        WHERE bo.team_bowling_club_id=? AND bo.team_bowling_id IN ({t_ph})
          AND m.season IN ({s_ph}) AND m.competition_type IN ({c_ph})
        GROUP BY m.season
    """, (club_id, *team_ids, *seasons, *comp_types)).fetchall()
    for season, runs, dismissals in bat_rows:
        out.setdefault(season, {})["bat_avg"] = (
            (runs / dismissals) if dismissals else None)
        out[season]["bat_runs"] = runs
        out[season]["bat_dismissals"] = dismissals
    for season, runs, wkts in bowl_rows:
        out.setdefault(season, {})["bowl_avg"] = (
            (runs / wkts) if wkts else None)
        out[season]["bowl_runs"] = runs
        out[season]["bowl_wkts"] = wkts
    return out


def head_to_head(conn, club_a_team_ids, club_b_team_ids,
                  comp_types=("League","Cup")):
    """Matches between two specific (1st XI) team ID sets."""
    if not club_a_team_ids or not club_b_team_ids:
        return []
    a_ph = ",".join("?" * len(club_a_team_ids))
    b_ph = ",".join("?" * len(club_b_team_ids))
    c_ph = ",".join("?" * len(comp_types))
    sql = f"""
        SELECT match_id, season, match_date,
               home_club_id, home_club_name, home_team_id, home_team_name,
               away_club_id, away_club_name, away_team_id, away_team_name,
               toss_won_by_team_id, toss, batted_first,
               competition_type, result, result_applied_to, result_description
        FROM matches
        WHERE ((home_team_id IN ({a_ph}) AND away_team_id IN ({b_ph}))
            OR (home_team_id IN ({b_ph}) AND away_team_id IN ({a_ph})))
          AND match_date <> ''
          AND competition_type IN ({c_ph})
        ORDER BY substr(match_date,7,4)||substr(match_date,4,2)||substr(match_date,1,2) DESC
    """
    cur = conn.execute(sql,
        (*club_a_team_ids, *club_b_team_ids, *club_b_team_ids, *club_a_team_ids,
         *comp_types))
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# ---------------------------------------------------------------- league table -

def load_league_table(division_id):
    p = LEAGUE_TABLE_DIR / f"{division_id}.json"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text())
    except Exception:
        return None
    tbls = d.get("league_table") or []
    if not tbls:
        return None
    return tbls[0]


def league_table_with_form(conn, division_id, season, club_team_ids_by_team,
                            today_iso):
    """Read the cached league table for `division_id` and decorate each row
    with `form` (last 5 results, oldest -> newest, from each team's matches)
    pulled from our matches table.

    club_team_ids_by_team: dict team_id -> set of all sibling team_ids
    representing that club (we don't have this; we'll compute form per team_id
    individually here).
    """
    table = load_league_table(division_id)
    if not table:
        return None
    headings = table.get("headings", {})
    def colnum(k):
        try: return int(k.split("_")[-1])
        except Exception: return 0
    sorted_keys = sorted(headings.keys(), key=colnum)
    pts_key = sorted_keys[-1] if sorted_keys else None
    p_key = next((k for k, h in headings.items() if str(h).lower()=="p"), None)
    w_key = next((k for k, h in headings.items() if str(h).lower()=="w"), None)
    rows = table.get("values", [])
    out = []
    for v in rows:
        team_id = v.get("team_id") or ""
        # Pull form for this team_id (last 5 played League matches in this season)
        form = []
        if team_id and season is not None:
            recs = conn.execute("""
                SELECT result, result_applied_to, home_team_id, away_team_id
                FROM matches
                WHERE (home_team_id=? OR away_team_id=?)
                  AND season=?
                  AND match_date<>''
                  AND substr(match_date,7,4)||substr(match_date,4,2)||substr(match_date,1,2) <= ?
                  AND result <> ''
                  AND competition_type='League'
                ORDER BY substr(match_date,7,4)||substr(match_date,4,2)||substr(match_date,1,2) DESC
                LIMIT 5
            """, (team_id, team_id, season, today_iso)).fetchall()
            for res, rat, hid, aid in recs:
                # Determine W/L/D for this team
                if res in ("D","A","T"):
                    form.append(res or "NR")
                elif rat == team_id:
                    form.append(res)
                elif rat:
                    form.append("L" if res == "W" else ("W" if res == "L" else res))
                else:
                    form.append(res or "NR")
            form = form[::-1]  # oldest to newest
        out.append({
            "position": v.get("position"),
            "team_id": team_id,
            "team": v.get("column_1"),
            "played": v.get(p_key) if p_key else "",
            "wins": v.get(w_key) if w_key else "",
            "points": v.get(pts_key) if pts_key else "",
            "form": form,
        })
    return out


# ---------------------------------------------------------------- chart helpers

def wld_segments(counts, keys=("W","D","L","T","A","NR")):
    """Return list of (label, count) for the result-type segments in order."""
    return [(k, counts.get(k, 0)) for k in keys if counts.get(k, 0) > 0]


# ---------------------------------------------------------------- main ----

def play_cricket_match_url(match_id):
    return f"https://play-cricket.com/website/results/{match_id}"


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--club-id")
    ap.add_argument("--club-name")
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--seasons-back", type=int, default=3)
    ap.add_argument("--last-recent", type=int, default=10)
    ap.add_argument("--vs-club-id", default=RAINHAM_CLUB_ID,
                    help="Comparison club (default: Rainham 5251).")
    ap.add_argument("--today", default=None,
                    help="Override today's date (yyyy-mm-dd).")
    return ap.parse_args()


def main():
    args = parse_args()
    today = args.today or dt.date.today().isoformat()
    today_iso = today.replace("-", "")

    conn = sqlite3.connect(DB)
    club_id, club_name = resolve_club(conn, args.club_name, args.club_id)
    target_team_ids = first_xi_team_ids(conn, club_id)
    if not target_team_ids:
        sys.exit(f"Could not identify a 1st XI team for {club_name}")
    print(f"Scouting {club_name} (club_id {club_id}) — 1st XI team_ids: {target_team_ids}")

    target_all = all_club_team_ids(conn, club_id)

    # Comparison club: Rainham (or whatever --vs-club-id)
    vs_club_id, vs_club_name = resolve_club(conn, None, args.vs_club_id)
    vs_team_ids = first_xi_team_ids(conn, vs_club_id)
    print(f"Compared vs {vs_club_name} (1st XI team_ids: {vs_team_ids})")

    # Seasons to look at (last N covering)
    all_seasons = sorted({r[0] for r in conn.execute(
        "SELECT DISTINCT season FROM matches WHERE home_club_id=? OR away_club_id=?",
        (club_id, club_id)).fetchall()})
    if args.season in all_seasons:
        cur_season = args.season
    else:
        cur_season = max(all_seasons) if all_seasons else args.season
    last_n_seasons = sorted({s for s in all_seasons if s <= cur_season})[-args.seasons_back:]
    print(f"Seasons under analysis: {last_n_seasons}; current: {cur_season}")

    # All 1st XI matches (last N seasons, past, L+C). matches_played is the
    # subset with a recorded result — used for charts and recent listings.
    # The season summary uses all `matches_all` so the P count aligns with the
    # league table (an unrecorded past match still counts toward the season).
    season_min = min(last_n_seasons) if last_n_seasons else cur_season
    matches_all = fetch_1stxi_matches(conn, target_team_ids,
                                       cutoff_iso=today_iso,
                                       season_min=season_min)
    matches = [m for m in matches_all if m["result"]]

    # ---- Section 0: League table ----
    # Find the division_id the 1st XI played in for the current season
    div_row = conn.execute(f"""
        SELECT league_name, competition_id, competition_name, COUNT(*) c
        FROM matches
        WHERE (home_team_id IN ({','.join('?' * len(target_team_ids))})
            OR away_team_id IN ({','.join('?' * len(target_team_ids))}))
          AND season=? AND competition_type='League'
        GROUP BY league_name, competition_id, competition_name
        ORDER BY c DESC LIMIT 1
    """, (*target_team_ids, *target_team_ids, cur_season)).fetchone()
    league_table = None
    league_div_label = None
    if div_row:
        league_name, division_id, division_name, _ = div_row
        league_div_label = f"{league_name} — {division_name}"
        league_table = league_table_with_form(conn, division_id, cur_season,
                                               {}, today_iso)
        if not league_table:
            # Fall back to last completed season's table
            prev_div = conn.execute(f"""
                SELECT competition_id FROM matches
                WHERE (home_team_id IN ({','.join('?' * len(target_team_ids))})
                    OR away_team_id IN ({','.join('?' * len(target_team_ids))}))
                  AND season=? AND competition_type='League'
                LIMIT 1
            """, (*target_team_ids, *target_team_ids, cur_season - 1)).fetchone()
            if prev_div and prev_div[0]:
                league_table = league_table_with_form(conn, prev_div[0],
                                                       cur_season - 1, {}, today_iso)

    # ---- Sections 1: last 3 seasons L+C wld + finishing positions ----
    season_summary = []
    for s in last_n_seasons:
        # League-only & include un-recorded (NR) past matches so P matches the
        # league table.
        s_matches = [m for m in matches_all if m["season"] == s
                      and m["competition_type"] == "League"]
        counts = compute_wld(
            [{"result": m["result"], "result_applied_to": m["result_applied_to"]}
             for m in s_matches], target_all)
        # Find the division for that season + position
        sdrow = conn.execute(f"""
            SELECT league_name, competition_id, competition_name FROM matches
            WHERE (home_team_id IN ({','.join('?' * len(target_team_ids))})
                OR away_team_id IN ({','.join('?' * len(target_team_ids))}))
              AND season=? AND competition_type='League'
            GROUP BY league_name, competition_id, competition_name
            ORDER BY COUNT(*) DESC LIMIT 1
        """, (*target_team_ids, *target_team_ids, s)).fetchone()
        league_label = "—"
        pos_str = "—"
        if sdrow:
            ln, div_id, dn = sdrow
            league_label = f"{ln} — {dn}"
            tbl = load_league_table(div_id)
            if tbl:
                for v in tbl.get("values", []):
                    if v.get("team_id") in target_team_ids:
                        total = len(tbl.get("values", []))
                        pos_str = f"{v.get('position')}/{total}"
                        break
        season_summary.append({
            "season": s, "league": league_label, "pos": pos_str,
            **counts,
            "win_pct": (100 * counts["W"] / counts["P"]) if counts["P"] else 0,
        })

    # ---- Sections 2 & 3: Top batters/bowlers across last N seasons ----
    bat_rows = top_batters(conn, club_id, target_team_ids, last_n_seasons, limit=15)
    bowl_rows = top_bowlers(conn, club_id, target_team_ids, last_n_seasons, limit=12)

    # ---- Section 4: H2H vs RCC 1st XI ----
    h2h_matches = head_to_head(conn, target_team_ids, vs_team_ids,
                                comp_types=("League","Cup"))
    h2h_played = [m for m in h2h_matches if m["result"]]
    h2h_upcoming = [m for m in h2h_matches if not m["result"]
                    and to_iso(m["match_date"]) >= today_iso]

    # ---- Section 5: last 10 1st XI played L+C with scores ----
    recent = matches[:args.last_recent]   # already DESC
    # Decorate with innings scores
    for m in recent:
        innings = fetch_innings_summary(conn, m["match_id"])
        m["innings_lines"] = innings

    # ---- Section 6 charts ----
    # 6a. bat 1st vs bat 2nd
    def split_bat_first(rows, team_ids_set):
        bat1, bat2, unknown = [], [], []
        for m in rows:
            bf = (m.get("batted_first") or "").strip()
            our_tid = (m["home_team_id"] if m["home_team_id"] in team_ids_set
                       else m["away_team_id"] if m["away_team_id"] in team_ids_set
                       else None)
            if not bf or not our_tid:
                unknown.append(m); continue
            (bat1 if bf == our_tid else bat2).append(m)
        return bat1, bat2

    bat1, bat2 = split_bat_first(matches, set(target_team_ids))
    chart_bat = {
        "us_bat1": compute_wld([{"result": m["result"],
                                   "result_applied_to": m["result_applied_to"]}
                                  for m in bat1], target_all),
        "us_bat2": compute_wld([{"result": m["result"],
                                   "result_applied_to": m["result_applied_to"]}
                                  for m in bat2], target_all),
    }

    # Same for vs (Rainham) for comparison
    vs_matches = fetch_1stxi_matches(conn, vs_team_ids, cutoff_iso=today_iso,
                                      season_min=season_min)
    vs_matches = [m for m in vs_matches if m["result"]]
    vs_team_set = set(vs_team_ids)
    vs_all = all_club_team_ids(conn, vs_club_id)
    v_bat1, v_bat2 = split_bat_first(vs_matches, vs_team_set)
    chart_bat["vs_bat1"] = compute_wld(
        [{"result": m["result"], "result_applied_to": m["result_applied_to"]}
         for m in v_bat1], vs_all)
    chart_bat["vs_bat2"] = compute_wld(
        [{"result": m["result"], "result_applied_to": m["result_applied_to"]}
         for m in v_bat2], vs_all)

    # 6b. home vs away
    def split_home_away(rows, club_id):
        home, away = [], []
        for m in rows:
            (home if m["home_club_id"] == club_id else away).append(m)
        return home, away
    us_home, us_away = split_home_away(matches, club_id)
    vs_home, vs_away = split_home_away(vs_matches, vs_club_id)
    chart_ha = {
        "us_home": compute_wld([{"result": m["result"],
                                   "result_applied_to": m["result_applied_to"]}
                                  for m in us_home], target_all),
        "us_away": compute_wld([{"result": m["result"],
                                   "result_applied_to": m["result_applied_to"]}
                                  for m in us_away], target_all),
        "vs_home": compute_wld([{"result": m["result"],
                                   "result_applied_to": m["result_applied_to"]}
                                  for m in vs_home], vs_all),
        "vs_away": compute_wld([{"result": m["result"],
                                   "result_applied_to": m["result_applied_to"]}
                                  for m in vs_away], vs_all),
    }

    # 6c. Toss won — what did they DO with it (bat or field)? + outcome.
    toss_won = [m for m in matches
                 if (m.get("toss_won_by_team_id") or "") in target_team_ids]
    toss_lost = [m for m in matches
                  if (m.get("toss_won_by_team_id") or "") and
                  m["toss_won_by_team_id"] not in target_team_ids]

    def split_choice(rows, target_set):
        """When this team won the toss, did they choose to bat or field?
        Determined by whether they batted first."""
        chose_bat, chose_field = [], []
        for m in rows:
            bf = (m.get("batted_first") or "").strip()
            we_batted_first = bf in target_set
            (chose_bat if we_batted_first else chose_field).append(m)
        return chose_bat, chose_field

    target_set = set(target_team_ids)
    chose_bat, chose_field = split_choice(toss_won, target_set)
    chart_toss = {
        "won_n": len(toss_won),
        "chose_bat_n": len(chose_bat),
        "chose_field_n": len(chose_field),
        "chose_bat_outcome": compute_wld(
            [{"result": m["result"], "result_applied_to": m["result_applied_to"]}
             for m in chose_bat], target_all),
        "chose_field_outcome": compute_wld(
            [{"result": m["result"], "result_applied_to": m["result_applied_to"]}
             for m in chose_field], target_all),
        "won_outcome": compute_wld(
            [{"result": m["result"], "result_applied_to": m["result_applied_to"]}
             for m in toss_won], target_all),
        "lost_outcome": compute_wld(
            [{"result": m["result"], "result_applied_to": m["result_applied_to"]}
             for m in toss_lost], target_all),
    }

    # 6d. Team avgs per season (vs and us)
    us_avgs = team_avgs_per_season(conn, club_id, target_team_ids, last_n_seasons)
    vs_avgs = team_avgs_per_season(conn, vs_club_id, vs_team_ids, last_n_seasons)

    # ---- Build report data ----
    data = {
        "club_name": club_name,
        "club_id": club_id,
        "vs_club_name": vs_club_name,
        "today": today,
        "current_season": cur_season,
        "last_n_seasons": last_n_seasons,
        "league_div_label": league_div_label,
        "league_table": league_table,
        "season_summary": season_summary,
        "top_batters": bat_rows,
        "top_bowlers": bowl_rows,
        "h2h": h2h_played,
        "h2h_upcoming": h2h_upcoming,
        "h2h_vs_team_ids": list(vs_team_ids),
        "target_team_ids": list(target_team_ids),
        "recent": recent,
        "chart_bat": chart_bat,
        "chart_ha": chart_ha,
        "chart_toss": chart_toss,
        "us_avgs": us_avgs,
        "vs_avgs": vs_avgs,
        "video_links": video_links_for(club_id, club_name, recent_matches=recent),
    }

    # ---- Output paths ----
    out_dir = REPORTS_DIR / today / slugify(club_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "scout.md"
    html_path = out_dir / "scout.html"
    md_path.write_text(render_md(data))
    html_path.write_text(render_html(data))
    print(f"Wrote {md_path}")
    print(f"Wrote {html_path}")
    return 0


# ---------------------------------------------------------------- video links --

# Hard-coded official / verified links. The 'verified' list has been
# eyeballed (or the user has supplied them). The 'speculative' list is
# from name-based web searches and may not be the right entity.
CLUB_LINKS = {
    "14366": {  # Spartans CC, Essex
        "official": [
            ("Play-Cricket club page", "https://spartansessex.play-cricket.com/home"),
            ("Club website", "https://www.thespartanscricketclub.com/"),
            ("Instagram (@spartanscricket)", "https://www.instagram.com/spartanscricket/"),
        ],
        "speculative": [
            ("YouTube channel '@essexspartans8127' — name match, NOT verified",
             "https://www.youtube.com/@essexspartans8127"),
            ("Highlights: 'Challengers CC vs Spartans CC' (RCA T20 Cup) — "
             "name match, NOT verified that this is the same Spartans",
             "https://www.youtube.com/watch?v=OQ0vzP7ylx4"),
        ],
    },
    "6909": {  # Wickford CC
        "official": [
            ("Play-Cricket club page", "https://wickford.play-cricket.com/home"),
            ("Club website", "https://www.wickfordcc.co.uk/"),
            ("Twitter/X (@WickfordCC)", "https://x.com/wickfordcc"),
            ("Instagram (@wickfordcc)", "https://www.instagram.com/wickfordcc/"),
            ("Facebook", "https://www.facebook.com/WickfordCC/"),
        ],
        "verified_videos": [
            ("Wickford livestream (user-supplied, exact match TBC)",
             "https://www.youtube.com/live/GN-Ou--1pwg"),
            ("Bentley CC 1st XI v Wickford CC Saturday 1st XI "
             "(Div 3 Round 18, 7 Sep 2024)",
             "https://www.youtube.com/watch?v=EltwYIp1e6o"),
        ],
    },
    # Opposition channels we've found while scouting other clubs — keep so
    # they can be re-used when those clubs come up as opponents.
    "_opposition_channels": {
        "Brentwood CC": "https://www.youtube.com/@brentwoodcricketclub2319",
    },
}


def yt_search_url(query):
    from urllib.parse import quote_plus
    return f"https://www.youtube.com/results?search_query={quote_plus(query)}"


def video_links_for(club_id, club_name, recent_matches=None):
    """Return ([(label, url, kind), ...]) where kind is 'official', 'video',
    'probe' or 'speculative'."""
    out = []
    info = CLUB_LINKS.get(str(club_id), {})

    for label, url in info.get("official", []):
        out.append((label, url, "official"))
    for label, url in info.get("verified_videos", []):
        out.append((label, url, "video"))

    # Per-match YouTube search probes — build one for each recent played
    # match so the user can click through to the search-results page.
    # YouTube doesn't expose a searchable API publicly, so we can't
    # automatically pick out the right video; this gives a one-click probe.
    if recent_matches:
        for m in recent_matches[:10]:
            opp = (m["away_club_name"] if m["home_club_id"] == str(club_id)
                   else m["home_club_name"])
            opp_short = re.sub(r",.*$", "", opp).strip()
            club_short = re.sub(r",.*$", "", club_name).strip()
            year = (m["match_date"] or "")[-4:]
            q = f'"{club_short}" "{opp_short}" cricket {year}'
            out.append((f"YouTube search — {m['match_date']} vs {opp_short}",
                        yt_search_url(q), "probe"))

    for label, url in info.get("speculative", []):
        out.append((label, url, "speculative"))

    if not info.get("official"):
        # Generic fallback for any club not in CLUB_LINKS yet.
        out.append((f"Google search — {club_name} video",
                     f"https://www.google.com/search?q=%22"
                     f"{club_name.replace(' ','+')}%22+cricket+video",
                     "probe"))
        out.append((f"YouTube search — {club_name}",
                    yt_search_url(f"{club_name} cricket"),
                    "probe"))
    return out


# ---------------------------------------------------------------- markdown ---

def render_md(d):
    md = []
    md.append(f"# Scout — {d['club_name']} 1st XI")
    md.append("")
    md.append(f"_All sections: 1st XI only, League + Cup unless noted. "
              f"Compared vs {d['vs_club_name']} 1st XI. Today {d['today']}._")
    md.append("")

    # 0. League table
    md.append("## 0. Current league table")
    md.append("")
    if d["league_table"]:
        md.append(f"_{d['league_div_label'] or ''}_")
        md.append("")
        md.append("| Pos | Team | P | W | Pts | Form |")
        md.append("|--:|------|--:|--:|---:|------|")
        for r in d["league_table"]:
            highlight = "**" if r["team_id"] in d["target_team_ids"] else ""
            form = " ".join(r["form"]) if r["form"] else "—"
            md.append(f"| {r['position']} | {highlight}{r['team']}{highlight} "
                      f"| {r['played']} | {r['wins']} | {r['points']} | {form} |")
    else:
        md.append("_No cached league table for this season; skipped._")
    md.append("")

    # 1. Last 3 seasons finishing positions
    md.append("## 1. Last 3 seasons — 1st XI league finishing positions")
    md.append("")
    md.append("| Season | League / Division | Pos | P | W | L | D | NR | Win% |")
    md.append("|--:|---|--:|--:|--:|--:|--:|--:|--:|")
    for s in d["season_summary"]:
        md.append(f"| {s['season']} | {s['league']} | {s['pos']} | "
                  f"{s['P']} | {s['W']} | {s['L']} | {s['D']} | {s['NR']} | "
                  f"{s['win_pct']:.1f}% |")
    md.append("")

    # 2. Top batters
    md.append(f"## 2. Top run scorers — last {len(d['last_n_seasons'])} seasons (1st XI, L+C)")
    md.append("")
    md.append("| # | Player | M | I | NO | Runs | HS | Avg | SR | 50 | 100 | Pos |")
    md.append("|--:|--------|--:|--:|--:|---:|---:|---:|---:|--:|--:|--:|")
    for i, r in enumerate(d["top_batters"], 1):
        (bid, name, innings, no_, runs, hs, fifties, hundreds, balls_known,
         runs_when_balls, matches_, mode_pos) = r
        avg = f"{runs/(innings-no_):.2f}" if (innings-no_) > 0 else "—"
        sr = f"{100*runs_when_balls/balls_known:.1f}" if balls_known else "—"
        md.append(f"| {i} | {name} | {matches_} | {innings} | {no_} | "
                  f"**{runs}** | {hs} | {avg} | {sr} | {fifties} | {hundreds} | {mode_pos} |")
    md.append("")

    # 3. Top bowlers
    md.append(f"## 3. Top wicket takers — last {len(d['last_n_seasons'])} seasons (1st XI, L+C)")
    md.append("")
    md.append("| # | Bowler | M | Overs | Mdns | Runs | Wkts | Avg | Econ | Best | 5wi | 4wi |")
    md.append("|--:|--------|--:|--:|--:|---:|---:|---:|---:|----|--:|--:|")
    for i, r in enumerate(d["top_bowlers"], 1):
        avg = f"{r['avg']:.2f}" if r["avg"] is not None else "—"
        econ = f"{r['econ']:.2f}" if r["econ"] is not None else "—"
        md.append(f"| {i} | {r['name']} | {r['matches']} | {r['overs']} | "
                  f"{r['maidens']} | {r['runs']} | **{r['wickets']}** | {avg} | "
                  f"{econ} | {r['best']} | {r['fivers']} | {r['fourers']} |")
    md.append("")

    # 4. H2H
    md.append(f"## 4. Head-to-head vs {d['vs_club_name']} 1st XI (any era in cache)")
    md.append("")
    if d["h2h"]:
        # tally
        tally = {"W_them": 0, "L_them": 0, "D": 0}
        for m in d["h2h"]:
            target_set = set(d["target_team_ids"])
            r = result_for(m["result"], m["result_applied_to"], target_set)
            if r == "W": tally["W_them"] += 1
            elif r == "L": tally["L_them"] += 1
            else: tally["D"] += 1
        md.append(f"**{d['club_name']} {tally['W_them']} — "
                  f"{tally['L_them']} {d['vs_club_name']}**, "
                  f"draws/abandoned {tally['D']}.")
        md.append("")
        md.append("| Date | Comp | Venue (for them) | Result | Detail |")
        md.append("|---|---|---|---|---|")
        for m in d["h2h"][:15]:
            we_home = m["home_club_id"] == d["club_id"]
            venue = "H" if we_home else "A"
            target_set = set(d["target_team_ids"])
            r = result_for(m["result"], m["result_applied_to"], target_set)
            md.append(f"| {m['match_date']} | {m['competition_type']} | {venue} | "
                      f"{r} | {m['result_description'] or '—'} |")
    else:
        md.append("_No played 1st XI head-to-head matches in the cache._")
    if d.get("h2h_upcoming"):
        md.append("")
        md.append("**Upcoming fixtures:**")
        for m in sorted(d["h2h_upcoming"], key=lambda x: to_iso(x["match_date"])):
            we_home = m["home_club_id"] == d["club_id"]
            venue = "H" if we_home else "A"
            md.append(f"- {m['match_date']} ({m['competition_type']}, {venue})")
    md.append("")

    # 5. Recent
    md.append(f"## 5. Last {len(d['recent'])} 1st XI played matches (L+C)")
    md.append("")
    md.append("| Date | Comp | Venue | Opponent | Result | Score |")
    md.append("|---|---|---|---|---|---|")
    for m in d["recent"]:
        we_home = m["home_club_id"] == d["club_id"]
        venue = "H" if we_home else "A"
        opp = m["away_club_name"] if we_home else m["home_club_name"]
        target_set = set(d["target_team_ids"])
        r = result_for(m["result"], m["result_applied_to"], target_set)
        scores = innings_inline(m)
        md.append(f"| [{m['match_date']}]({play_cricket_match_url(m['match_id'])}) "
                  f"| {m['competition_type']} | {venue} | {opp} | {r} | {scores} |")
    md.append("")

    # 6. Charts (text-only summary in MD; HTML has bars)
    md.append("## 6. Patterns")
    md.append("")
    md.append("### 6a. When batting first vs second (last 3 seasons L+C)")
    md.append("")
    md.append(f"- {d['club_name']} batting 1st: " + wld_str(d["chart_bat"]["us_bat1"]))
    md.append(f"- {d['club_name']} batting 2nd: " + wld_str(d["chart_bat"]["us_bat2"]))
    md.append(f"- {d['vs_club_name']} batting 1st: " + wld_str(d["chart_bat"]["vs_bat1"]))
    md.append(f"- {d['vs_club_name']} batting 2nd: " + wld_str(d["chart_bat"]["vs_bat2"]))
    md.append("")

    md.append("### 6b. Home vs away (last 3 seasons L+C)")
    md.append("")
    md.append(f"- {d['club_name']} at home: " + wld_str(d["chart_ha"]["us_home"]))
    md.append(f"- {d['club_name']} away: " + wld_str(d["chart_ha"]["us_away"]))
    md.append(f"- {d['vs_club_name']} at home: " + wld_str(d["chart_ha"]["vs_home"]))
    md.append(f"- {d['vs_club_name']} away: " + wld_str(d["chart_ha"]["vs_away"]))
    md.append("")

    md.append("### 6c. When they win the toss")
    md.append("")
    t = d["chart_toss"]
    won_n = t["won_n"]
    if won_n:
        bat_n = t["chose_bat_n"]; fld_n = t["chose_field_n"]
        bat_pct = 100 * bat_n / won_n if won_n else 0
        fld_pct = 100 * fld_n / won_n if won_n else 0
        md.append(f"**Won toss in {won_n} matches** — chose to bat {bat_n} "
                  f"({bat_pct:.0f}%), chose to field {fld_n} ({fld_pct:.0f}%).")
        md.append("")
        md.append(f"- Won toss → batted first → " + wld_str(t["chose_bat_outcome"]))
        md.append(f"- Won toss → fielded first → " + wld_str(t["chose_field_outcome"]))
        md.append("")
        md.append(f"_Reference: lost toss → outcome: {wld_str(t['lost_outcome'])}._")
    else:
        md.append("_No matches with toss data in scope._")
    md.append("")

    md.append("### 6d. Team batting & bowling avg per season (1st XI, L+C)")
    md.append("")
    md.append("| Season | "
              f"{d['club_name']} bat | {d['vs_club_name']} bat | "
              f"{d['club_name']} bowl | {d['vs_club_name']} bowl |")
    md.append("|--:|--:|--:|--:|--:|")
    for s in d["last_n_seasons"]:
        u = d["us_avgs"].get(s, {})
        v = d["vs_avgs"].get(s, {})
        ub = f"{u['bat_avg']:.2f}" if u.get("bat_avg") else "—"
        vb = f"{v['bat_avg']:.2f}" if v.get("bat_avg") else "—"
        ubo = f"{u['bowl_avg']:.2f}" if u.get("bowl_avg") else "—"
        vbo = f"{v['bowl_avg']:.2f}" if v.get("bowl_avg") else "—"
        md.append(f"| {s} | {ub} | {vb} | {ubo} | {vbo} |")
    md.append("")

    # 7. Links
    md.append("## 7. Web / video links")
    md.append("")
    md.append("> ⚠️ **Speculative.** The Play-Cricket API doesn't expose match "
              "video URLs, and there's no API for finding the right YouTube "
              "channel for a given club. Anything tagged _(speculative)_ is a "
              "name-based guess and may be the wrong entity entirely. **Verify "
              "the channel/post matches this club before sharing.** Per-match "
              "search probes are one-click YouTube searches scoped to the "
              "fixture — typical hits are full-match livestreams hosted by the "
              "home club's channel (often a few hours long).")
    md.append("")
    by_kind = {"official": [], "video": [], "probe": [], "speculative": []}
    for label, url, kind in d["video_links"]:
        by_kind.setdefault(kind, []).append((label, url))

    if by_kind["official"]:
        md.append("**Official / club channels**")
        for lbl, url in by_kind["official"]:
            md.append(f"- [{lbl}]({url})")
        md.append("")
    if by_kind["video"]:
        md.append("**Verified match videos**")
        for lbl, url in by_kind["video"]:
            md.append(f"- [{lbl}]({url})")
        md.append("")
    if by_kind["probe"]:
        md.append("**Per-match YouTube search probes** (one click per fixture)")
        for lbl, url in by_kind["probe"]:
            md.append(f"- [{lbl}]({url})")
        md.append("")
    if by_kind["speculative"]:
        md.append("**Speculative — name match only, NOT verified**")
        for lbl, url in by_kind["speculative"]:
            md.append(f"- [{lbl}]({url}) _(speculative)_")
        md.append("")
    return "\n".join(md)


def innings_inline(m):
    """Compact 'A 187/8 (40), B 188/3 (38.5)' from an innings list, with team
    names abbreviated. Uses m['innings_lines'] populated in main()."""
    parts = []
    lines = m.get("innings_lines") or []
    for seq, tid, tname, runs, wkts, overs in lines:
        # Abbreviate team name
        nm = (tname or "").replace(" - 1st XI", "").replace(", Essex", "").strip()
        if " - " in nm:
            nm = nm.split(" - ")[0]
        if "CC" in nm:
            nm = nm.replace(" CC", "")
        score = f"{runs}/{wkts}" if runs is not None else "—"
        if overs and overs != "0":
            score += f" ({overs})"
        parts.append(f"{nm} {score}")
    return " · ".join(parts) if parts else "—"


def wld_str(counts):
    p = counts.get("P", 0)
    if not p:
        return "_no matches_"
    w, l, d = counts.get("W",0), counts.get("L",0), counts.get("D",0) + counts.get("T",0)
    nr = counts.get("NR",0) + counts.get("A",0)
    pct = (100*w/p) if p else 0
    bits = [f"{w}W", f"{l}L"]
    if d: bits.append(f"{d}D")
    if nr: bits.append(f"{nr}NR")
    return f"{p} played, " + " / ".join(bits) + f" — win rate {pct:.0f}%"


# ---------------------------------------------------------------- HTML ---

CSS = """
*{box-sizing:border-box}
body{margin:0;padding:14px;font-family:-apple-system,BlinkMacSystemFont,
     'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;
     font-size:14px;line-height:1.35;color:#111;background:#fafafa;
     max-width:540px;margin:0 auto}
h1{font-size:21px;margin:0 0 6px}
h2{font-size:16px;margin:20px 0 6px;padding-top:10px;border-top:1px solid #ddd;
   font-weight:700}
h2:first-of-type{border-top:none;padding-top:0}
h3{font-size:13px;margin:12px 0 4px;color:#333}
p{font-size:13px;margin:4px 0 8px}
p.meta{color:#555;margin:0 0 12px;font-size:12px}
table{width:100%;border-collapse:collapse;margin:6px 0 10px;
      font-size:11.5px;font-variant-numeric:tabular-nums}
th,td{padding:4px 5px;border-bottom:1px solid #e6e6e6;text-align:right;
      white-space:nowrap}
th{background:#f0f0f0;font-weight:600;color:#444;text-align:right;
   border-bottom:2px solid #d0d0d0}
th.l,td.l{text-align:left;white-space:normal;word-break:break-word}
tr.us{background:#fff5cc}
tr.us td{font-weight:600}
.pill{display:inline-block;padding:1px 6px;border-radius:10px;
      font-size:11px;font-weight:600;color:#fff;min-width:14px;
      text-align:center;line-height:14px}
.pill.W{background:#2c8a2c}
.pill.L{background:#c03030}
.pill.D{background:#888}
.pill.T{background:#888}
.pill.NR,.pill.A{background:#caa84e}
.form-pill{display:inline-block;width:14px;height:14px;border-radius:3px;
           margin:0 1px;font-size:10px;line-height:14px;text-align:center;
           color:#fff;font-weight:700}
.form-pill.W{background:#2c8a2c}
.form-pill.L{background:#c03030}
.form-pill.D,.form-pill.T,.form-pill.NR,.form-pill.A{background:#888}

.bar-row{display:flex;align-items:center;margin:5px 0;gap:8px;font-size:12px}
.bar-row .lbl{flex:0 0 110px;color:#444}
.bar-row .bar{flex:1;height:22px;background:#eee;display:flex;
              border-radius:3px;overflow:hidden;border:1px solid #d6d6d6}
.bar-row .bar .seg{display:flex;align-items:center;justify-content:center;
                   color:#fff;font-weight:600;font-size:11px;line-height:1}
.seg.W{background:#2c8a2c}
.seg.L{background:#c03030}
.seg.D{background:#888}
.seg.T{background:#888}
.seg.NR,.seg.A{background:#caa84e}
.bar-row .num{flex:0 0 110px;text-align:right;color:#666;font-size:11px}

.avg-grid{display:grid;grid-template-columns:48px repeat(4, 1fr) 50px;
          gap:3px 6px;font-size:11px;align-items:center;margin:6px 0}
.avg-grid .gh{font-weight:600;color:#555;font-size:10.5px;text-align:center}
.avg-grid .gv{text-align:right;font-variant-numeric:tabular-nums}
.avg-grid .bar2{height:14px;background:#dde;border-radius:2px;position:relative}
.avg-grid .bar2 span{position:absolute;left:0;top:0;bottom:0;display:block;
                     border-radius:2px}
.avg-grid .bar2 .us{background:#2563b8}
.avg-grid .bar2 .vs{background:#c03030}

a{color:#0a58ca;text-decoration:none;word-break:break-all}
.bullets{padding-left:18px;margin:6px 0}
.bullets li{margin:3px 0;font-size:12px}
.small{font-size:11px;color:#666}
.footer{margin-top:18px;font-size:11px;color:#666}
.score-cell{font-size:11px}
"""


def render_html(d):
    parts = []
    parts.append("<!doctype html>")
    parts.append('<html lang="en"><head><meta charset="utf-8">')
    parts.append('<meta name="viewport" content="width=device-width,initial-scale=1">')
    parts.append(f"<title>Scout — {_esc(d['club_name'])} 1st XI</title>")
    parts.append(f"<style>{CSS}</style></head><body>")
    parts.append(f"<h1>Scout: {_esc(d['club_name'])} 1st XI</h1>")
    parts.append(f"<p class='meta'>Compared vs {_esc(d['vs_club_name'])} 1st XI. "
                 f"League + Cup unless noted. Today {_esc(d['today'])}.</p>")

    # 0. League table
    parts.append("<h2>League table — current</h2>")
    if d["league_table"]:
        parts.append(f"<p class='small'>{_esc(d['league_div_label'] or '')}</p>")
        parts.append("<table><tr>"
                     "<th>Pos</th><th class='l'>Team</th>"
                     "<th>P</th><th>W</th><th>Pts</th><th>Form</th></tr>")
        for r in d["league_table"]:
            cls = " class='us'" if r["team_id"] in d["target_team_ids"] else ""
            form_html = "".join(
                f'<span class="form-pill {f}">{_esc(f)}</span>' for f in r["form"]
            ) or "<span class='small'>—</span>"
            parts.append(f"<tr{cls}>"
                         f"<td>{_esc(r['position'])}</td>"
                         f"<td class='l'>{_esc(r['team'])}</td>"
                         f"<td>{_esc(r['played'])}</td>"
                         f"<td>{_esc(r['wins'])}</td>"
                         f"<td>{_esc(r['points'])}</td>"
                         f"<td class='l'>{form_html}</td></tr>")
        parts.append("</table>")
    else:
        parts.append("<p class='small'>No cached table for this season.</p>")

    # 1. Last N seasons finishing positions
    parts.append("<h2>Last 3 seasons — finishing positions</h2>")
    parts.append("<table><tr><th>Yr</th><th class='l'>League / Division</th>"
                 "<th>Pos</th><th>P</th><th>W</th><th>L</th><th>D</th>"
                 "<th>NR</th><th>Win%</th></tr>")
    for s in d["season_summary"]:
        parts.append(f"<tr><td>{s['season']}</td>"
                     f"<td class='l'>{_esc(s['league'])}</td>"
                     f"<td>{_esc(s['pos'])}</td>"
                     f"<td>{s['P']}</td><td>{s['W']}</td><td>{s['L']}</td>"
                     f"<td>{s['D']}</td><td>{s['NR']}</td>"
                     f"<td>{s['win_pct']:.0f}%</td></tr>")
    parts.append("</table>")

    # 2. Top batters
    parts.append(f"<h2>Top run scorers ({len(d['last_n_seasons'])} seasons)</h2>")
    parts.append("<table><tr><th>#</th><th class='l'>Player</th>"
                 "<th>M</th><th>I</th><th>NO</th><th>Runs</th>"
                 "<th>HS</th><th>Avg</th><th>SR</th><th>50</th><th>100</th><th>Pos</th></tr>")
    for i, r in enumerate(d["top_batters"], 1):
        (bid, name, innings, no_, runs, hs, fifties, hundreds, balls_known,
         runs_when_balls, matches_, mode_pos) = r
        avg = f"{runs/(innings-no_):.2f}" if (innings-no_) > 0 else "—"
        sr = f"{100*runs_when_balls/balls_known:.1f}" if balls_known else "—"
        parts.append(f"<tr><td>{i}</td><td class='l'>{_esc(name)}</td>"
                     f"<td>{matches_}</td><td>{innings}</td><td>{no_}</td>"
                     f"<td><b>{runs}</b></td><td>{hs}</td><td>{avg}</td>"
                     f"<td>{sr}</td><td>{fifties}</td><td>{hundreds}</td>"
                     f"<td>{mode_pos}</td></tr>")
    parts.append("</table>")

    # 3. Top bowlers
    parts.append(f"<h2>Top wicket takers ({len(d['last_n_seasons'])} seasons)</h2>")
    parts.append("<table><tr><th>#</th><th class='l'>Bowler</th>"
                 "<th>M</th><th>Ov</th><th>Md</th><th>R</th>"
                 "<th>W</th><th>Avg</th><th>Econ</th><th>Best</th>"
                 "<th>5wi</th><th>4wi</th></tr>")
    for i, r in enumerate(d["top_bowlers"], 1):
        avg = f"{r['avg']:.2f}" if r["avg"] is not None else "—"
        econ = f"{r['econ']:.2f}" if r["econ"] is not None else "—"
        parts.append(f"<tr><td>{i}</td><td class='l'>{_esc(r['name'])}</td>"
                     f"<td>{r['matches']}</td><td>{r['overs']}</td>"
                     f"<td>{r['maidens']}</td><td>{r['runs']}</td>"
                     f"<td><b>{r['wickets']}</b></td><td>{avg}</td>"
                     f"<td>{econ}</td><td>{r['best']}</td>"
                     f"<td>{r['fivers']}</td><td>{r['fourers']}</td></tr>")
    parts.append("</table>")

    # 4. H2H
    parts.append(f"<h2>Head-to-head vs {_esc(d['vs_club_name'])} 1st XI</h2>")
    if d["h2h"]:
        target_set = set(d["target_team_ids"])
        tally = {"W":0, "L":0, "D":0}
        for m in d["h2h"]:
            r = result_for(m["result"], m["result_applied_to"], target_set)
            if r == "W": tally["W"] += 1
            elif r == "L": tally["L"] += 1
            else: tally["D"] += 1
        parts.append(f"<p><b>{_esc(d['club_name'])} {tally['W']} — "
                     f"{tally['L']} {_esc(d['vs_club_name'])}</b>"
                     f", draws/abandoned {tally['D']} (n={len(d['h2h'])}).</p>")
        parts.append("<table><tr><th>Date</th><th>Comp</th>"
                     "<th>Venue</th><th>Result</th><th class='l'>Detail</th></tr>")
        for m in d["h2h"][:15]:
            we_home = m["home_club_id"] == d["club_id"]
            venue = "H" if we_home else "A"
            r = result_for(m["result"], m["result_applied_to"], target_set)
            url = play_cricket_match_url(m["match_id"])
            parts.append(f"<tr><td><a href='{url}'>{_esc(m['match_date'])}</a></td>"
                         f"<td>{_esc(m['competition_type'])}</td>"
                         f"<td>{venue}</td>"
                         f"<td><span class='pill {r}'>{r}</span></td>"
                         f"<td class='l'>{_esc(m['result_description'] or '')}</td></tr>")
        parts.append("</table>")
    else:
        parts.append("<p class='small'>No played 1st XI head-to-head matches in the cache.</p>")
    if d.get("h2h_upcoming"):
        parts.append("<p><b>Upcoming fixtures:</b></p>")
        parts.append("<ul class='bullets'>")
        for m in sorted(d["h2h_upcoming"], key=lambda x: to_iso(x["match_date"])):
            we_home = m["home_club_id"] == d["club_id"]
            venue = "H" if we_home else "A"
            parts.append(f"<li>{_esc(m['match_date'])} "
                         f"({_esc(m['competition_type'])}, {venue})</li>")
        parts.append("</ul>")

    # 5. Recent
    parts.append(f"<h2>Last {len(d['recent'])} 1st XI played matches (L+C)</h2>")
    parts.append("<table><tr><th>Date</th><th>Comp</th><th>V</th>"
                 "<th class='l'>Opponent</th><th>Res</th><th class='l'>Score</th></tr>")
    target_set = set(d["target_team_ids"])
    for m in d["recent"]:
        we_home = m["home_club_id"] == d["club_id"]
        venue = "H" if we_home else "A"
        opp = m["away_club_name"] if we_home else m["home_club_name"]
        r = result_for(m["result"], m["result_applied_to"], target_set)
        url = play_cricket_match_url(m["match_id"])
        parts.append(f"<tr><td><a href='{url}'>{_esc(m['match_date'])}</a></td>"
                     f"<td>{_esc(m['competition_type'])}</td>"
                     f"<td>{venue}</td>"
                     f"<td class='l'>{_esc(opp)}</td>"
                     f"<td><span class='pill {r}'>{r}</span></td>"
                     f"<td class='l score-cell'>{_esc(innings_inline(m))}</td></tr>")
    parts.append("</table>")

    # 6. Charts
    parts.append("<h2>Patterns</h2>")

    parts.append("<h3>Bat 1st vs Bat 2nd (last 3 seasons L+C)</h3>")
    parts.append(wld_chart_block([
        (f"{d['club_name']} bat 1st", d["chart_bat"]["us_bat1"]),
        (f"{d['club_name']} bat 2nd", d["chart_bat"]["us_bat2"]),
        (f"{d['vs_club_name']} bat 1st", d["chart_bat"]["vs_bat1"]),
        (f"{d['vs_club_name']} bat 2nd", d["chart_bat"]["vs_bat2"]),
    ]))

    parts.append("<h3>Home vs Away (last 3 seasons L+C)</h3>")
    parts.append(wld_chart_block([
        (f"{d['club_name']} home", d["chart_ha"]["us_home"]),
        (f"{d['club_name']} away", d["chart_ha"]["us_away"]),
        (f"{d['vs_club_name']} home", d["chart_ha"]["vs_home"]),
        (f"{d['vs_club_name']} away", d["chart_ha"]["vs_away"]),
    ]))

    parts.append(f"<h3>{_esc(d['club_name'])} when winning the toss</h3>")
    t = d["chart_toss"]
    if t["won_n"]:
        bat_pct = 100 * t["chose_bat_n"] / t["won_n"]
        fld_pct = 100 * t["chose_field_n"] / t["won_n"]
        parts.append(f"<p class='small'>Won toss in <b>{t['won_n']}</b> matches "
                     f"&middot; chose to <b>bat</b> {t['chose_bat_n']} "
                     f"({bat_pct:.0f}%), chose to <b>field</b> "
                     f"{t['chose_field_n']} ({fld_pct:.0f}%).</p>")
        # Choice split bar (just two segments)
        parts.append(
            "<div class='bar-row'>"
            "<div class='lbl'>Toss-win choice</div>"
            "<div class='bar'>"
            f"<div class='seg' style='width:{bat_pct:.1f}%;background:#2563b8'>"
            f"Bat {t['chose_bat_n']}</div>"
            f"<div class='seg' style='width:{fld_pct:.1f}%;background:#caa84e'>"
            f"Field {t['chose_field_n']}</div>"
            "</div>"
            f"<div class='num'>n={t['won_n']}</div></div>"
        )
        # Outcome by choice
        parts.append(wld_chart_block([
            ("Won toss → batted", t["chose_bat_outcome"]),
            ("Won toss → fielded", t["chose_field_outcome"]),
            ("Lost toss (ref)", t["lost_outcome"]),
        ]))
    else:
        parts.append("<p class='small'>No toss data in scope.</p>")

    parts.append("<h3>Team batting & bowling avg per season (1st XI L+C)</h3>")
    parts.append(avg_chart_block(
        d["last_n_seasons"], d["us_avgs"], d["vs_avgs"],
        d["club_name"], d["vs_club_name"]))

    # 7. Links
    parts.append("<h2>Web / video links</h2>")
    parts.append("<p class='small' style='background:#fff7d4;padding:8px 10px;"
                 "border-radius:4px;border:1px solid #e0c46c'>"
                 "⚠️ <b>Speculative.</b> The Play-Cricket API doesn't expose "
                 "match video URLs, and there's no API for finding the right "
                 "YouTube channel for a given club. Anything tagged "
                 "<i>(speculative)</i> is a name-based guess and may be the "
                 "wrong entity. <b>Verify before sharing.</b> Per-match search "
                 "probes are one-click YouTube searches scoped to the fixture "
                 "— typical hits are full-match livestreams (often a few hours "
                 "long) hosted by the home club's channel.</p>")
    by_kind = {"official": [], "video": [], "probe": [], "speculative": []}
    for label, url, kind in d["video_links"]:
        by_kind.setdefault(kind, []).append((label, url))

    if by_kind["official"]:
        parts.append("<h3>Official / club channels</h3>")
        parts.append("<ul class='bullets'>")
        for lbl, url in by_kind["official"]:
            parts.append(f"<li><a href='{_esc(url)}'>{_esc(lbl)}</a></li>")
        parts.append("</ul>")
    if by_kind["video"]:
        parts.append("<h3>Verified match videos</h3>")
        parts.append("<ul class='bullets'>")
        for lbl, url in by_kind["video"]:
            parts.append(f"<li><a href='{_esc(url)}'>{_esc(lbl)}</a></li>")
        parts.append("</ul>")
    if by_kind["probe"]:
        parts.append("<h3>Per-match YouTube search probes</h3>")
        parts.append("<p class='small'>One click per recent fixture — opens "
                     "YouTube search results. Look for long videos (multi-hour "
                     "livestreams) posted by the opposition's channel.</p>")
        parts.append("<ul class='bullets'>")
        for lbl, url in by_kind["probe"]:
            parts.append(f"<li><a href='{_esc(url)}'>{_esc(lbl)}</a></li>")
        parts.append("</ul>")
    if by_kind["speculative"]:
        parts.append("<h3>Speculative — name match only, NOT verified</h3>")
        parts.append("<ul class='bullets'>")
        for lbl, url in by_kind["speculative"]:
            parts.append(f"<li><a href='{_esc(url)}'>{_esc(lbl)}</a> "
                         f"<span class='small'>(speculative)</span></li>")
        parts.append("</ul>")

    parts.append(f"<p class='footer'>Generated {_esc(d['today'])} from "
                 f"<code>stats/data/rainham.db</code>. Source: Play-Cricket public API.</p>")
    parts.append("</body></html>")
    return "\n".join(parts)


def wld_chart_block(rows):
    """rows: [(label, counts_dict), ...]. Returns an HTML string with one
    bar-row per pair. Counts are (W/L/D/T/A/NR)."""
    parts = ["<div class='chart'>"]
    for label, counts in rows:
        p = counts.get("P", 0)
        if not p:
            parts.append(f"<div class='bar-row'><div class='lbl'>{_esc(label)}</div>"
                         f"<div class='bar'></div>"
                         f"<div class='num'>—</div></div>")
            continue
        segs = []
        for k in ("W","D","L","T","A","NR"):
            n = counts.get(k, 0)
            if n <= 0: continue
            pct = 100 * n / p
            display = k if pct > 12 else ""
            segs.append(f"<div class='seg {k}' style='width:{pct:.1f}%'>{n}{display}</div>")
        w = counts.get("W", 0); l = counts.get("L", 0)
        d = counts.get("D", 0) + counts.get("T", 0)
        nr = counts.get("NR", 0) + counts.get("A", 0)
        win_pct = 100 * w / p if p else 0
        nb = f"{w}W / {l}L"
        if d: nb += f" / {d}D"
        if nr: nb += f" / {nr}NR"
        nb += f" · {win_pct:.0f}%"
        parts.append(f"<div class='bar-row'>"
                     f"<div class='lbl'>{_esc(label)}</div>"
                     f"<div class='bar'>{''.join(segs)}</div>"
                     f"<div class='num'>{nb}</div></div>")
    parts.append("</div>")
    return "".join(parts)


def avg_chart_block(seasons, us_avgs, vs_avgs, us_name, vs_name):
    """Two side-by-side bars per season: Us bat-avg vs Vs bat-avg, Us bowl-avg
    vs Vs bowl-avg."""
    # Find max for scaling
    all_vals = []
    for s in seasons:
        for d_ in (us_avgs.get(s, {}), vs_avgs.get(s, {})):
            for k in ("bat_avg", "bowl_avg"):
                v = d_.get(k)
                if v is not None: all_vals.append(v)
    max_val = max(all_vals) if all_vals else 50.0
    max_val = max(max_val * 1.1, 30)

    parts = []
    parts.append("<table style='font-size:11px'>")
    parts.append(f"<tr><th>Yr</th>"
                 f"<th class='l'>Bat avg ({_esc(us_name)} vs {_esc(vs_name)})</th>"
                 f"<th class='l'>Bowl avg ({_esc(us_name)} vs {_esc(vs_name)})</th></tr>")
    for s in seasons:
        u = us_avgs.get(s, {})
        v = vs_avgs.get(s, {})
        ub = u.get("bat_avg"); vb = v.get("bat_avg")
        ubo = u.get("bowl_avg"); vbo = v.get("bowl_avg")
        bat_html = mini_double_bar(ub, vb, max_val)
        bowl_html = mini_double_bar(ubo, vbo, max_val)
        parts.append(f"<tr><td>{s}</td>"
                     f"<td class='l'>{bat_html}</td>"
                     f"<td class='l'>{bowl_html}</td></tr>")
    parts.append("</table>")
    parts.append(f"<p class='small'>Blue = {_esc(us_name)} · Red = {_esc(vs_name)}</p>")
    return "".join(parts)


def mini_double_bar(us_val, vs_val, max_val):
    """Two narrow stacked bars - one for us, one for vs. Returns HTML."""
    def bar(val, color_class):
        if val is None or max_val <= 0:
            return f"<div style='height:10px;background:#eee;border-radius:2px;margin:1px 0'></div>"
        pct = min(100, 100 * val / max_val)
        return (f"<div style='height:10px;background:#eee;border-radius:2px;margin:1px 0;position:relative'>"
                f"<div style='position:absolute;left:0;top:0;bottom:0;width:{pct:.1f}%;"
                f"background:{color_class};border-radius:2px;display:flex;"
                f"align-items:center;justify-content:flex-end;padding-right:4px;"
                f"color:#fff;font-size:9.5px;font-weight:600'>{val:.1f}</div></div>")
    return f"<div>{bar(us_val,'#2563b8')}{bar(vs_val,'#c03030')}</div>"


if __name__ == "__main__":
    raise SystemExit(main())
