#!/usr/bin/env python3
"""
Build the RCC player-stats dashboard data.

Writes:

  app/rcc/index.html              chooser page (one card per Rainham
                                  1st-XI player in our universe)
  app/rcc/player.html             single dashboard template — reads
                                  ?id=<player_id> and fetches its JSON
  app/data/rcc/index.json         summary: name, apps, runs, wkts,
                                  has_bbb per player
  app/data/rcc/<player_id>.json   per-player bundle: matches +
                                  batting + bowling + balls_faced +
                                  balls_bowled, with opposition
                                  metadata snapshotted onto each ball
                                  for the BBB slicers.
  app/static/rcc.js               dashboard client (slicers + cards)

Universe: Rainham 1st XI / League + non-T20 Cup, last 10 seasons —
same `_app_lib.first_xi_match_ids` filter used everywhere else.

The bundle is intentionally raw: every slice the user can apply on
the page (home/away, season, position, bowler-type, innings phase,
spell, etc.) is computed client-side by `rcc.js` against this data.
That keeps the build cheap and the bundle small while keeping
filter combos snappy.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from html import escape
from pathlib import Path

import _app_lib as L


SEASONS_BACK = 10
RCC_DATA_DIR = L.APP_DIR / "data" / "rcc"
RCC_PAGE_DIR = L.APP_DIR / "rcc"

PC_PLAYER_URL = "https://rainhamcc.play-cricket.com/player_stats/batting/{pid}"
PC_MATCH_URL  = "https://rainhamcc.play-cricket.com/website/results/{mid}"


# ---------------------------------------------------------- universe --

def relevant_match_ids(conn) -> list[int]:
    season_min = dt.date.today().year - SEASONS_BACK + 1
    return L.first_xi_match_ids(
        conn, season_min=season_min, include_unplayed=True,
    )


def rcc_player_ids(conn, mids: list[int]) -> list[int]:
    """Rainham 1st-XI players seen on a relevant fixture roster."""
    if not mids:
        return []
    qm = ",".join("?" * len(mids))
    rows = conn.execute(f"""
        SELECT mp.player_id AS pid, COUNT(DISTINCT mp.match_id) AS apps
        FROM match_players mp
        WHERE mp.club_id = ? AND mp.match_id IN ({qm})
          AND mp.player_id IS NOT NULL
        GROUP BY mp.player_id
        HAVING apps >= 1
        ORDER BY apps DESC
    """, (L.RAINHAM_CLUB_ID, *mids)).fetchall()
    return [int(r[0]) for r in rows]


# ---------------------------------------------------------- per-match meta --

def result_letter(m: dict) -> str:
    """W/L/D/T/NR/A from Rainham 1st XI's perspective.

    The DB stores `result` as a single letter ('W'/'L'/'D'/'T'/'A')
    and `result_applied_to` as a team_id (the team the W/L is from).
    Rainham 1st XI team_id is `RAINHAM_FIRST_XI_TEAM_ID`.
    """
    res = (m.get("result") or "").strip().upper()
    if not res:
        return ""
    applied_to = str(m.get("result_applied_to") or "").strip()
    rainham = applied_to == L.RAINHAM_FIRST_XI_TEAM_ID
    if res == "W":  return "W" if rainham else "L"
    if res == "L":  return "L" if rainham else "W"
    if res in ("T", "TIE"):       return "T"
    if res in ("D", "DRAW"):      return "D"
    if res in ("A", "ABD", "ABANDONED"): return "A"
    return res or "NR"


def match_meta(conn, mids: list[int]) -> dict[int, dict]:
    """
    Build a per-match metadata dict (everything the dashboard slicers
    or fixture-list might want). Indexed by match_id.
    """
    qm = ",".join("?" * len(mids))
    out: dict[int, dict] = {}
    for r in conn.execute(f"""
        SELECT * FROM matches WHERE match_id IN ({qm})
    """, mids).fetchall():
        m = dict(r)
        rainham_is_home = m["home_club_id"] == L.RAINHAM_CLUB_ID
        opp_club_id = m["away_club_id"] if rainham_is_home else m["home_club_id"]
        opp_club_nm = m["away_club_name"] if rainham_is_home else m["home_club_name"]
        rainham_team_id = m["home_team_id"] if rainham_is_home else m["away_team_id"]
        # batted_first is a team_id (not "Home"/"Away"). Rainham
        # batted first iff the team that batted first matches the
        # Rainham 1st XI team_id we know is on this match.
        bat_first_id = str(m.get("batted_first") or "").strip()
        rainham_batted_first = (bat_first_id == rainham_team_id
                                and bat_first_id != "")
        toss_won = m.get("toss_won_by_team_id") == rainham_team_id
        out[int(m["match_id"])] = {
            "match_id":        int(m["match_id"]),
            "match_date":      m["match_date"],
            "season":          int(m["season"] or 0),
            "opp_club_id":     opp_club_id or "",
            "opp_club_name":   opp_club_nm or "",
            "home_away":       "home" if rainham_is_home else "away",
            "result":          result_letter(m),
            "competition":     m.get("competition_type") or "",
            "competition_name": m.get("competition_name") or "",
            "bat_first":       rainham_batted_first,
            "toss_won":        toss_won,
            "no_of_overs":     int(m["no_of_overs"]) if m.get("no_of_overs") else None,
        }
    return out


# ---------------------------------------------------------- innings rows --

def batting_rows(conn, pid: int, mids: list[int]) -> list[dict]:
    qm = ",".join("?" * len(mids))
    rows = conn.execute(f"""
        SELECT match_id, innings_seq, position, runs, balls, fours, sixes,
               how_out, bowler_id, fielder_id
        FROM batting
        WHERE batsman_id = ? AND team_batting_club_id = ?
          AND match_id IN ({qm})
        ORDER BY match_id, innings_seq
    """, (pid, L.RAINHAM_CLUB_ID, *mids)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        ho = (d.get("how_out") or "").lower()
        d["did_not_bat"] = ho in ("did not bat", "absent")
        d["not_out"] = ho in ("not out", "retired not out", "")
        out.append(d)
    return out


def bowling_rows(conn, pid: int, mids: list[int]) -> list[dict]:
    qm = ",".join("?" * len(mids))
    rows = conn.execute(f"""
        SELECT match_id, innings_seq, bowl_position, overs, maidens,
               runs, wides, no_balls, wickets
        FROM bowling
        WHERE bowler_id = ? AND team_bowling_club_id = ?
          AND match_id IN ({qm})
        ORDER BY match_id, innings_seq
    """, (pid, L.RAINHAM_CLUB_ID, *mids)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        # `overs` is a string like '10' or '10.3' (3 balls into the 11th).
        try:
            ovs = float(d.get("overs") or 0)
            whole = int(ovs)
            frac  = round((ovs - whole) * 10)
            d["overs_decimal"] = whole + frac / 6
            d["legal_balls"] = whole * 6 + frac
        except (TypeError, ValueError):
            d["overs_decimal"] = 0.0
            d["legal_balls"] = 0
        out.append(d)
    return out


# ---------------------------------------------------------- ball rows --

def _meta_lookup(all_meta: dict[int, dict]) -> dict[int, dict]:
    """Compact lookup: pid -> dict with only the fields we snapshot."""
    out: dict[int, dict] = {}
    for pid, blob in all_meta.items():
        m = (blob or {}).get("metadata") or {}
        out[int(pid)] = {
            "batting_hand": m.get("batting_hand"),
            "bowling_type": m.get("bowling_type"),
            "bowling_arm":  m.get("bowling_arm"),
            "pace_type":    m.get("pace_type"),
            "spin_type":    m.get("spin_type"),
        }
    return out


def balls_faced(conn, pid: int, mids: list[int],
                meta_lookup: dict[int, dict]) -> list[dict]:
    qm = ",".join("?" * len(mids))
    rows = conn.execute(f"""
        SELECT match_id, innings_seq, over_no, ball_no, ball_no_disp,
               is_legal_ball, runs_bat, runs_extra, extras_type,
               bowler_id, dismissed_batter_id
        FROM balls
        WHERE batter_id = ? AND match_id IN ({qm})
        ORDER BY match_id, innings_seq, over_no, ball_no
    """, (pid, *mids)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["wicket"] = 1 if d.get("dismissed_batter_id") == pid else 0
        bm = meta_lookup.get(int(d["bowler_id"])) if d.get("bowler_id") else None
        d["bowling_type"] = (bm or {}).get("bowling_type")
        d["bowling_arm"]  = (bm or {}).get("bowling_arm")
        d["pace_type"]    = (bm or {}).get("pace_type")
        d["spin_type"]    = (bm or {}).get("spin_type")
        d.pop("dismissed_batter_id", None)
        out.append(d)
    return out


def balls_bowled(conn, pid: int, mids: list[int],
                 meta_lookup: dict[int, dict]) -> list[dict]:
    qm = ",".join("?" * len(mids))
    rows = conn.execute(f"""
        SELECT match_id, innings_seq, over_no, ball_no, ball_no_disp,
               is_legal_ball, runs_bat, runs_extra, extras_type,
               batter_id, dismissed_batter_id
        FROM balls
        WHERE bowler_id = ? AND match_id IN ({qm})
        ORDER BY match_id, innings_seq, over_no, ball_no
    """, (pid, *mids)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["wicket"] = 1 if d.get("dismissed_batter_id") else 0
        bm = meta_lookup.get(int(d["batter_id"])) if d.get("batter_id") else None
        d["batting_hand"] = (bm or {}).get("batting_hand")
        d.pop("dismissed_batter_id", None)
        out.append(d)
    return out


# ---------------------------------------------------------- per-player bundle --

def player_bundle(conn, pid: int, mids: list[int],
                  match_metas: dict[int, dict],
                  aliases: dict[int, list[str]],
                  meta_lookup: dict[int, dict]) -> dict:
    bat = batting_rows(conn, pid, mids)
    bowl = bowling_rows(conn, pid, mids)
    faced = balls_faced(conn, pid, mids, meta_lookup)
    bowled = balls_bowled(conn, pid, mids, meta_lookup)

    # Trim match metadata to only matches the player actually appears in.
    seen = set()
    for r in bat:   seen.add(int(r["match_id"]))
    for r in bowl:  seen.add(int(r["match_id"]))
    # Also include matches where they were on the squad but DNB and didn't bowl
    qm = ",".join("?" * len(mids))
    for r in conn.execute(f"""
        SELECT match_id FROM match_players
        WHERE player_id = ? AND club_id = ? AND match_id IN ({qm})
    """, (pid, L.RAINHAM_CLUB_ID, *mids)).fetchall():
        seen.add(int(r[0]))
    matches = [match_metas[m] for m in sorted(seen) if m in match_metas]

    name = (aliases.get(pid) or [f"#{pid}"])[0]

    return {
        "player_id": pid,
        "name": name,
        "aliases": aliases.get(pid, []),
        "matches": matches,
        "batting": bat,
        "bowling": bowl,
        "balls_faced":  faced,
        "balls_bowled": bowled,
        "pc_url": PC_PLAYER_URL.format(pid=pid),
    }


# ---------------------------------------------------------- team aggregates --

def _bat_emptier():
    return {"inns":0, "runs":0, "runsWithBalls":0, "balls":0, "nots":0}

def _bat_accum(s, r):
    if r.get("did_not_bat"):
        return
    s["inns"] += 1
    runs = r.get("runs") or 0
    s["runs"] += runs
    bls = r.get("balls") or 0
    if bls:
        s["balls"] += bls
        s["runsWithBalls"] += runs
    if r.get("not_out"):
        s["nots"] += 1

def _bat_fin(s):
    dis = s["inns"] - s["nots"]
    return {
        "inns": s["inns"],
        "avg":  (s["runs"] / dis) if dis else None,
        "sr":   (s["runsWithBalls"] / s["balls"] * 100) if s["balls"] else None,
    }

def _bowl_emptier():
    return {"sp":0, "balls":0, "runs":0, "wkts":0}

def _bowl_accum(s, r):
    s["sp"] += 1
    s["balls"] += r.get("legal_balls") or 0
    s["runs"]  += r.get("runs") or 0
    s["wkts"]  += r.get("wickets") or 0

def _bowl_fin(s):
    return {
        "sp": s["sp"],
        "avg":  (s["runs"] / s["wkts"]) if s["wkts"] else None,
        "econ": (s["runs"] / s["balls"] * 6) if s["balls"] else None,
        "sr":   (s["balls"] / s["wkts"]) if s["wkts"] else None,
    }


def _ball_empty():
    return {"balls":0, "legal":0, "runs_bat":0, "runs_extra":0,
            "wickets":0, "dots":0}

def _ball_accum(s, b):
    s["balls"] += 1
    legal = int(b.get("is_legal_ball") or 0)
    rb = int(b.get("runs_bat") or 0)
    re = int(b.get("runs_extra") or 0)
    s["runs_bat"]   += rb
    s["runs_extra"] += re
    if legal:
        s["legal"] += 1
        if rb == 0 and re == 0:
            s["dots"] += 1
    if int(b.get("wicket") or 0):
        s["wickets"] += 1

def _ball_fin_bat(s):
    return {
        "balls": s["balls"],
        "sr":  (s["runs_bat"] / s["legal"] * 100) if s["legal"] else None,
        "avg": (s["runs_bat"] / s["wickets"]) if s["wickets"] else None,
    }

def _ball_fin_bowl(s):
    return {
        "balls": s["balls"],
        "econ": ((s["runs_bat"] + s["runs_extra"]) / s["legal"] * 6)
                if s["legal"] else None,
        "avg":  ((s["runs_bat"] + s["runs_extra"]) / s["wickets"])
                if s["wickets"] else None,
    }


def _phase_of(over_no: int) -> str:
    o = (over_no or 0) + 1
    if o <= 10: return "1-10"
    if o <= 20: return "11-20"
    if o <= 30: return "21-30"
    if o <= 40: return "31-40"
    return "41-50"

def _pi_bucket(legal_so_far: int) -> str:
    if legal_so_far < 10: return "0-10"
    if legal_so_far < 20: return "11-20"
    if legal_so_far < 50: return "21-50"
    if legal_so_far < 100: return "51-100"
    return "101+"


def build_team_aggregates(player_bundles: list[dict]) -> dict:
    """
    Aggregate every Rainham 1st-XI batting / bowling row + every
    Rainham-batter / Rainham-bowler ball across the universe, then
    derive per-dimension summary metrics for the dashboard hover
    tooltips.
    """
    # Pool every player's filtered universe rows.
    all_bat = []
    all_bowl = []
    all_balls_faced = []
    all_balls_bowled = []
    matches_by_id = {}
    for b in player_bundles:
        for m in b["matches"]:
            matches_by_id[m["match_id"]] = m
        all_bat.extend(b["batting"])
        all_bowl.extend(b["bowling"])
        all_balls_faced.extend(b["balls_faced"])
        all_balls_bowled.extend(b["balls_bowled"])

    def by_match_attr(rows, attr_fn, accum, fin):
        out = {}
        for r in rows:
            m = matches_by_id.get(r["match_id"])
            if not m:
                continue
            key = attr_fn(m)
            if key is None or key == "":
                continue
            s = out.setdefault(str(key), accum())
            (_bat_accum if accum is _bat_emptier else _bowl_accum)(s, r)
        return {k: fin(v) for k, v in out.items()}

    def by_row_attr(rows, attr_fn, accum, fin):
        out = {}
        for r in rows:
            key = attr_fn(r)
            if key is None or key == "":
                continue
            s = out.setdefault(str(key), accum())
            (_bat_accum if accum is _bat_emptier else _bowl_accum)(s, r)
        return {k: fin(v) for k, v in out.items()}

    def by_ball_attr(balls, attr_fn, fin):
        out = {}
        for b in balls:
            key = attr_fn(b)
            if key is None:
                continue
            s = out.setdefault(str(key), _ball_empty())
            _ball_accum(s, b)
        return {k: fin(v) for k, v in out.items()}

    # Career
    bat_total  = _bat_emptier()
    bowl_total = _bowl_emptier()
    for r in all_bat:  _bat_accum(bat_total, r)
    for r in all_bowl: _bowl_accum(bowl_total, r)

    # By each match-level dimension
    out = {
        "career": {
            "bat":  _bat_fin(bat_total),
            "bowl": _bowl_fin(bowl_total),
        },
        "by_season": {
            "bat":  by_match_attr(all_bat,  lambda m: m["season"],
                                  _bat_emptier, _bat_fin),
            "bowl": by_match_attr(all_bowl, lambda m: m["season"],
                                  _bowl_emptier, _bowl_fin),
        },
        "by_home_away": {
            "bat":  by_match_attr(all_bat,  lambda m: m["home_away"],
                                  _bat_emptier, _bat_fin),
            "bowl": by_match_attr(all_bowl, lambda m: m["home_away"],
                                  _bowl_emptier, _bowl_fin),
        },
        "by_result": {
            "bat":  by_match_attr(all_bat,  lambda m: m["result"],
                                  _bat_emptier, _bat_fin),
            "bowl": by_match_attr(all_bowl, lambda m: m["result"],
                                  _bowl_emptier, _bowl_fin),
        },
        "by_competition": {
            "bat":  by_match_attr(all_bat,  lambda m: m["competition"],
                                  _bat_emptier, _bat_fin),
            "bowl": by_match_attr(all_bowl, lambda m: m["competition"],
                                  _bowl_emptier, _bowl_fin),
        },
        "by_bat_first": {
            "bat":  by_match_attr(all_bat,
                                  lambda m: "yes" if m["bat_first"] else "no",
                                  _bat_emptier, _bat_fin),
            "bowl": by_match_attr(all_bowl,
                                  lambda m: "yes" if m["bat_first"] else "no",
                                  _bowl_emptier, _bowl_fin),
        },
        "by_toss": {
            "bat":  by_match_attr(all_bat,
                                  lambda m: "won" if m["toss_won"] else "lost",
                                  _bat_emptier, _bat_fin),
            "bowl": by_match_attr(all_bowl,
                                  lambda m: "won" if m["toss_won"] else "lost",
                                  _bowl_emptier, _bowl_fin),
        },
        "by_position": by_row_attr(all_bat, lambda r: r.get("position"),
                                   _bat_emptier, _bat_fin),
        # BBB-derived
        "by_phase_bat":  by_ball_attr(all_balls_faced,
                                      lambda b: _phase_of(b.get("over_no")),
                                      _ball_fin_bat),
        "by_phase_bowl": by_ball_attr(all_balls_bowled,
                                      lambda b: _phase_of(b.get("over_no")),
                                      _ball_fin_bowl),
        "by_vs_btype":   by_ball_attr(all_balls_faced,
                                      lambda b: b.get("bowling_type") or "unknown",
                                      _ball_fin_bat),
        "by_vs_barm":    by_ball_attr(all_balls_faced,
                                      lambda b: b.get("bowling_arm") or "unknown",
                                      _ball_fin_bat),
        "by_vs_hand":    by_ball_attr(all_balls_bowled,
                                      lambda b: b.get("batting_hand") or "unknown",
                                      _ball_fin_bowl),
    }

    # Player innings buckets need a running counter per innings.
    pi_buckets = {}
    counters = {}
    for b in all_balls_faced:
        key = (b.get("match_id"), b.get("innings_seq"))
        so = counters.get(key, 0)
        bk = pi_buckets.setdefault(_pi_bucket(so), _ball_empty())
        _ball_accum(bk, b)
        if int(b.get("is_legal_ball") or 0):
            counters[key] = so + 1
    out["by_player_innings"] = {k: _ball_fin_bat(v) for k, v in pi_buckets.items()}

    # Spell buckets — aggregate per innings, separate 1st vs later.
    spell_first = _ball_empty()
    spell_later = _ball_empty()
    by_inn = {}
    for b in all_balls_bowled:
        by_inn.setdefault((b.get("match_id"), b.get("innings_seq")), []).append(b)
    for inn_balls in by_inn.values():
        inn_balls.sort(key=lambda x: ((x.get("over_no") or 0),
                                       (x.get("ball_no") or 0)))
        spell_idx = 0
        last_over = None
        for b in inn_balls:
            o = b.get("over_no") or 0
            if last_over is None or o > last_over + 2:
                spell_idx += 1
            target = spell_first if spell_idx == 1 else spell_later
            _ball_accum(target, b)
            last_over = o
    out["by_spell"] = {
        "1st":   _ball_fin_bowl(spell_first),
        "later": _ball_fin_bowl(spell_later),
    }

    return out


# ---------------------------------------------------------- index summary --

def _summarise(b: dict) -> dict:
    n_apps = len(b["matches"])
    runs = sum((r.get("runs") or 0) for r in b["batting"]
               if not r.get("did_not_bat"))
    inns = sum(1 for r in b["batting"] if not r.get("did_not_bat"))
    wkts = sum((r.get("wickets") or 0) for r in b["bowling"])
    has_bbb = bool(b["balls_faced"]) or bool(b["balls_bowled"])
    last_match = b["matches"][-1]["match_date"] if b["matches"] else ""
    return {
        "player_id":  b["player_id"],
        "name":       b["name"],
        "apps":       n_apps,
        "inns":       inns,
        "runs":       runs,
        "wkts":       wkts,
        "has_bbb":    has_bbb,
        "n_balls_faced":  len(b["balls_faced"]),
        "n_balls_bowled": len(b["balls_bowled"]),
        "last_match": last_match,
    }


# ---------------------------------------------------------- HTML pages --

def write_chooser(rows: list[dict]) -> None:
    rows = sorted(rows, key=lambda r: (r["apps"], r["runs"]+r["wkts"]),
                  reverse=True)
    n_with_bbb = sum(1 for r in rows if r["has_bbb"])

    body = [
        L.hero(
            "RCC player dashboard",
            crumbs=[("Rainham CC", "../index.html")],
            lead=("Pick a Rainham 1st-XI player to slice their career "
                  "across home/away, position, season, opposition and "
                  "(where ball-by-ball is captured) bowler type, "
                  "innings phase and bowling spells."),
            stats=[
                (str(len(rows)), "players"),
                (str(n_with_bbb), "with BBB"),
                (f"{n_with_bbb*100//max(len(rows),1)}%", "BBB coverage"),
                (str(sum(r["apps"] for r in rows)), "appearances"),
            ],
        ),
        '<div class="card">',
        '<h2>Squad</h2>',
        '<p class="note">Sorted by appearances. The 🎯 chip means we '
        'have ball-by-ball data for at least one of their innings.</p>',
        '<input type="search" id="player-filter" '
        'placeholder="Filter by name..." autocomplete="off" '
        'autocapitalize="none" autocorrect="off" spellcheck="false" '
        'style="margin-bottom:8px">',
        '<div class="row-list" id="rcc-list">',
    ]
    for r in rows:
        bbb_chip = (f'<span class="count bbb">§ {r["n_balls_faced"]}'
                    f'/{r["n_balls_bowled"]} bbb</span>'
                    if r["has_bbb"] else "")
        meta = (f'{r["apps"]} apps · {r["runs"]} runs · {r["wkts"]} wkts'
                f' · last seen {escape(r["last_match"])}')
        body.append(
            f'<a class="row-link" href="player.html?id={r["player_id"]}" '
            f'data-name="{escape(r["name"].lower())}">'
            f'<div class="name">{escape(r["name"])}'
            f'<div class="row-meta">{meta}</div></div>'
            f'<div class="right">{bbb_chip}</div>'
            f'</a>'
        )
    body.append("</div></div>")
    body.append("""<script>
(function(){
  var inp=document.getElementById('player-filter');
  var list=document.getElementById('rcc-list');
  if(!inp||!list) return;
  var rows=list.querySelectorAll('.row-link');
  inp.addEventListener('input',function(){
    var q=inp.value.trim().toLowerCase();
    for(var i=0;i<rows.length;i++){
      var n=rows[i].getAttribute('data-name')||'';
      rows[i].style.display=(!q||n.indexOf(q)>=0)?'':'none';
    }
  });
})();
</script>""")
    out = RCC_PAGE_DIR / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(L.page("".join(body), title="RCC dashboard",
        css_href="../static/app.css"))


def write_player_template() -> None:
    body = (
        '<header class="hero" id="hero">'
        '<div class="crumbs">'
        '<a href="../index.html">Rainham CC</a>'
        '<span class="sep">&rsaquo;</span>'
        '<a href="index.html">Squad</a>'
        '<span class="sep">&rsaquo;</span>'
        '<span id="crumb-name">Player</span>'
        '</div>'
        '<h1 id="player-name">Loading…</h1>'
        '<p class="lead" id="player-summary"></p>'
        '<div class="tabs" id="tabs"></div>'
        '<div id="player-stats"></div>'
        '<div class="bbb-bar-wrap" id="bbb-bar"></div>'
        '</header>'
        '<main>'
        '<div class="filter-panel" id="filter-panel"></div>'
        '<div id="cards"></div>'
        '</main>'
    )
    out = RCC_PAGE_DIR / "player.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(L.page(body, title="RCC player",
        css_href="../static/app.css",
        extra_body='<script src="../static/rcc.js"></script>'))


# ---------------------------------------------------------- main --

def build() -> int:
    # Dashboard CSS lives inside _app_lib.CSS itself so build order
    # doesn't matter (build_data_repo.py / build_metadata.py both call
    # write_static_assets() and would otherwise drop it).
    L.write_static_assets()

    conn = L.open_db()
    mids = relevant_match_ids(conn)
    if not mids:
        print("No relevant matches found — abort.", file=sys.stderr)
        return 1

    print(f"Universe: {len(mids)} matches")
    pids = rcc_player_ids(conn, mids)
    print(f"Rainham 1st-XI players: {len(pids)}")

    all_meta = L.load_all_player_meta()
    meta_lookup = _meta_lookup(all_meta)

    # Aliases: most-recent-first batsman_name from the batting table.
    cur = conn.cursor()
    rows = cur.execute("""
        SELECT batsman_id AS pid, batsman_name AS name,
               match_id
        FROM batting
        WHERE batsman_id IS NOT NULL AND batsman_name <> ''
        ORDER BY match_id DESC
    """).fetchall()
    aliases: dict[int, list[str]] = {}
    for r in rows:
        lst = aliases.setdefault(r["pid"], [])
        if r["name"] not in lst:
            lst.append(r["name"])

    metas = match_meta(conn, mids)

    # Per-player bundles
    RCC_DATA_DIR.mkdir(parents=True, exist_ok=True)
    summaries: list[dict] = []
    bundles: list[dict] = []
    for pid in pids:
        b = player_bundle(conn, pid, mids, metas, aliases, meta_lookup)
        if not b["batting"] and not b["bowling"]:
            continue   # squad-only, no innings yet
        out = RCC_DATA_DIR / f"{pid}.json"
        out.write_text(json.dumps(b, separators=(",", ":")))
        summaries.append(_summarise(b))
        bundles.append(b)

    # Index summary
    (RCC_DATA_DIR / "index.json").write_text(
        json.dumps(summaries, separators=(",", ":"))
    )

    # Team aggregates — used by the dashboard for per-cell hover
    # comparisons against the 1st-XI baseline.
    team = build_team_aggregates(bundles)
    (RCC_DATA_DIR / "team.json").write_text(
        json.dumps(team, separators=(",", ":"))
    )

    # Static pages
    write_chooser(summaries)
    write_player_template()

    # Dashboard JS (rcc.js) — written separately to keep the main
    # builder focused on data plumbing.
    from build_rcc_js import RCC_JS
    (L.APP_DIR / "static" / "rcc.js").write_text(RCC_JS)

    n_with_bbb = sum(1 for s in summaries if s["has_bbb"])
    print(f"Wrote {len(summaries)} player bundles "
          f"({n_with_bbb} with BBB)")
    return 0


if __name__ == "__main__":
    sys.exit(build())
