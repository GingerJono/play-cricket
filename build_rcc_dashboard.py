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
    """W/L/D/T/NR/A from Rainham's perspective."""
    res = (m.get("result") or "").strip().lower()
    applied_to = m.get("result_applied_to") or ""
    if not res:
        return ""    # unplayed
    if res in ("won", "win"):
        return "W" if applied_to == "home" and m["home_club_id"] == L.RAINHAM_CLUB_ID \
            or applied_to == "away" and m["away_club_id"] == L.RAINHAM_CLUB_ID else "L"
    if res in ("lost", "lose", "loss"):
        return "L" if applied_to == "home" and m["home_club_id"] == L.RAINHAM_CLUB_ID \
            or applied_to == "away" and m["away_club_id"] == L.RAINHAM_CLUB_ID else "W"
    if res in ("tie", "tied"):    return "T"
    if res in ("drawn", "draw"):  return "D"
    if res in ("abandoned",):     return "A"
    return "NR"


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
        bat_first = (m.get("batted_first") or "").strip()
        rainham_batted_first = (
            (bat_first == "Home" and rainham_is_home)
            or (bat_first == "Away" and not rainham_is_home)
        )
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
        '<div class="stats" id="player-stats"></div>'
        '<div class="bbb-bar-wrap" id="bbb-bar"></div>'
        '</header>'
        '<main>'
        '<div class="card" id="slicer-card">'
        '<h2>Slicers</h2>'
        '<p class="note">Tap to toggle. All slicers AND together; '
        'multiple chips within one slicer OR.</p>'
        '<div id="slicer-rail"></div>'
        '<div class="filter-bar"><span id="filter-summary"></span>'
        '<button id="reset-filters" class="btn-link">Reset</button></div>'
        '</div>'
        '<div id="cards"></div>'
        '</main>'
    )
    out = RCC_PAGE_DIR / "player.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(L.page(body, title="RCC player",
        css_href="../static/app.css",
        extra_body='<script src="../static/rcc.js"></script>'))


# ---------------------------------------------------------- supplementary CSS --

# The dashboard reuses the global hero/card vocabulary from app.css and
# adds a couple of bits of CSS for slicers + bucket bars + volume chips.
RCC_EXTRA_CSS = """
/* Dashboard extras — reuses global app.css palette tokens. */

.bbb-bar-wrap{margin-top:14px;border:1px solid var(--rule);
  background:repeating-linear-gradient(
    90deg,var(--paper-2) 0 4px,transparent 4px 8px);height:20px;
  position:relative;overflow:hidden}
.bbb-bar-wrap > .fill{display:block;height:100%;background:var(--ball);
  width:0;animation:bbbWipe .8s cubic-bezier(.2,.8,.2,1) forwards}
.bbb-bar-wrap > .lbl{position:absolute;inset:0;display:flex;
  align-items:center;justify-content:center;
  font-family:'JetBrains Mono',monospace;font-size:10.5px;font-weight:700;
  color:var(--ink);letter-spacing:.06em}
@keyframes bbbWipe{from{width:0}to{width:var(--w,0%)}}

#slicer-rail{display:flex;flex-direction:column;gap:10px;margin-top:8px}
.slicer-group{border-top:1px solid var(--rule);padding-top:8px}
.slicer-group:first-child{border-top:none;padding-top:0}
.slicer-group .lbl{
  font-family:'Source Serif Pro',Georgia,serif;
  font-size:9.5px;text-transform:uppercase;letter-spacing:.18em;
  color:var(--muted);font-weight:700;margin-bottom:6px}
.slicer-chips{display:flex;flex-wrap:wrap;gap:5px}
.slicer-chip{display:inline-flex;align-items:center;gap:4px;
  padding:4px 10px;background:var(--paper);border:1px solid var(--rule-2);
  font-family:'JetBrains Mono',monospace;font-size:11px;
  font-weight:600;color:var(--ink-2);cursor:pointer;user-select:none;
  transition:background .12s,border-color .12s,color .12s}
.slicer-chip:hover{border-color:var(--ball);color:var(--ball)}
.slicer-chip.on{background:var(--ball);color:var(--paper);
  border-color:var(--ball-2)}
.slicer-chip.dim{opacity:.45;cursor:not-allowed}
.slicer-chip .n{font-weight:400;opacity:.75;font-size:10px}
.slicer-chip.on .n{opacity:.8}

.filter-bar{display:flex;justify-content:space-between;align-items:center;
  margin-top:10px;padding-top:10px;border-top:1px solid var(--rule);
  font-family:'Source Serif Pro',Georgia,serif;font-style:italic;
  font-size:12.5px;color:var(--muted)}
.btn-link{background:none;border:none;color:var(--ball);
  font-family:'Source Serif Pro',Georgia,serif;font-size:12.5px;
  font-weight:700;letter-spacing:.05em;cursor:pointer;
  text-decoration:underline}

.kv-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));
  gap:14px 18px;margin:8px 0 0}
.kv-grid > div{border-left:2px solid var(--ball);padding-left:10px}
.kv-grid .k{font-size:9.5px;text-transform:uppercase;letter-spacing:.18em;
  color:var(--muted);font-weight:700;
  font-family:'Source Serif Pro',Georgia,serif}
.kv-grid .v{font-family:'JetBrains Mono',monospace;font-size:18px;
  font-weight:700;color:var(--ink);margin-top:2px;letter-spacing:-.02em}
.kv-grid .vol{font-size:10.5px;color:var(--muted);margin-top:2px;
  font-family:'Source Serif Pro',Georgia,serif;font-style:italic}

.bucket-table{width:100%;font-family:'JetBrains Mono',monospace;
  font-size:11.5px;font-variant-numeric:tabular-nums;border-collapse:collapse}
.bucket-table th{
  font-family:'Source Serif Pro',Georgia,serif;font-size:9.5px;
  text-transform:uppercase;letter-spacing:.14em;color:var(--muted);
  font-weight:700;padding:6px 6px;border-bottom:2px solid var(--ball);
  text-align:right}
.bucket-table th:first-child{text-align:left}
.bucket-table td{padding:7px 6px;border-bottom:1px solid var(--rule);
  text-align:right}
.bucket-table td:first-child{text-align:left;color:var(--ink);font-weight:700}
.bucket-table tr:last-child td{border-bottom:none}
.bucket-table .empty td{color:var(--muted);opacity:.55;font-style:italic}

.bucket-bars{display:flex;flex-direction:column;gap:6px;margin-top:8px}
.bucket-bar{display:flex;align-items:center;gap:8px;font-size:11.5px;
  font-family:'JetBrains Mono',monospace}
.bucket-bar .lbl{flex:0 0 56px;color:var(--muted);font-size:10px;
  letter-spacing:.05em;text-transform:uppercase;font-weight:700}
.bucket-bar .bar{flex:1;height:11px;background:var(--paper-2);
  border:1px solid var(--rule);overflow:hidden}
.bucket-bar .bar > span{display:block;height:100%;background:var(--ball);
  transition:width .35s cubic-bezier(.2,.8,.2,1)}
.bucket-bar .bar.pitch > span{background:var(--pitch)}
.bucket-bar .num{flex:0 0 70px;text-align:right;
  font-variant-numeric:tabular-nums;color:var(--ink);font-weight:600}

.vol-chip{display:inline-block;padding:1px 7px;border:1px solid var(--ball);
  background:transparent;color:var(--ball);
  font-family:'JetBrains Mono',monospace;font-size:10px;font-weight:700;
  letter-spacing:.04em;margin-left:6px;
  font-variant-numeric:tabular-nums}

.fix-list .pill{flex-shrink:0}
.fix-list .fix .opp{font-size:14.5px}
.empty-note{color:var(--muted);font-style:italic;font-size:13px;
  padding:10px 0}
"""


# ---------------------------------------------------------- main --

def build() -> int:
    L.write_static_assets()
    # Append our extras to app.css so the rcc pages get them too
    css_path = L.APP_DIR / "static" / "app.css"
    base = css_path.read_text() if css_path.exists() else L.CSS
    css_path.write_text(base + "\n\n" + RCC_EXTRA_CSS)

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
    for pid in pids:
        b = player_bundle(conn, pid, mids, metas, aliases, meta_lookup)
        if not b["batting"] and not b["bowling"]:
            continue   # squad-only, no innings yet
        out = RCC_DATA_DIR / f"{pid}.json"
        out.write_text(json.dumps(b, separators=(",", ":")))
        summaries.append(_summarise(b))

    # Index summary
    (RCC_DATA_DIR / "index.json").write_text(
        json.dumps(summaries, separators=(",", ":"))
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
