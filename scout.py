#!/usr/bin/env python3
"""
Generic 1st-XI scouting report. League + Cup focus throughout.

Usage:
  python3 stats/scout.py --club-name "Spartans"
  python3 stats/scout.py --club-id 14366 --season 2026
  python3 stats/scout.py --club-name "Wickford" --vs-club-id 5251

Writes (versioned — each rebuild on the same day creates a new vN folder
unless --version is passed explicitly):
  stats/reports/<YYYY-MM-DD>/<slug>/v<N>/scout.md
  stats/reports/<YYYY-MM-DD>/<slug>/v<N>/scout.html
  stats/reports/<YYYY-MM-DD>/<slug>/v<N>/scout.png   ← long mobile PNG
  stats/reports/<YYYY-MM-DD>/<slug>/latest           → v<N>  (symlink)

Sections (1st XI only, League + Cup unless otherwise noted):
  0. Current league table (with form)
  1. Last 3 seasons finishing positions (League only)
  2. Top run scorers (last 3 seasons)
  3. Top wicket takers (last 3 seasons)
  4. Head-to-head vs RCC 1st XI (any era in cache)
  5. Last 20 1st XI played matches (with scores + Play-Cricket links)
  6. Charts:
       - W/L/D when batting first vs second  (vs RCC 1st XI baseline)
       - W/L/D home vs away                  (vs RCC 1st XI baseline)
       - W/L/D when winning the toss
       - Team batting & bowling averages per season vs RCC
  7. Web links (Play-Cricket, club site, video search probes)

Long PNG:
  Rendered from the generated scout.html via headless Chromium so the
  report can be shared as a single mobile-friendly image (e.g. WhatsApp).
  Pass --no-png to skip if Chromium isn't available.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "rainham.db"
RAW_DIR = ROOT / "data" / "raw"
LEAGUE_TABLE_DIR = RAW_DIR / "league_table"
REPORTS_DIR = ROOT / "reports"
SCOUTING_DIR = REPORTS_DIR / "scouting"

# Headless-Chromium binary — used for rendering scout.html → scout.png.
# We look at the env var first, then a couple of well-known paths.
CHROMIUM_CANDIDATES = [
    os.environ.get("CHROMIUM_BINARY") or "",
    "/opt/pw-browsers/chromium",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
]

RAINHAM_CLUB_ID = "5251"
RAINHAM_FIRST_XI_TEAM_ID = "51207"   # Rainham CC, Essex - 1st XI
RAINHAM_NAME = "Rainham CC"
RAINHAM_PC_SUBDOMAIN = "rainhamcc"
PC_DEFAULT_RULE_TYPE = 179

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


def _player_link(subdomain, player_id, kind, label):
    """Return an `<a>` tag for the player's Play-Cricket profile, or just
    the escaped label if we can't build one (no subdomain / no id)."""
    url = player_pc_url(subdomain, player_id, kind=kind) if subdomain else None
    safe = _esc(label)
    if not url or not player_id:
        return safe
    return f"<a href='{_esc(url)}' target='_blank' rel='noopener'>{safe}</a>"


def _perf_bat_html(subdomain, b):
    """Render one batter line for the §5 match strip: `Smith 89 (60b, #4)`."""
    name = _player_link(subdomain, b.get("id"), "batting",
                        b.get("name") or "?")
    runs = b.get("runs", 0)
    star = "*" if (b.get("how_out") or "").lower() in NOT_OUT else ""
    pos = b.get("pos")
    balls = b.get("balls")
    extras = []
    if balls is not None and balls != "":
        extras.append(f"{balls}b")
    if pos is not None:
        extras.append(f"#{pos}")
    extra = f" <span class='x'>({', '.join(extras)})</span>" if extras else ""
    return f"<span class='who'>{name}</span> <b>{runs}{star}</b>{extra}"


def _perf_bowl_html(subdomain, b):
    """`Khan 5/22 (10ov)`."""
    name = _player_link(subdomain, b.get("id"), "bowling",
                        b.get("name") or "?")
    w = b.get("wickets", 0); r = b.get("runs", 0)
    overs = b.get("overs") or ""
    ext = f" <span class='x'>({overs}ov)</span>" if overs else ""
    return f"<span class='who'>{name}</span> <b>{w}/{r}</b>{ext}"


def _player_md_link(subdomain, player_id, kind, label):
    url = player_pc_url(subdomain, player_id, kind=kind) if subdomain else None
    if not url or not player_id:
        return label or "?"
    return f"[{label or '?'}]({url})"


def _perf_bat_md(subdomain, b):
    name = _player_md_link(subdomain, b.get("id"), "batting",
                           b.get("name") or "?")
    star = "*" if (b.get("how_out") or "").lower() in NOT_OUT else ""
    bits = []
    if b.get("balls") not in (None, "", 0):
        bits.append(f"{b['balls']}b")
    if b.get("pos") is not None:
        bits.append(f"#{b['pos']}")
    extra = f" ({', '.join(bits)})" if bits else ""
    return f"{name} **{b.get('runs', 0)}{star}**{extra}"


def _perf_bowl_md(subdomain, b):
    name = _player_md_link(subdomain, b.get("id"), "bowling",
                           b.get("name") or "?")
    overs = b.get("overs") or ""
    extra = f" ({overs}ov)" if overs else ""
    return f"{name} **{b.get('wickets', 0)}/{b.get('runs', 0)}**{extra}"


def _date_short(date_ddmmyyyy):
    """'12/07/2025' → "Jul '25" (just month + year — enough to anchor a
    sample range without overflowing a narrow cell)."""
    if not date_ddmmyyyy or len(date_ddmmyyyy) < 10:
        return date_ddmmyyyy or ""
    months = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    try:
        mo = int(date_ddmmyyyy[3:5])
        y = date_ddmmyyyy[8:10]
        return f"{months[mo]} '{y}"
    except (ValueError, IndexError):
        return date_ddmmyyyy


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


# ------------------------------------------------ match-detail / matrix --

def fetch_match_perf(conn, match_id, club_id):
    """Pull the scouted club's batting + bowling performance in this match.

    Returns a dict {top_bat, fifty_plus, top_bowl, three_plus} where each
    of top_bat/top_bowl is a single row (or None) and the *_plus lists
    contain anyone else who hit the bonus threshold (50+ runs / 3+
    wickets) but wasn't the top performer."""
    bat_rows = conn.execute("""
        SELECT batsman_id, batsman_name, position,
               coalesce(runs,0), balls, how_out
        FROM batting
        WHERE match_id=? AND team_batting_club_id=?
          AND lower(coalesce(how_out,'')) NOT IN ('did not bat','absent')
        ORDER BY coalesce(runs,0) DESC, position ASC
    """, (match_id, club_id)).fetchall()
    bowl_rows = conn.execute("""
        SELECT bowler_id, bowler_name, overs,
               coalesce(maidens,0), coalesce(runs,0), coalesce(wickets,0)
        FROM bowling
        WHERE match_id=? AND team_bowling_club_id=?
        ORDER BY coalesce(wickets,0) DESC, coalesce(runs,0) ASC
    """, (match_id, club_id)).fetchall()

    top_bat, fifty_plus = None, []
    if bat_rows:
        b = bat_rows[0]
        top_bat = {
            "id": b[0], "name": b[1], "pos": b[2],
            "runs": b[3], "balls": b[4], "how_out": b[5],
        }
        for r in bat_rows[1:]:
            if (r[3] or 0) >= 50:
                fifty_plus.append({
                    "id": r[0], "name": r[1], "pos": r[2],
                    "runs": r[3], "balls": r[4], "how_out": r[5],
                })

    # Filter out empty bowling-card rows (no overs / runs / wickets / maidens)
    bowled = [r for r in bowl_rows
              if overs_to_balls(r[2]) or r[3] or r[4] or r[5]]
    top_bowl, three_plus = None, []
    if bowled:
        b = bowled[0]
        top_bowl = {
            "id": b[0], "name": b[1], "overs": b[2],
            "maidens": b[3], "runs": b[4], "wickets": b[5],
        }
        for r in bowled[1:]:
            if (r[5] or 0) >= 3:
                three_plus.append({
                    "id": r[0], "name": r[1], "overs": r[2],
                    "maidens": r[3], "runs": r[4], "wickets": r[5],
                })
    return {
        "top_bat": top_bat, "fifty_plus": fifty_plus,
        "top_bowl": top_bowl, "three_plus": three_plus,
    }


def first_innings_matrix(conn, club_id, team_ids, today_iso, comp_types=("League","Cup")):
    """Return a 2x2 dict for (bat_first|bowl_first) × (home|away) of the
    last `n` matches each, with avg/median first-innings totals and the
    date range that those samples cover.

    We only look at the scouted team's 1st XI (team_ids), include
    League + Cup played matches up to today_iso, and pull
    `innings.runs WHERE innings_seq=1` to get the first-innings total.
    """
    if not team_ids:
        return None
    t_ph = ",".join("?" * len(team_ids))
    c_ph = ",".join("?" * len(comp_types))
    rows = conn.execute(f"""
        SELECT m.match_id, m.match_date,
               m.home_club_id, m.batted_first,
               (SELECT i.runs FROM innings i
                 WHERE i.match_id=m.match_id AND i.innings_seq=1) AS r1
        FROM matches m
        WHERE (m.home_team_id IN ({t_ph}) OR m.away_team_id IN ({t_ph}))
          AND m.competition_type IN ({c_ph})
          AND m.match_date <> '' AND m.result <> ''
          AND substr(m.match_date,7,4)||substr(m.match_date,4,2)||
              substr(m.match_date,1,2) <= ?
        ORDER BY substr(m.match_date,7,4)||substr(m.match_date,4,2)||
                 substr(m.match_date,1,2) DESC
    """, (*team_ids, *team_ids, *comp_types, today_iso)).fetchall()

    target = set(team_ids)
    buckets = {("bat", "home"): [], ("bat", "away"): [],
               ("bowl", "home"): [], ("bowl", "away"): []}
    for mid, mdate, hcid, bf, r1 in rows:
        bf = (bf or "").strip()
        if not bf:
            continue
        venue = "home" if hcid == club_id else "away"
        side = "bat" if bf in target else "bowl"
        buckets[(side, venue)].append((mdate, r1))

    LAST = 10
    out = {}
    for k, items in buckets.items():
        # Take the last LAST played items (we already DESC'd), keep only
        # those that have a recorded first-innings total.
        sample = [(d, r) for d, r in items[:LAST] if r is not None]
        if not sample:
            out[k] = None
            continue
        runs = sorted(r for _, r in sample)
        n = len(runs)
        avg = sum(runs) / n
        if n % 2:
            med = runs[n // 2]
        else:
            med = (runs[n // 2 - 1] + runs[n // 2]) / 2
        # Date range: dd/mm/yyyy strings — sort by ISO (yyyymmdd) so
        # "06/09/2025" doesn't appear earlier than "30/08/2025".
        dates = sorted((d for d, _ in sample), key=to_iso)
        out[k] = {
            "n": n, "avg": avg, "median": med,
            "first": dates[0], "last": dates[-1],
        }
    return out


def chase_matrix(conn, club_id, team_ids, today_iso,
                 comp_types=("League","Cup"), last=10):
    """For each played match where the result is W or L (decisive), bucket
    by who was chasing (us / opp) × Spartans' venue (home / away) ×
    outcome (won / lost), then keep the last `last` of each.

    Returns dict keyed by (chaser, venue, outcome) where:
      - chaser: 'us' if our 1st XI batted second, 'opp' otherwise
      - venue:  'home' / 'away' (always Spartans' venue)
      - outcome: 'won' if the chaser succeeded, 'lost' if they failed

    Each entry:
      - won  → {n, avg_target, first, last}        (target chased down)
      - lost → {n, avg_score, avg_target, first, last}
               (chaser fell short — scored avg_score vs avg_target)
    """
    if not team_ids:
        return None
    t_ph = ",".join("?" * len(team_ids))
    c_ph = ",".join("?" * len(comp_types))
    rows = conn.execute(f"""
        SELECT m.match_id, m.match_date, m.home_club_id, m.batted_first,
               m.result, m.result_applied_to,
               (SELECT i.runs FROM innings i
                 WHERE i.match_id=m.match_id AND i.innings_seq=1) AS r1,
               (SELECT i.runs FROM innings i
                 WHERE i.match_id=m.match_id AND i.innings_seq=2) AS r2
        FROM matches m
        WHERE (m.home_team_id IN ({t_ph}) OR m.away_team_id IN ({t_ph}))
          AND m.competition_type IN ({c_ph})
          AND m.match_date <> '' AND m.result <> ''
          AND substr(m.match_date,7,4)||substr(m.match_date,4,2)||
              substr(m.match_date,1,2) <= ?
        ORDER BY substr(m.match_date,7,4)||substr(m.match_date,4,2)||
                 substr(m.match_date,1,2) DESC
    """, (*team_ids, *team_ids, *comp_types, today_iso)).fetchall()

    target = set(team_ids)
    buckets = {(c, v, o): []
               for c in ("us", "opp")
               for v in ("home", "away")
               for o in ("won", "lost")}

    for mid, mdate, hcid, bf, res, rat, r1, r2 in rows:
        if r1 is None or r2 is None:
            continue
        bf = (bf or "").strip()
        if not bf or res not in ("W", "L"):
            continue
        venue = "home" if hcid == club_id else "away"
        we_batted_first = bf in target
        we_won = (res == "W" and rat in target) or \
                 (res == "L" and rat and rat not in target)
        we_lost = not we_won

        if we_batted_first:
            # Opposition was chasing our R1
            chaser = "opp"
            target_runs, chaser_score = r1, r2
            chaser_won = we_lost
        else:
            # We were chasing their R1
            chaser = "us"
            target_runs, chaser_score = r1, r2
            chaser_won = we_won

        outcome = "won" if chaser_won else "lost"
        buckets[(chaser, venue, outcome)].append((mdate, target_runs, chaser_score))

    out = {}
    for k, items in buckets.items():
        sample = items[:last]
        if not sample:
            out[k] = None
            continue
        n = len(sample)
        dates = sorted((d for d, _, _ in sample), key=to_iso)
        if k[2] == "won":
            tgts = [t for _, t, _ in sample]
            out[k] = {"n": n, "avg_target": sum(tgts) / n,
                      "first": dates[0], "last": dates[-1]}
        else:
            tgts = [t for _, t, _ in sample]
            scores = [s for _, _, s in sample]
            out[k] = {"n": n,
                      "avg_target": sum(tgts) / n,
                      "avg_score": sum(scores) / n,
                      "first": dates[0], "last": dates[-1]}
    return out


# ---------------------------------------------------------------- main ----

def play_cricket_match_url(match_id):
    return f"https://play-cricket.com/website/results/{match_id}"


def player_pc_url(subdomain, player_id, kind="batting",
                  rule_type_id=PC_DEFAULT_RULE_TYPE):
    """Build a Play-Cricket player profile URL.

    Example: https://spartansessex.play-cricket.com/player_stats/batting/
             4908218?rule_type_id=179
    `kind` is 'batting' or 'bowling'."""
    if not subdomain or not player_id:
        return None
    return (f"https://{subdomain}.play-cricket.com/player_stats/"
            f"{kind}/{player_id}?rule_type_id={rule_type_id}")


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--club-id")
    ap.add_argument("--club-name")
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--seasons-back", type=int, default=3)
    ap.add_argument("--last-recent", type=int, default=20,
                    help="How many recent matches to list (also drives the "
                    "per-match video search probes). Default: 20.")
    ap.add_argument("--vs-club-id", default=RAINHAM_CLUB_ID,
                    help="Comparison club (default: Rainham 5251).")
    ap.add_argument("--today", default=None,
                    help="Override today's date (yyyy-mm-dd).")
    ap.add_argument("--version", type=int, default=None,
                    help="Force this version number (overwrites). Default: "
                    "auto-increment within the day.")
    ap.add_argument("--no-png", action="store_true",
                    help="Skip the long-PNG render (faster, no Chromium).")
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

    # ---- Section 5: last N 1st XI played L+C with scores ----
    recent = matches[:args.last_recent]   # already DESC
    # Decorate each with innings totals + per-side performance summary
    # (top scorer + 50+ supporting innings; top wicket-taker + 3wi+).
    for m in recent:
        m["innings_lines"] = fetch_innings_summary(conn, m["match_id"])
        m["perf"] = fetch_match_perf(conn, m["match_id"], club_id)

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

    # 6e. First-innings matrix (bat/bowl first × home/away, last 10 each)
    fi_matrix = first_innings_matrix(conn, club_id, target_team_ids, today_iso)
    # 6f. Chase matrix (us/opp chasing × home/away × won/lost, last 10 each)
    ch_matrix = chase_matrix(conn, club_id, target_team_ids, today_iso)

    # Play-Cricket subdomain for player profile links (used in §2/§3/§5)
    pc_subdomain = (CLUB_LINKS.get(str(club_id), {}) or {}).get("pc_subdomain")

    # ---- Build report data ----
    data = {
        "club_name": club_name,
        "club_id": club_id,
        "vs_club_name": vs_club_name,
        "vs_pc_subdomain": RAINHAM_PC_SUBDOMAIN,
        "pc_subdomain": pc_subdomain,
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
        "fi_matrix": fi_matrix,
        "ch_matrix": ch_matrix,
        "video_links": video_links_for(club_id, club_name, recent_matches=recent),
    }

    # ---- Output paths (versioned per-day) ----
    club_dir = SCOUTING_DIR / today / slugify(club_name)
    club_dir.mkdir(parents=True, exist_ok=True)
    version = args.version if args.version is not None else next_version(club_dir)
    out_dir = club_dir / f"v{version}"
    out_dir.mkdir(parents=True, exist_ok=True)
    data["version"] = version

    md_path = out_dir / "scout.md"
    html_path = out_dir / "scout.html"
    png_path = out_dir / "scout.png"

    md_path.write_text(render_md(data))
    html_path.write_text(render_html(data))
    print(f"Wrote {md_path}")
    print(f"Wrote {html_path}")

    # Long-PNG render (mobile-friendly screenshot of the whole page).
    if not args.no_png:
        try:
            render_long_png(html_path, png_path)
            print(f"Wrote {png_path}")
        except Exception as e:
            print(f"PNG render skipped: {e}", file=sys.stderr)

    # Update `latest` symlink so consumers can find the most recent version.
    update_latest_symlink(club_dir, f"v{version}")

    # Always refresh the top-level index so the new report appears in it.
    try:
        import build_index
        build_index.build()
    except Exception as e:
        print(f"index refresh skipped: {e}", file=sys.stderr)
    return 0


# --------------------------------------------------------------- versioning --

def next_version(club_dir: Path) -> int:
    """Return the next vN integer not already present in `club_dir`."""
    used = []
    if club_dir.exists():
        for p in club_dir.iterdir():
            if p.is_dir() and re.fullmatch(r"v\d+", p.name):
                try:
                    used.append(int(p.name[1:]))
                except ValueError:
                    pass
    return (max(used) + 1) if used else 1


def update_latest_symlink(club_dir: Path, target: str):
    """(Re)create `<club_dir>/latest` pointing at `target` (e.g. 'v3').

    On filesystems that don't support symlinks we silently fall back to a
    plain text file naming the latest version."""
    link = club_dir / "latest"
    try:
        if link.is_symlink() or link.exists():
            try:
                link.unlink()
            except IsADirectoryError:
                shutil.rmtree(link)
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        try:
            link.write_text(target + "\n")
        except Exception:
            pass


# ---------------------------------------------------------------- PNG render --

def find_chromium() -> str | None:
    for c in CHROMIUM_CANDIDATES:
        if c and Path(c).exists():
            return c
    found = shutil.which("chromium") or shutil.which("chromium-browser") \
        or shutil.which("google-chrome")
    return found


def _measure_page_height(chromium: str, html_url: str) -> int:
    """Run a quick `--dump-dom` pass to read document.body.scrollHeight that
    the report writes into <html data-render-h="…"> on load."""
    try:
        out = subprocess.run(
            [chromium, "--headless=new", "--no-sandbox", "--disable-gpu",
             "--hide-scrollbars", "--window-size=540,200",
             "--virtual-time-budget=3000", "--dump-dom", html_url],
            capture_output=True, text=True, timeout=60, check=False,
        )
        m = re.search(r'data-render-h="(\d+)"', out.stdout)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    # Fallback: a generous default. Chromium will fill any unused space
    # with the body background — not ideal but at least the report is
    # captured in full.
    return 12000


def render_long_png(html_path: Path, png_path: Path):
    """Render `html_path` to `png_path` as a single tall PNG via headless
    Chromium. Width is fixed at 540 px (matches the HTML's max-width)."""
    chromium = find_chromium()
    if not chromium:
        raise RuntimeError(
            "no chromium binary found (set $CHROMIUM_BINARY or install "
            "playwright's chromium)"
        )
    url = f"file://{html_path.resolve()}"
    height = _measure_page_height(chromium, url)
    # +30 px breathing room.
    height = max(800, height + 30)
    width = 540
    subprocess.run(
        [chromium, "--headless=new", "--no-sandbox", "--disable-gpu",
         "--hide-scrollbars", f"--window-size={width},{height}",
         "--virtual-time-budget=4000",
         f"--screenshot={png_path}", url],
        check=True, capture_output=True, timeout=120,
    )
    if not png_path.exists() or png_path.stat().st_size < 200:
        raise RuntimeError("chromium produced no usable PNG output")


# ---------------------------------------------------------------- video links --

# Hard-coded official / verified links. The 'verified' list has been
# eyeballed (or the user has supplied them). The 'speculative' list is
# from name-based web searches and may not be the right entity.
CLUB_LINKS = {
    "14366": {  # Spartans CC, Essex
        "pc_subdomain": "spartansessex",
        "official": [
            ("Play-Cricket club page", "https://spartansessex.play-cricket.com/home"),
            ("Club website", "https://www.thespartanscricketclub.com/"),
            ("YouTube channel (@SpartansCC_Essex)",
             "https://www.youtube.com/@SpartansCC_Essex"),
            ("Instagram (@spartanscricket)", "https://www.instagram.com/spartanscricket/"),
        ],
        # Verified by Claude on 2026-05-04 by walking the @SpartansCC_Essex
        # channel + opposition channels and matching titles + livestream
        # length + stream date against each fixture in section 5.
        # NB: @essexspartans8127 (American Football club) was previously
        # listed as speculative — confirmed wrong sport and removed.
        "verified_videos": [
            ("H v Essex Lions CC (Cup, 06/09/2025)",
             "https://youtu.be/01XHP9sG13U"),
            ("H v Aztecs CC, Ilford (League, 30/08/2025)",
             "https://youtu.be/C75WRtZvlCs"),
            ("A v London Avengers CC (Cup, 28/08/2025)",
             "https://youtu.be/rvIf-e9u7UE"),
            ("A v Millwall Stars CC (League, 23/08/2025)",
             "https://youtu.be/UESK7IG6waI"),
            ("A v Roding Valley CC (League, 16/08/2025)",
             "https://youtu.be/LGpeN00Ib0E"),
            ("H v Tower Hamlets CC (League, 09/08/2025)",
             "https://youtu.be/rvEImb3YABM"),
            ("A v Waltham Forest CC (League, 02/08/2025)",
             "https://youtu.be/dQcCTtS9Yg8"),
            ("H v Goodmayes United CC (Cup, 28/07/2025)",
             "https://youtu.be/1ONSvioDkas"),
            ("A v Aztecs CC, Ilford (League, 26/07/2025)",
             "https://youtu.be/7VeroHGmFA8"),
            ("H v Roding Valley CC (League, 12/07/2025)",
             "https://youtu.be/Juz8Hc5fK4M"),
            ("A v Tower Hamlets CC (League, 05/07/2025)",
             "https://youtu.be/HSSCw88FcKs"),
            ("H v Essex Lions CC (League, 28/06/2025)",
             "https://youtu.be/swP8mS6ETKA"),
            ("H v Neo CC (Cup, 25/06/2025)",
             "https://youtu.be/DvhbuC5oLY0"),
            ("A v Neo CC (League, 14/06/2025)",
             "https://youtu.be/6fyq21wz4zg"),
            ("A v Essex Lions CC (League, 31/05/2025)",
             "https://youtu.be/igN7DG5cA9I"),
            ("A v Victoria Park CC (Cup, 26/05/2025)",
             "https://youtu.be/e62uZGAZbME"),
        ],
    },
    "6909": {  # Wickford CC
        "pc_subdomain": "wickford",
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
    """Return ([(label, url, kind), ...]) where kind is 'official', 'video'
    or 'speculative'.

    Note: this function deliberately does NOT emit per-match YouTube
    *search* links. Surfacing the actual playable video per fixture is a
    manual / AI step (see the "Hunt for match videos" section in
    CLAUDE.md) — the assistant runs the searches, opens the candidate
    results, and pastes any verified video URL into
    `CLUB_LINKS[club_id]["verified_videos"]` before re-running the
    report. Anything in `verified_videos` ends up here as kind='video';
    anything in `speculative` ends up as kind='speculative'."""
    out = []
    info = CLUB_LINKS.get(str(club_id), {})

    for label, url in info.get("official", []):
        out.append((label, url, "official"))
    for label, url in info.get("verified_videos", []):
        out.append((label, url, "video"))
    for label, url in info.get("speculative", []):
        out.append((label, url, "speculative"))
    return out


# ---------------------------------------------------------------- markdown ---

def render_md(d):
    md = []
    version_str = (f" (v{d['version']})" if d.get("version") is not None else "")
    md.append(f"# Scout — {d['club_name']} 1st XI{version_str}")
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
    sub = d.get("pc_subdomain")
    for i, r in enumerate(d["top_batters"], 1):
        (bid, name, innings, no_, runs, hs, fifties, hundreds, balls_known,
         runs_when_balls, matches_, mode_pos) = r
        avg = f"{runs/(innings-no_):.2f}" if (innings-no_) > 0 else "—"
        sr = f"{100*runs_when_balls/balls_known:.1f}" if balls_known else "—"
        name = _player_md_link(sub, bid, "batting", name)
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
        nm = _player_md_link(sub, r["bowler_id"], "bowling", r["name"])
        md.append(f"| {i} | {nm} | {r['matches']} | {r['overs']} | "
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
    sub = d.get("pc_subdomain")
    target_set = set(d["target_team_ids"])
    for m in d["recent"]:
        we_home = m["home_club_id"] == d["club_id"]
        venue = "H" if we_home else "A"
        opp = m["away_club_name"] if we_home else m["home_club_name"]
        opp_short = re.sub(r",.*$", "", opp).strip()
        r = result_for(m["result"], m["result_applied_to"], target_set)
        scores = innings_inline(m)
        md.append(
            f"- **{r}** [{m['match_date']}]"
            f"({play_cricket_match_url(m['match_id'])}) "
            f"· {m['competition_type']} · {venue} vs **{opp_short}** "
            f"— {scores}"
        )
        perf = m.get("perf") or {}
        if perf.get("top_bat"):
            bits = [_perf_bat_md(sub, perf["top_bat"])]
            for x in perf.get("fifty_plus", []):
                bits.append(_perf_bat_md(sub, x))
            md.append(f"  - 🏏 " + " · ".join(bits))
        if perf.get("top_bowl") and perf["top_bowl"]["wickets"] is not None:
            bits = [_perf_bowl_md(sub, perf["top_bowl"])]
            for x in perf.get("three_plus", []):
                bits.append(_perf_bowl_md(sub, x))
            md.append(f"  - 🎯 " + " · ".join(bits))
    md.append("")

    # 6. Charts (text-only summary in MD; HTML has bars)
    md.append("## 6. Patterns")
    md.append("")

    fi = d.get("fi_matrix")
    if fi:
        md.append("### 6a. First-innings totals — last 10 of each quadrant")
        md.append("")
        md.append("_The runs the team batting first put up — own innings on "
                  "the **Bat 1st** row, opposition's on the **Bowl 1st** row. "
                  "Avg shown prominently, median in brackets._")
        md.append("")
        md.append("| | at Home | Away |")
        md.append("|---|---|---|")
        for side, label, sub_label in (
            ("bat",  "**Bat 1st**",  "own runs"),
            ("bowl", "**Bowl 1st**", "opp. runs"),
        ):
            row = [f"{label}<br>_{sub_label}_"]
            for venue in ("home", "away"):
                c = fi.get((side, venue))
                if not c:
                    row.append("—")
                else:
                    med = (f"{c['median']:.0f}" if isinstance(c['median'], float)
                            and c['median'].is_integer() else f"{c['median']}")
                    row.append(
                        f"**{c['avg']:.0f}** (med {med}) "
                        f"<br><sub>n={c['n']} · {_date_short(c['first'])} → "
                        f"{_date_short(c['last'])}</sub>"
                    )
            md.append("| " + " | ".join(row) + " |")
        md.append("")

    ch = d.get("ch_matrix")
    if ch:
        md.append("### 6b. Run chases — last 10 of each quadrant")
        md.append("")
        md.append("_Successful: the avg target the chaser knocked off. "
                  "Unsuccessful: scored X vs target Y._")
        md.append("")

        def chase_cell(chaser, venue, outcome):
            c = ch.get((chaser, venue, outcome))
            if not c:
                return "—"
            sub = (f"<br><sub>n={c['n']} · {_date_short(c['first'])} → "
                   f"{_date_short(c['last'])}</sub>")
            if outcome == "won":
                return f"chased **{c['avg_target']:.0f}**{sub}"
            gap = c['avg_target'] - c['avg_score']
            return (f"**{c['avg_score']:.0f}** vs **{c['avg_target']:.0f}** "
                    f"_({gap:.0f} short)_{sub}")

        for chaser, header in (
            ("us",  f"**{d['club_name']} chasing** _(they batted second)_"),
            ("opp", f"**Opposition chasing** _({d['club_name']} batted first)_"),
        ):
            md.append(f"#### {header}")
            md.append("")
            md.append("| | at Home | Away |")
            md.append("|---|---|---|")
            md.append(f"| **Made**<br>_chased it_ "
                      f"| {chase_cell(chaser, 'home', 'won')} "
                      f"| {chase_cell(chaser, 'away', 'won')} |")
            md.append(f"| **Failed**<br>_fell short_ "
                      f"| {chase_cell(chaser, 'home', 'lost')} "
                      f"| {chase_cell(chaser, 'away', 'lost')} |")
            md.append("")

    md.append("### 6c. When batting first vs second (last 3 seasons L+C)")
    md.append("")
    md.append(f"- {d['club_name']} batting 1st: " + wld_str(d["chart_bat"]["us_bat1"]))
    md.append(f"- {d['club_name']} batting 2nd: " + wld_str(d["chart_bat"]["us_bat2"]))
    md.append(f"- {d['vs_club_name']} batting 1st: " + wld_str(d["chart_bat"]["vs_bat1"]))
    md.append(f"- {d['vs_club_name']} batting 2nd: " + wld_str(d["chart_bat"]["vs_bat2"]))
    md.append("")

    md.append("### 6d. Home vs away (last 3 seasons L+C)")
    md.append("")
    md.append(f"- {d['club_name']} at home: " + wld_str(d["chart_ha"]["us_home"]))
    md.append(f"- {d['club_name']} away: " + wld_str(d["chart_ha"]["us_away"]))
    md.append(f"- {d['vs_club_name']} at home: " + wld_str(d["chart_ha"]["vs_home"]))
    md.append(f"- {d['vs_club_name']} away: " + wld_str(d["chart_ha"]["vs_away"]))
    md.append("")

    md.append("### 6e. When they win the toss")
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

    md.append("### 6f. Team batting & bowling avg per season (1st XI, L+C)")
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
    by_kind = {"official": [], "video": [], "speculative": []}
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
    else:
        md.append("_No match videos verified yet for this club. The "
                  "assistant will loop the fixtures in section 5 (curl "
                  "the YouTube search for each, judge the top hits, and "
                  "paste any verified video into "
                  "`CLUB_LINKS[\"<club_id>\"][\"verified_videos\"]` in "
                  "`scout.py`), then re-run the report. See "
                  "`CLAUDE.md → \"Hunt for match videos\"`._")
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
:root{
  --bg:#f3f4f8;
  --card:#ffffff;
  --ink:#10172a;
  --muted:#5a657a;
  --line:#e6e9f0;
  --line-2:#d4d9e4;
  --accent:#1d4ed8;
  --accent-2:#0ea5a5;
  --us:#1d4ed8;
  --them:#b91c1c;
  --w:#15803d;
  --l:#b91c1c;
  --d:#6b7280;
  --nr:#b08a2e;
  --hi:#fff7d6;
  --shadow:0 1px 2px rgba(16,23,42,.04),0 4px 14px rgba(16,23,42,.06);
}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{padding:12px 12px 24px;font-family:-apple-system,BlinkMacSystemFont,
     'Segoe UI',Inter,Roboto,'Helvetica Neue',Arial,sans-serif;
     font-size:14px;line-height:1.4;color:var(--ink);
     background:linear-gradient(180deg,#eef1f7 0,#f3f4f8 240px);
     max-width:540px;margin:0 auto;
     -webkit-font-smoothing:antialiased}

/* Hero header */
.hero{background:linear-gradient(135deg,#0f1f4a 0%,#1d4ed8 100%);
      color:#fff;border-radius:14px;padding:14px 16px 16px;
      box-shadow:var(--shadow);margin-bottom:14px;position:relative;
      overflow:hidden}
.hero::after{content:"";position:absolute;inset:0;background:
   radial-gradient(circle at 90% -10%,rgba(255,255,255,.18),transparent 50%);
   pointer-events:none}
.hero .eyebrow{font-size:10.5px;text-transform:uppercase;letter-spacing:.12em;
               opacity:.75;font-weight:600}
.hero h1{font-size:22px;margin:2px 0 4px;font-weight:800;line-height:1.15;
         letter-spacing:-.01em}
.hero .meta{font-size:11.5px;opacity:.85;margin:0}
.hero .stats{display:flex;gap:10px;margin-top:11px;flex-wrap:wrap}
.hero .stat{background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.18);
            border-radius:9px;padding:6px 10px;backdrop-filter:blur(2px);
            min-width:74px;flex:1}
.hero .stat .n{font-size:16px;font-weight:700;line-height:1}
.hero .stat .lbl{font-size:9.5px;text-transform:uppercase;letter-spacing:.08em;
                 opacity:.78;margin-top:3px}

/* Cards */
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
      padding:12px 14px;margin:0 0 12px;box-shadow:var(--shadow)}
.card > h2{margin-top:0}

h2{font-size:15px;margin:0 0 8px;padding:0 0 7px;
   border-bottom:1px solid var(--line);font-weight:700;letter-spacing:-.005em;
   color:var(--ink);display:flex;align-items:center;gap:8px}
h2 .num{display:inline-flex;align-items:center;justify-content:center;
        background:var(--accent);color:#fff;font-size:11px;font-weight:700;
        width:20px;height:20px;border-radius:6px;flex:0 0 20px}
h3{font-size:12.5px;margin:14px 0 6px;color:var(--muted);
   text-transform:uppercase;letter-spacing:.06em;font-weight:700}
p{font-size:13px;margin:4px 0 8px}
p.meta{color:var(--muted);margin:0 0 12px;font-size:12px}
.subtle{color:var(--muted);font-size:11.5px;margin:-2px 0 8px}

table{width:100%;border-collapse:collapse;margin:4px 0 4px;
      font-size:11.5px;font-variant-numeric:tabular-nums}
th,td{padding:5px 5px;border-bottom:1px solid var(--line);text-align:right;
      white-space:nowrap}
th{background:#f7f8fc;font-weight:600;color:var(--muted);text-align:right;
   border-bottom:1px solid var(--line-2);font-size:10.5px;
   text-transform:uppercase;letter-spacing:.05em}
th.l,td.l{text-align:left;white-space:normal;word-break:break-word}
tr:nth-child(even) td{background:#fafbfd}
tr.us{background:var(--hi) !important}
tr.us td{font-weight:700;background:var(--hi) !important}
tr:last-child td{border-bottom:none}

/* Result pill (used in tables) */
.pill{display:inline-block;padding:1px 7px;border-radius:9px;
      font-size:10.5px;font-weight:700;color:#fff;min-width:18px;
      text-align:center;line-height:15px;letter-spacing:.02em}
.pill.W{background:var(--w)}
.pill.L{background:var(--l)}
.pill.D,.pill.T{background:var(--d)}
.pill.NR,.pill.A{background:var(--nr)}

.form-pill{display:inline-block;width:15px;height:15px;border-radius:4px;
           margin:0 1px;font-size:9px;line-height:15px;text-align:center;
           color:#fff;font-weight:700}
.form-pill.W{background:var(--w)}
.form-pill.L{background:var(--l)}
.form-pill.D,.form-pill.T,.form-pill.NR,.form-pill.A{background:var(--d)}

/* Bar charts (W/L/D segments) */
.bar-row{display:flex;align-items:center;margin:6px 0;gap:8px;font-size:12px}
.bar-row .lbl{flex:0 0 108px;color:var(--ink);font-weight:600;font-size:11.5px}
.bar-row .bar{flex:1;height:22px;background:#eef0f6;display:flex;
              border-radius:6px;overflow:hidden;
              box-shadow:inset 0 0 0 1px rgba(0,0,0,.05)}
.bar-row .bar .seg{display:flex;align-items:center;justify-content:center;
                   color:#fff;font-weight:700;font-size:10.5px;line-height:1}
.seg.W{background:linear-gradient(180deg,#1c9b46,#157235)}
.seg.L{background:linear-gradient(180deg,#dc2626,#a01818)}
.seg.D,.seg.T{background:linear-gradient(180deg,#7b8493,#535a68)}
.seg.NR,.seg.A{background:linear-gradient(180deg,#d4a83a,#a47d20)}
.bar-row .num{flex:0 0 96px;text-align:right;color:var(--muted);
              font-size:10.5px;font-weight:600}

/* Per-season avg comparison bars */
.avg-tbl td.bar2-cell{padding:2px 6px;width:46%}
.bar2{height:11px;background:#eef0f6;border-radius:5px;position:relative;
      margin:2px 0}
.bar2 span{position:absolute;left:0;top:0;bottom:0;border-radius:5px;
           display:flex;align-items:center;justify-content:flex-end;
           padding-right:5px;color:#fff;font-size:9.5px;font-weight:700}
.bar2 span.us{background:linear-gradient(180deg,#3b82f6,#1d4ed8)}
.bar2 span.them{background:linear-gradient(180deg,#ef4444,#b91c1c)}

/* Top-N players: name on its own row, stats below — keeps long names
   from squashing the numeric grid on narrow phone widths. */
table.players{margin:6px 0 4px}
table.players thead th{font-size:10px;padding:4px 4px;color:var(--muted);
  background:#f7f8fc;border-bottom:1px solid var(--line-2);
  text-transform:uppercase;letter-spacing:.05em}
table.players tr.p-name td{padding:8px 8px 2px;border-top:1px solid var(--line);
  border-bottom:none;text-align:left;background:#fff;
  white-space:normal;word-break:normal}
table.players tr.p-stat td{padding:2px 4px 9px;border-bottom:none;
  font-size:11.5px;background:#fff}
table.players tr.p-name + tr.p-stat td{padding-top:1px}
table.players tr.p-name td .rk{display:inline-flex;align-items:center;
  justify-content:center;width:20px;height:20px;border-radius:6px;
  background:#e6ecf9;color:var(--accent);font-weight:800;font-size:10.5px;
  margin-right:8px;vertical-align:middle}
table.players tr.p-name td .nm{font-weight:700;font-size:13.5px;color:var(--ink);
  letter-spacing:-.005em;vertical-align:middle}
/* Podium tints span both rows of each top-3 player. */
table.players tr.top1 td{background:#fff4cc !important}
table.players tr.top1 .rk{background:#f3d568;color:#7a5300}
table.players tr.top2 td{background:#eef0f5 !important}
table.players tr.top2 .rk{background:#cdd2dc;color:#3e4757}
table.players tr.top3 td{background:#f6e7d4 !important}
table.players tr.top3 .rk{background:#e0b988;color:#6e3f12}
/* Fade in a subtle separator before the next player */
table.players tbody tr.p-stat + tr.p-name td{border-top:1px solid var(--line)}

/* Recent-match list — scores wrap to a second line if they don't fit */
.recent-list{margin:6px 0 2px;display:flex;flex-direction:column;gap:6px}
.r-row{display:grid;grid-template-columns:64px 32px 18px 1fr;
       gap:3px 8px;padding:8px 10px;
       border:1px solid var(--line);border-radius:8px;background:#fff;
       align-items:center}
.r-row .date{font-size:10.5px;color:var(--muted);font-weight:700}
.r-row .pill{justify-self:start}
.r-row .venue{font-size:10.5px;font-weight:700;
              text-align:center;color:var(--muted)}
.r-row .opp{font-size:12.5px;font-weight:700;
            white-space:normal;word-break:break-word;line-height:1.25}
.r-row .scores{grid-column:1 / -1;font-size:11px;color:var(--muted);
               font-variant-numeric:tabular-nums;line-height:1.35;
               white-space:normal;word-break:break-word}
.r-row .scores b{color:var(--ink)}
/* Per-match performance strip (top scorer + 50+, top bowler + 3wi+) */
.r-row .perf{grid-column:1 / -1;font-size:11px;line-height:1.4;
  padding:1px 0 0;color:var(--ink)}
.r-row .perf .lbl{display:inline-block;min-width:30px;font-size:9.5px;
  font-weight:800;color:var(--muted);text-transform:uppercase;
  letter-spacing:.06em;margin-right:6px}
.r-row .perf.bat .lbl{color:#0f5b29}
.r-row .perf.bowl .lbl{color:#7e1f1f}
.r-row .perf .who a{color:var(--ink);text-decoration:none;
  border-bottom:1px dotted var(--line-2)}
.r-row .perf .who a:hover{color:var(--accent);border-bottom-color:var(--accent)}
.r-row .perf .x{color:var(--muted);font-size:10.5px}

/* First-innings matrix + chase matrix share the same scaffolding */
table.fi-matrix{width:100%;margin:6px 0 4px;border-collapse:separate;
  border-spacing:5px;table-layout:fixed}
table.fi-matrix col.rh-col{width:82px}
table.fi-matrix thead th{padding:3px 4px;font-size:10.5px;
  text-transform:uppercase;letter-spacing:.05em;color:var(--muted);
  background:none;border:none;text-align:center}
table.fi-matrix thead th.corner{background:transparent}
table.fi-matrix th.rh{text-align:left;font-weight:800;font-size:12.5px;
  color:var(--ink);background:#f7f8fc;padding:8px 8px;border-radius:8px;
  border:1px solid var(--line);line-height:1.15;vertical-align:middle}
table.fi-matrix th.rh .subh{display:block;font-size:9.5px;font-weight:600;
  color:var(--muted);text-transform:none;letter-spacing:0;margin-top:3px;
  white-space:normal}
table.fi-matrix td{padding:0;background:transparent;border:none}

/* Cells — base + own (blue tint) and opp (red tint) variants so the
   "whose runs is this?" question is legible at a glance. */
.fi-cell{background:#fff;border:1px solid var(--line);border-radius:9px;
  padding:8px 6px 7px;text-align:center;box-shadow:var(--shadow);
  display:flex;flex-direction:column;align-items:center;gap:1px;
  min-height:88px;justify-content:center;position:relative}
tr.own .fi-cell{background:#eef3fb;border-color:#cfdbf2}
tr.opp .fi-cell{background:#fbeded;border-color:#f1cdcd}
tr.won .fi-cell{background:#e9f4ec;border-color:#c6e2cd}
tr.lost .fi-cell{background:#fbeded;border-color:#f1cdcd}
.fi-cell.empty{background:#fafbfd !important;color:var(--muted);
  border-color:var(--line) !important}
.fi-cell .avg{font-size:24px;font-weight:800;color:var(--ink);line-height:1;
  font-variant-numeric:tabular-nums}
tr.own .fi-cell .avg{color:#193e94}
tr.opp .fi-cell .avg, tr.lost .fi-cell .avg{color:#8b1c1c}
tr.won .fi-cell .avg{color:#0f5b29}
.fi-cell .med{font-size:11px;color:var(--muted);font-weight:600;
  font-variant-numeric:tabular-nums;margin-top:2px}
.fi-cell .sub{font-size:9.5px;color:var(--muted);font-weight:600;
  letter-spacing:.02em;line-height:1.25}
.fi-cell .tag{display:inline-block;padding:1px 6px;border-radius:5px;
  font-size:8.5px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;
  margin-bottom:3px;line-height:1.15}
.fi-cell .tag.own{background:#1d4ed8;color:#fff}
.fi-cell .tag.opp{background:#b91c1c;color:#fff}

/* Chasing-matrix specifics — narrow row-header so two cells fit comfortably */
table.chase-matrix th.rh .subh{font-size:9px}
.fi-cell .vs{font-size:11px;color:var(--muted);font-weight:600;
  font-variant-numeric:tabular-nums;line-height:1.2}
.fi-cell .vs b{color:var(--ink);font-weight:800}
.fi-cell .gap{font-size:10px;color:var(--muted);margin-top:2px;font-weight:600}

/* Toss split bar */
.choice-bar{height:22px;background:#eef0f6;border-radius:6px;
            display:flex;overflow:hidden;margin:6px 0;
            box-shadow:inset 0 0 0 1px rgba(0,0,0,.05)}
.choice-bar .seg{color:#fff;display:flex;align-items:center;
                 justify-content:center;font-weight:700;font-size:11px}
.choice-bar .seg.bat{background:linear-gradient(180deg,#3b82f6,#1d4ed8)}
.choice-bar .seg.fld{background:linear-gradient(180deg,#d4a83a,#a47d20)}

/* Headline strip */
.headline{display:flex;gap:8px;margin:0 0 10px}
.h-tile{flex:1;padding:8px 10px;border-radius:9px;background:#fff;
        border:1px solid var(--line);box-shadow:var(--shadow);
        text-align:center}
.h-tile .n{font-size:18px;font-weight:800;color:var(--ink);line-height:1}
.h-tile .lbl{font-size:9.5px;text-transform:uppercase;letter-spacing:.06em;
             color:var(--muted);margin-top:3px;font-weight:700}
.h-tile.win .n{color:var(--w)}
.h-tile.loss .n{color:var(--l)}

a{color:var(--accent);text-decoration:none;word-break:break-all}
a:hover{text-decoration:underline}
.bullets{padding-left:18px;margin:6px 0}
.bullets li{margin:4px 0;font-size:12px}
.small{font-size:11px;color:var(--muted)}
.footer{margin:18px 4px 0;font-size:10.5px;color:var(--muted);text-align:center}
.score-cell{font-size:10.5px;color:var(--muted)}
.warn{background:#fff7e0;border:1px solid #ecd075;border-radius:8px;
      padding:9px 11px;font-size:11.5px;color:#704a00;line-height:1.4}
.warn b{color:#5a3700}

/* Link kind chips for the video section */
.link-list{display:flex;flex-direction:column;gap:5px;margin:6px 0}
.link-row{display:flex;gap:8px;align-items:center;padding:6px 8px;
          border:1px solid var(--line);border-radius:8px;background:#fff;
          font-size:11.5px}
.kind{flex:0 0 auto;padding:1px 7px;border-radius:6px;font-size:9.5px;
      font-weight:800;letter-spacing:.04em;text-transform:uppercase;
      background:#eef0f6;color:var(--muted)}
.kind.official{background:#e6f1e6;color:#15803d}
.kind.video{background:#e6ecfb;color:var(--accent)}
.kind.probe{background:#f1ecfb;color:#5b21b6}
.kind.speculative{background:#fdecec;color:#b91c1c}
"""


def render_html(d):
    parts = []
    parts.append("<!doctype html>")
    parts.append('<html lang="en"><head><meta charset="utf-8">')
    parts.append('<meta name="viewport" content="width=device-width,initial-scale=1">')
    parts.append(f"<title>Scout — {_esc(d['club_name'])} 1st XI</title>")
    parts.append(f"<style>{CSS}</style></head><body>")

    # Render-height marker for the long-PNG renderer (sets data-render-h on
    # <html> so render_long_png() can read it via --dump-dom).
    parts.append("<script>"
                 "window.addEventListener('load',function(){"
                 "requestAnimationFrame(function(){"
                 "document.documentElement.dataset.renderH="
                 "document.body.scrollHeight;});});"
                 "</script>")

    # ------- Hero header (club, version, headline numbers) ----------------
    target_set = set(d["target_team_ids"])
    hero_tiles = _hero_stats(d, target_set)
    version_str = (f" · v{d['version']}" if d.get("version") is not None else "")
    parts.append("<div class='hero'>")
    parts.append(f"<div class='eyebrow'>Scouting report{_esc(version_str)}</div>")
    parts.append(f"<h1>{_esc(d['club_name'])} 1st XI</h1>")
    parts.append(f"<p class='meta'>vs {_esc(d['vs_club_name'])} · "
                 f"League + Cup · {_esc(d['today'])}</p>")
    if hero_tiles:
        parts.append("<div class='stats'>")
        for n, lbl in hero_tiles:
            parts.append(f"<div class='stat'><div class='n'>{_esc(n)}</div>"
                         f"<div class='lbl'>{_esc(lbl)}</div></div>")
        parts.append("</div>")
    parts.append("</div>")

    # ------- 0. League table ----------------------------------------------
    parts.append("<div class='card'>")
    parts.append("<h2><span class='num'>0</span>Current league table</h2>")
    if d["league_table"]:
        parts.append(f"<p class='subtle'>{_esc(d['league_div_label'] or '')}</p>")
        parts.append("<table><tr>"
                     "<th>Pos</th><th class='l'>Team</th>"
                     "<th>P</th><th>W</th><th>Pts</th>"
                     "<th class='l'>Form</th></tr>")
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
    parts.append("</div>")

    # ------- 1. Last N seasons finishing positions ------------------------
    parts.append("<div class='card'>")
    parts.append("<h2><span class='num'>1</span>Last 3 seasons — finishing positions</h2>")
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
    parts.append("</div>")

    # ------- 2. Top batters -----------------------------------------------
    parts.append("<div class='card'>")
    parts.append(f"<h2><span class='num'>2</span>Top run scorers · "
                 f"{len(d['last_n_seasons'])} seasons</h2>")
    parts.append("<table class='players'>")
    parts.append("<thead><tr>"
                 "<th>M</th><th>I</th><th>NO</th><th>Runs</th><th>HS</th>"
                 "<th>Avg</th><th>SR</th><th>50</th><th>100</th><th>Pos</th>"
                 "</tr></thead><tbody>")
    for i, r in enumerate(d["top_batters"], 1):
        (bid, name, innings, no_, runs, hs, fifties, hundreds, balls_known,
         runs_when_balls, matches_, mode_pos) = r
        avg = f"{runs/(innings-no_):.2f}" if (innings-no_) > 0 else "—"
        sr = f"{100*runs_when_balls/balls_known:.1f}" if balls_known else "—"
        rank_cls = f" top{i}" if i <= 3 else ""
        nm_html = _player_link(d.get("pc_subdomain"), bid, "batting", name)
        parts.append(
            f"<tr class='p-name{rank_cls}'>"
            f"<td colspan='10' class='l'>"
            f"<span class='rk'>{i}</span>"
            f"<span class='nm'>{nm_html}</span></td></tr>"
            f"<tr class='p-stat{rank_cls}'>"
            f"<td>{matches_}</td><td>{innings}</td><td>{no_}</td>"
            f"<td><b>{runs}</b></td><td>{hs}</td><td>{avg}</td>"
            f"<td>{sr}</td><td>{fifties}</td><td>{hundreds}</td>"
            f"<td>{mode_pos}</td></tr>"
        )
    parts.append("</tbody></table>")
    parts.append("</div>")

    # ------- 3. Top bowlers -----------------------------------------------
    parts.append("<div class='card'>")
    parts.append(f"<h2><span class='num'>3</span>Top wicket takers · "
                 f"{len(d['last_n_seasons'])} seasons</h2>")
    parts.append("<table class='players'>")
    parts.append("<thead><tr>"
                 "<th>M</th><th>Ov</th><th>Md</th><th>R</th><th>W</th>"
                 "<th>Avg</th><th>Econ</th><th>Best</th><th>5wi</th><th>4wi</th>"
                 "</tr></thead><tbody>")
    for i, r in enumerate(d["top_bowlers"], 1):
        avg = f"{r['avg']:.2f}" if r["avg"] is not None else "—"
        econ = f"{r['econ']:.2f}" if r["econ"] is not None else "—"
        rank_cls = f" top{i}" if i <= 3 else ""
        nm_html = _player_link(d.get("pc_subdomain"), r['bowler_id'],
                               "bowling", r['name'])
        parts.append(
            f"<tr class='p-name{rank_cls}'>"
            f"<td colspan='10' class='l'>"
            f"<span class='rk'>{i}</span>"
            f"<span class='nm'>{nm_html}</span></td></tr>"
            f"<tr class='p-stat{rank_cls}'>"
            f"<td>{r['matches']}</td><td>{r['overs']}</td>"
            f"<td>{r['maidens']}</td><td>{r['runs']}</td>"
            f"<td><b>{r['wickets']}</b></td><td>{avg}</td>"
            f"<td>{econ}</td><td>{r['best']}</td>"
            f"<td>{r['fivers']}</td><td>{r['fourers']}</td></tr>"
        )
    parts.append("</tbody></table>")
    parts.append("</div>")

    # ------- 4. H2H -------------------------------------------------------
    parts.append("<div class='card'>")
    parts.append(f"<h2><span class='num'>4</span>Head-to-head vs "
                 f"{_esc(d['vs_club_name'])} 1st XI</h2>")
    if d["h2h"]:
        target_set = set(d["target_team_ids"])
        tally = {"W":0, "L":0, "D":0}
        for m in d["h2h"]:
            r = result_for(m["result"], m["result_applied_to"], target_set)
            if r == "W": tally["W"] += 1
            elif r == "L": tally["L"] += 1
            else: tally["D"] += 1
        them_short = re.sub(r",.*$", "", d["club_name"]).strip()[:14]
        us_short = re.sub(r",.*$", "", d["vs_club_name"]).strip()[:14]
        parts.append("<div class='headline'>")
        parts.append(f"<div class='h-tile win'><div class='n'>{tally['W']}</div>"
                     f"<div class='lbl'>{_esc(them_short)}</div></div>")
        parts.append(f"<div class='h-tile'><div class='n'>{tally['D']}</div>"
                     f"<div class='lbl'>D / NR</div></div>")
        parts.append(f"<div class='h-tile loss'><div class='n'>{tally['L']}</div>"
                     f"<div class='lbl'>{_esc(us_short)}</div></div>")
        parts.append("</div>")
        parts.append(f"<p class='subtle'>{len(d['h2h'])} played meetings in "
                     f"the cache.</p>")
        parts.append("<table><tr><th>Date</th><th>Comp</th>"
                     "<th>V</th><th>Res</th><th class='l'>Detail</th></tr>")
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
        parts.append("<p class='small'>No played 1st XI head-to-head matches "
                     "in the cache.</p>")
    if d.get("h2h_upcoming"):
        parts.append("<p><b>Upcoming fixtures</b></p>")
        parts.append("<ul class='bullets'>")
        for m in sorted(d["h2h_upcoming"], key=lambda x: to_iso(x["match_date"])):
            we_home = m["home_club_id"] == d["club_id"]
            venue = "H" if we_home else "A"
            parts.append(f"<li>{_esc(m['match_date'])} "
                         f"({_esc(m['competition_type'])}, {venue})</li>")
        parts.append("</ul>")
    parts.append("</div>")

    # ------- 5. Recent matches -------------------------------------------
    parts.append("<div class='card'>")
    parts.append(f"<h2><span class='num'>5</span>Last {len(d['recent'])} "
                 "1st XI matches (L+C)</h2>")
    parts.append("<div class='recent-list'>")
    target_set = set(d["target_team_ids"])
    sub = d.get("pc_subdomain")
    for m in d["recent"]:
        we_home = m["home_club_id"] == d["club_id"]
        venue = "H" if we_home else "A"
        opp = m["away_club_name"] if we_home else m["home_club_name"]
        opp = re.sub(r",.*$", "", opp).strip()
        r = result_for(m["result"], m["result_applied_to"], target_set)
        url = play_cricket_match_url(m["match_id"])
        scores = _esc(innings_inline(m)).replace(" · ", " &middot; ")
        parts.append(
            f"<div class='r-row'>"
            f"<div class='date'><a href='{url}'>{_esc(m['match_date'])}</a></div>"
            f"<span class='pill {r}'>{r}</span>"
            f"<div class='venue'>{venue}</div>"
            f"<div class='opp'>{_esc(opp)}</div>"
            f"<div class='scores'>{scores}</div>"
        )
        # Per-side performance — top batter (+ any 50+ supporting innings),
        # then top bowler (+ any 3wi+ supporting spells). Names link to
        # Play-Cricket player profiles when we have the club's subdomain.
        perf = m.get("perf") or {}
        if perf.get("top_bat"):
            tb = perf["top_bat"]
            bits = [_perf_bat_html(sub, tb)]
            for x in perf.get("fifty_plus", []):
                bits.append(_perf_bat_html(sub, x))
            parts.append("<div class='perf bat'>"
                         "<span class='lbl'>Bat</span>"
                         + " &middot; ".join(bits) + "</div>")
        if perf.get("top_bowl") and perf["top_bowl"]["wickets"] is not None:
            tb = perf["top_bowl"]
            bits = [_perf_bowl_html(sub, tb)]
            for x in perf.get("three_plus", []):
                bits.append(_perf_bowl_html(sub, x))
            parts.append("<div class='perf bowl'>"
                         "<span class='lbl'>Bowl</span>"
                         + " &middot; ".join(bits) + "</div>")
        parts.append("</div>")
    parts.append("</div>")
    parts.append("</div>")

    # ------- 6. Patterns / charts ----------------------------------------
    parts.append("<div class='card'>")
    parts.append("<h2><span class='num'>6</span>Patterns</h2>")

    # 6a. First-innings matrix (bat/bowl × home/away). Rendered first
    # because it's the most actionable signal for a captain who's just
    # won the toss.
    if d.get("fi_matrix"):
        parts.append("<h3>First-innings totals · last 10 in each quadrant</h3>")
        parts.append("<p class='subtle'>The runs the team batting first put "
                     "up. <b style='color:#193e94'>Blue</b> = "
                     f"{_esc(d['club_name'])}'s own innings; "
                     "<b style='color:#8b1c1c'>red</b> = opposition's innings "
                     f"when {_esc(d['club_name'])} bowls first.</p>")
        parts.append(_render_fi_matrix(d["fi_matrix"], d["club_name"]))

    # 6b. Chase matrix.
    if d.get("ch_matrix"):
        parts.append("<h3>Run chases · last 10 in each quadrant</h3>")
        parts.append("<p class='subtle'>"
                     "<b style='color:#0f5b29'>Green</b> = chase succeeded "
                     "(the headline number is the target chased); "
                     "<b style='color:#8b1c1c'>red</b> = chase failed "
                     "(scored X vs target Y).</p>")
        parts.append(_render_chase_matrix(d["ch_matrix"], d["club_name"]))

    parts.append("<h3>Bat 1st vs Bat 2nd · last 3 seasons</h3>")
    parts.append(wld_chart_block([
        (f"{d['club_name']} bat 1st", d["chart_bat"]["us_bat1"]),
        (f"{d['club_name']} bat 2nd", d["chart_bat"]["us_bat2"]),
        (f"{d['vs_club_name']} bat 1st", d["chart_bat"]["vs_bat1"]),
        (f"{d['vs_club_name']} bat 2nd", d["chart_bat"]["vs_bat2"]),
    ]))

    parts.append("<h3>Home vs Away · last 3 seasons</h3>")
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
        parts.append(f"<p class='subtle'>Won toss in <b>{t['won_n']}</b> "
                     f"matches · chose to <b>bat</b> {t['chose_bat_n']} "
                     f"({bat_pct:.0f}%), <b>field</b> {t['chose_field_n']} "
                     f"({fld_pct:.0f}%)</p>")
        parts.append(
            "<div class='choice-bar'>"
            f"<div class='seg bat' style='width:{bat_pct:.1f}%'>"
            f"Bat {t['chose_bat_n']}</div>"
            f"<div class='seg fld' style='width:{fld_pct:.1f}%'>"
            f"Field {t['chose_field_n']}</div>"
            "</div>"
        )
        parts.append(wld_chart_block([
            ("Won → batted", t["chose_bat_outcome"]),
            ("Won → fielded", t["chose_field_outcome"]),
            ("Lost toss", t["lost_outcome"]),
        ]))
    else:
        parts.append("<p class='small'>No toss data in scope.</p>")

    parts.append("<h3>Team batting & bowling avg per season</h3>")
    parts.append(avg_chart_block(
        d["last_n_seasons"], d["us_avgs"], d["vs_avgs"],
        d["club_name"], d["vs_club_name"]))
    parts.append("</div>")

    # ------- 7. Web / video links ----------------------------------------
    parts.append("<div class='card'>")
    parts.append("<h2><span class='num'>7</span>Web &amp; video links</h2>")
    by_kind = {"official": [], "video": [], "speculative": []}
    for label, url, kind in d["video_links"]:
        by_kind.setdefault(kind, []).append((label, url))

    if by_kind["official"]:
        parts.append("<h3>Official / club channels</h3>")
        parts.append("<div class='link-list'>")
        for lbl, url in by_kind["official"]:
            parts.append(f"<div class='link-row'>"
                         f"<span class='kind official'>Official</span>"
                         f"<a href='{_esc(url)}'>{_esc(lbl)}</a></div>")
        parts.append("</div>")
    if by_kind["video"]:
        parts.append("<h3>Verified match videos</h3>")
        parts.append("<div class='link-list'>")
        for lbl, url in by_kind["video"]:
            parts.append(f"<div class='link-row'>"
                         f"<span class='kind video'>Video</span>"
                         f"<a href='{_esc(url)}'>{_esc(lbl)}</a></div>")
        parts.append("</div>")
    else:
        parts.append("<p class='subtle'>No match videos verified yet for "
                     "this club. The assistant will work through the "
                     "fixtures in section 5 and add verified links here in "
                     "the next version.</p>")
    if by_kind["speculative"]:
        parts.append("<h3>Speculative — name match only, NOT verified</h3>")
        parts.append("<div class='link-list'>")
        for lbl, url in by_kind["speculative"]:
            parts.append(f"<div class='link-row'>"
                         f"<span class='kind speculative'>Specul.</span>"
                         f"<a href='{_esc(url)}'>{_esc(lbl)}</a></div>")
        parts.append("</div>")
    parts.append("</div>")

    parts.append(f"<p class='footer'>Generated {_esc(d['today'])}"
                 f"{_esc(version_str)} from <code>data/rainham.db</code> · "
                 f"Source: Play-Cricket public API</p>")
    parts.append("</body></html>")
    return "\n".join(parts)


def _hero_stats(d, target_set):
    """Build [(value, label), ...] tiles for the top of the hero block.

    Picks: current league position (if found), this-season W-L-D from the
    current season's matches, and last-3-seasons total played count."""
    tiles = []
    # Current league pos
    if d.get("league_table"):
        for r in d["league_table"]:
            if r["team_id"] in d["target_team_ids"]:
                pos = r.get("position")
                if pos:
                    tiles.append((f"{pos}", "League pos"))
                break

    # This-season record (from season_summary, current season is the last entry).
    # Fall back to the previous season's headline if the current season has no
    # played league matches yet (e.g. early-May before opening day).
    if d["season_summary"]:
        cur = d["season_summary"][-1]
        prev = d["season_summary"][-2] if len(d["season_summary"]) > 1 else None
        if cur["P"] > 0:
            tiles.append((f"{cur['W']}-{cur['L']}-{cur['D']}",
                          f"{cur['season']} L W-L-D"))
            tiles.append((f"{cur['win_pct']:.0f}%", f"{cur['season']} win rate"))
        elif prev and prev["P"] > 0:
            tiles.append((f"{prev['W']}-{prev['L']}-{prev['D']}",
                          f"{prev['season']} L W-L-D"))
            tiles.append((f"{prev['win_pct']:.0f}%",
                          f"{prev['season']} win rate"))

    # H2H tally — only show if there's a played meeting
    if d.get("h2h"):
        w = l_ = dr = 0
        for m in d["h2h"]:
            r = result_for(m["result"], m["result_applied_to"], target_set)
            if r == "W": w += 1
            elif r == "L": l_ += 1
            else: dr += 1
        if w + l_ + dr > 0:
            short = re.sub(r",.*$", "", d["vs_club_name"]).strip()[:12]
            tiles.append((f"{w}-{l_}-{dr}", f"vs {short} all-time"))
    return tiles[:4]


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
            inner = f"{n}" if pct > 8 else ""
            segs.append(f"<div class='seg {k}' style='width:{pct:.1f}%' "
                        f"title='{k}: {n}'>{inner}</div>")
        w = counts.get("W", 0); l = counts.get("L", 0)
        win_pct = 100 * w / p if p else 0
        nb = f"{w}–{l} · {win_pct:.0f}%"
        parts.append(f"<div class='bar-row'>"
                     f"<div class='lbl'>{_esc(label)}</div>"
                     f"<div class='bar'>{''.join(segs)}</div>"
                     f"<div class='num'>{nb}</div></div>")
    parts.append("</div>")
    return "".join(parts)


def avg_chart_block(seasons, us_avgs, vs_avgs, us_name, vs_name):
    """Two side-by-side bars per season: Us bat-avg vs Vs bat-avg, Us bowl-avg
    vs Vs bowl-avg."""
    all_vals = []
    for s in seasons:
        for d_ in (us_avgs.get(s, {}), vs_avgs.get(s, {})):
            for k in ("bat_avg", "bowl_avg"):
                v = d_.get(k)
                if v is not None: all_vals.append(v)
    max_val = max(all_vals) if all_vals else 50.0
    max_val = max(max_val * 1.1, 30)

    parts = []
    parts.append("<table class='avg-tbl'>")
    parts.append("<tr><th>Yr</th><th class='l'>Bat avg</th>"
                 "<th class='l'>Bowl avg</th></tr>")
    for s in seasons:
        u = us_avgs.get(s, {})
        v = vs_avgs.get(s, {})
        ub = u.get("bat_avg"); vb = v.get("bat_avg")
        ubo = u.get("bowl_avg"); vbo = v.get("bowl_avg")
        bat_html = mini_double_bar(ub, vb, max_val)
        bowl_html = mini_double_bar(ubo, vbo, max_val)
        parts.append(f"<tr><td>{s}</td>"
                     f"<td class='l bar2-cell'>{bat_html}</td>"
                     f"<td class='l bar2-cell'>{bowl_html}</td></tr>")
    parts.append("</table>")
    parts.append(f"<p class='small'>"
                 f"<span style='display:inline-block;width:8px;height:8px;"
                 f"background:#1d4ed8;border-radius:2px;vertical-align:middle'></span> "
                 f"{_esc(us_name)} &nbsp; "
                 f"<span style='display:inline-block;width:8px;height:8px;"
                 f"background:#b91c1c;border-radius:2px;vertical-align:middle'></span> "
                 f"{_esc(vs_name)}</p>")
    return "".join(parts)


def mini_double_bar(us_val, vs_val, max_val):
    """Two narrow stacked bars - one for us, one for them. Returns HTML."""
    def bar(val, kind):
        if val is None or max_val <= 0:
            return "<div class='bar2'></div>"
        pct = min(100, 100 * val / max_val)
        return (f"<div class='bar2'>"
                f"<span class='{kind}' style='width:{pct:.1f}%'>"
                f"{val:.1f}</span></div>")
    return f"{bar(us_val,'us')}{bar(vs_val,'them')}"


def _render_fi_matrix(matrix, club_name):
    """Render the bat/bowl × home/away first-innings matrix.

    Row labels are deliberately short — the section header already tells
    the reader which team this is about, so repeating the club name in
    every row just steals horizontal space from the data cells.
    """
    def cell(side, venue):
        c = matrix.get((side, venue))
        tag = ("<span class='tag own'>own</span>" if side == "bat"
               else "<span class='tag opp'>opp</span>")
        if not c:
            return ("<div class='fi-cell empty'>"
                    f"{tag}"
                    "<div class='avg'>—</div>"
                    "<div class='sub'>no data</div></div>")
        first = _date_short(c["first"])
        last = _date_short(c["last"])
        med = c["median"]
        if isinstance(med, float):
            med_s = f"{med:.0f}" if med.is_integer() else f"{med:.1f}"
        else:
            med_s = str(med)
        return (f"<div class='fi-cell'>"
                f"{tag}"
                f"<div class='avg'>{c['avg']:.0f}</div>"
                f"<div class='med'>med {med_s}</div>"
                f"<div class='sub'>n={c['n']}</div>"
                f"<div class='sub'>{_esc(first)} → {_esc(last)}</div>"
                f"</div>")

    return (
        "<table class='fi-matrix'>"
        "<colgroup>"
        "<col class='rh-col'><col><col>"
        "</colgroup>"
        "<thead><tr>"
        "<th class='corner'></th>"
        "<th>Home</th>"
        "<th>Away</th>"
        "</tr></thead>"
        "<tbody>"
        "<tr class='own'><th class='rh'>Bat 1st"
        "<span class='subh'>our innings</span></th>"
        f"<td>{cell('bat','home')}</td><td>{cell('bat','away')}</td></tr>"
        "<tr class='opp'><th class='rh'>Bowl 1st"
        "<span class='subh'>opp. innings</span></th>"
        f"<td>{cell('bowl','home')}</td><td>{cell('bowl','away')}</td></tr>"
        "</tbody></table>"
    )


def _render_chase_matrix(matrix, club_name):
    """Two stacked 2×2 grids, one for "we chase" and one for "opp chases".
    Rows are won/lost (chaser perspective); columns are home/away
    (always Spartans' venue)."""

    def cell(chaser, venue, outcome):
        c = matrix.get((chaser, venue, outcome))
        if not c:
            return ("<div class='fi-cell empty'>"
                    "<div class='avg'>—</div>"
                    "<div class='sub'>no data</div></div>")
        first = _date_short(c["first"])
        last = _date_short(c["last"])
        if outcome == "won":
            body = (f"<div class='avg'>{c['avg_target']:.0f}</div>"
                    f"<div class='med'>chased</div>")
        else:
            gap = c['avg_target'] - c['avg_score']
            body = (f"<div class='vs'><b>{c['avg_score']:.0f}</b> "
                    f"vs <b>{c['avg_target']:.0f}</b></div>"
                    f"<div class='gap'>fell {gap:.0f} short</div>")
        return (f"<div class='fi-cell'>"
                f"{body}"
                f"<div class='sub'>n={c['n']}</div>"
                f"<div class='sub'>{_esc(first)} → {_esc(last)}</div>"
                f"</div>")

    def block(chaser, title, sub_title):
        return (
            f"<p class='subtle' style='margin-top:8px'><b>{_esc(title)}</b> "
            f"&middot; <span class='small'>{_esc(sub_title)}</span></p>"
            "<table class='fi-matrix chase-matrix'>"
            "<colgroup><col class='rh-col'><col><col></colgroup>"
            "<thead><tr>"
            "<th class='corner'></th>"
            "<th>Home</th><th>Away</th>"
            "</tr></thead>"
            "<tbody>"
            "<tr class='won'><th class='rh'>Made"
            "<span class='subh'>chased it</span></th>"
            f"<td>{cell(chaser,'home','won')}</td>"
            f"<td>{cell(chaser,'away','won')}</td></tr>"
            "<tr class='lost'><th class='rh'>Failed"
            "<span class='subh'>fell short</span></th>"
            f"<td>{cell(chaser,'home','lost')}</td>"
            f"<td>{cell(chaser,'away','lost')}</td></tr>"
            "</tbody></table>"
        )

    return (
        block("us",  f"{club_name} chasing",
              "they batted second")
        + block("opp", "Opposition chasing",
                f"{club_name} batted first, opp chasing the target")
    )


if __name__ == "__main__":
    raise SystemExit(main())
