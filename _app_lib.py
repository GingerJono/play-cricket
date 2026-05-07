"""
Shared chrome + helpers for the static `app/` site.

Both `build_data_repo.py` (10-year game list) and `build_metadata.py`
(opposition metadata browse + submit form) read from `data/rainham.db`
+ `data/metadata/` and emit pages under `app/`. They share:

  * the database connection helper
  * one CSS string and one page <html> wrapper
  * the player-metadata-status function
  * a few small SQL utilities

Keep this file dependency-free (stdlib only).
"""

from __future__ import annotations

import json
import sqlite3
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "rainham.db"
META_DIR = ROOT / "data" / "metadata"
PLAYERS_META_DIR = META_DIR / "players"
VIDEOS_META_DIR = META_DIR / "videos"
SUBMISSIONS_META_DIR = META_DIR / "submissions"
APP_DIR = ROOT / "app"

RAINHAM_CLUB_ID = "5251"
RAINHAM_FIRST_XI_TEAM_ID = "51207"   # Saturday 1st XI

# competition_name patterns that mark a Cup as a T20 / 20-over format —
# we exclude these per the user's spec ("League games + Cup games where
# they are not T20"). Match on lowercased competition_name; the `%`
# placeholders are SQL LIKE wildcards.
T20_NAME_PATTERNS = (
    "%t20%", "%twenty20%", "%twenty 20%", "%20/20%", "%20-20%",
    "%smash%",            # 'Essex SMASH T20 ...'
)


def first_xi_fixture_where(*, alias: str = "m",
                            include_unplayed: bool = False) -> tuple[str, list]:
    """
    Returns (sql_fragment, params) suitable for a WHERE clause that
    filters `matches` (aliased as `alias`) down to the fixtures we
    care about for the metadata project:

      * Rainham 1st XI on either side
      * League games (always) OR Cup games whose competition_name does
        NOT match any T20 / 20-over pattern
      * (default) result <> ''  — i.e. played

    Caller is responsible for adding `WHERE` / `AND` glue.
    """
    a = alias
    parts = [
        f"({a}.home_team_id = ? OR {a}.away_team_id = ?)",
        f"("
        f"  {a}.competition_type = 'League'"
        f"  OR ({a}.competition_type = 'Cup' AND "
        + " AND ".join(
            f"lower({a}.competition_name) NOT LIKE ?" for _ in T20_NAME_PATTERNS
        )
        + "))",
    ]
    params: list = [RAINHAM_FIRST_XI_TEAM_ID, RAINHAM_FIRST_XI_TEAM_ID]
    params.extend(T20_NAME_PATTERNS)
    if not include_unplayed:
        parts.append(f"{a}.result <> ''")
    return " AND ".join(parts), params


def first_xi_match_ids(conn, *, include_unplayed: bool = False,
                        season_min: int | None = None) -> list[int]:
    """List of match_ids matching the 1st-XI / League+non-T20-Cup filter."""
    sql_filter, params = first_xi_fixture_where(include_unplayed=include_unplayed)
    sql = f"SELECT match_id FROM matches m WHERE {sql_filter}"
    if season_min is not None:
        sql += " AND m.season >= ?"; params.append(season_min)
    return [int(r[0]) for r in conn.execute(sql, params).fetchall()]

# ---------------------------------------------------------------------- DB --

def open_db() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise SystemExit(
            f"{DB_PATH} not found — run `python3 build_db.py` first."
        )
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------- player metadata model --

REQUIRED_FIELDS_ALWAYS = ("batting_hand", "bowling_type")

def metadata_status(meta: dict | None) -> str:
    """
    'complete' / 'partial' / 'not captured' — purely from the metadata blob
    of one player file. The 'needs review' / 'conflicting' overlays are
    applied later (they need the submissions queue).

    'unknown' counts as populated. None / "" / missing keys are missing.
    """
    if not meta:
        return "not captured"
    def has(k: str) -> bool:
        v = meta.get(k)
        return v not in (None, "")
    for k in REQUIRED_FIELDS_ALWAYS:
        if not has(k):
            return "partial"
    bt = meta.get("bowling_type")
    if bt in ("pace", "spin"):
        if not (has("bowling_arm") and has("angle_to_rhb")):
            return "partial"
        if bt == "pace" and not has("pace_type"):
            return "partial"
        if bt == "spin" and not has("spin_type"):
            return "partial"
    return "complete"


def load_player_meta(player_id: int) -> dict | None:
    p = PLAYERS_META_DIR / f"{player_id}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def load_all_player_meta() -> dict[int, dict]:
    out: dict[int, dict] = {}
    if not PLAYERS_META_DIR.exists():
        return out
    for p in PLAYERS_META_DIR.glob("*.json"):
        try:
            d = json.loads(p.read_text())
            pid = int(d.get("player_id"))
            out[pid] = d
        except Exception:
            continue
    return out


def load_videos_for(match_id: int) -> list[dict]:
    p = VIDEOS_META_DIR / f"{match_id}.json"
    if not p.exists():
        return []
    try:
        return (json.loads(p.read_text()) or {}).get("videos") or []
    except Exception:
        return []


def matches_with_videos() -> set[int]:
    out: set[int] = set()
    if not VIDEOS_META_DIR.exists():
        return out
    for p in VIDEOS_META_DIR.glob("*.json"):
        try:
            out.add(int(p.stem))
        except ValueError:
            pass
    return out


def covered_player_ids() -> set[int]:
    """Players whose metadata file resolves to status 'complete'."""
    return {
        pid for pid, d in load_all_player_meta().items()
        if metadata_status(d.get("metadata")) == "complete"
    }


# ---------------------------------------------------------- date utilities --

def date_yyyymmdd(ddmmyyyy: str) -> str:
    parts = (ddmmyyyy or "").split("/")
    if len(parts) != 3:
        return ""
    return parts[2] + parts[1] + parts[0]


def season_of(ddmmyyyy: str) -> int | None:
    parts = (ddmmyyyy or "").split("/")
    if len(parts) != 3:
        return None
    try:
        return int(parts[2])
    except ValueError:
        return None


# -------------------------------------------------------------- HTML chrome --

CSS = """
/*
  Clean light analytics theme. White cards on a cool-grey ground,
  pitch-green for the primary accent, brick red only for negative
  signal (wickets, losses). IBM Plex Sans + IBM Plex Mono throughout.
  No shadows; tight rules; tabular numerics on every stat.
*/

:root{
  --bg:#f1f4f7;
  --surface:#ffffff;
  --surface-2:#f7f9fb;
  --ink:#0d1219;
  --ink-2:#1f2937;
  --muted:#5d6878;
  --rule:#e1e6ed;
  --rule-2:#c2cad4;
  --accent:#0d7a52;
  --accent-2:#095a3c;
  --accent-soft:#dcefe4;
  --warn:#b42318;
  --warn-soft:#fde7e3;
  --info:#1d4ed8;
  --info-soft:#dde7fb;
  --bar-1:#0d7a52;
  --bar-2:#1d4ed8;
  --bar-3:#b42318;
  --tile-bg:#0d1219;
  --tile-ink:#f1f4f7;
}

*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{
  padding:14px 14px 32px;
  font-family:"IBM Plex Sans","Segoe UI",system-ui,sans-serif;
  font-size:14.5px;line-height:1.45;color:var(--ink);
  background:var(--bg);
  max-width:540px;margin:0 auto;
  -webkit-font-smoothing:antialiased;
  text-rendering:optimizeLegibility;
  font-feature-settings:"kern","liga","tnum";
}

a{color:var(--accent);text-decoration:none;
  border-bottom:1px solid rgba(13,122,82,.25);
  transition:color .15s,border-color .15s}
a:hover{color:var(--accent-2);border-bottom-color:var(--accent-2)}

::selection{background:var(--accent-soft);color:var(--ink)}

/* ----------------------------------- hero ------------------------------ */
.hero{
  background:transparent;color:var(--ink);
  padding:14px 0 18px;margin:0 0 14px;
  border-bottom:1px solid var(--rule);
}
.hero .crumbs{
  font-family:"IBM Plex Mono",monospace;
  font-size:10px;text-transform:uppercase;letter-spacing:.14em;
  font-weight:500;color:var(--muted);margin-bottom:10px;
}
.hero .crumbs a{color:var(--muted);border-bottom:none}
.hero .crumbs a:hover{color:var(--accent)}
.hero .crumbs .sep{margin:0 6px;opacity:.55}
.hero h1{
  font-family:"IBM Plex Sans",sans-serif;
  font-size:26px;line-height:1.1;margin:0 0 6px;
  font-weight:700;letter-spacing:-.02em;color:var(--ink);
}
.hero p.lead{
  font-size:13.5px;margin:0;color:var(--muted);
  line-height:1.5;max-width:48ch;
}
.hero .stats{
  display:grid;grid-template-columns:repeat(4,minmax(0,1fr));
  gap:1px;margin-top:14px;background:var(--rule);
  border:1px solid var(--rule);
}
.hero .stat{
  background:var(--surface);padding:9px 10px;text-align:left;
}
.hero .stat .n{
  font-family:"IBM Plex Mono",monospace;
  font-size:18px;font-weight:600;line-height:1;color:var(--ink);
  font-variant-numeric:tabular-nums lining-nums;letter-spacing:-.02em;
}
.hero .stat .lbl{
  font-family:"IBM Plex Mono",monospace;
  font-size:9px;text-transform:uppercase;letter-spacing:.14em;
  color:var(--muted);margin-top:5px;font-weight:500;
}

/* ----------------------------------- cards ----------------------------- */
.card{
  background:var(--surface);
  border:1px solid var(--rule);
  border-radius:6px;
  padding:14px 14px;margin:0 0 12px;
  box-shadow:none;
  position:relative;
}
.card > h2:first-child{margin-top:0}
.card-tight{padding:10px 12px}

h2{
  font-family:"IBM Plex Sans",sans-serif;
  font-size:14px;margin:14px 0 10px;padding:0;
  font-weight:600;letter-spacing:-.005em;color:var(--ink);
  display:flex;align-items:center;gap:8px;
}
h3{
  font-family:"IBM Plex Mono",monospace;
  font-size:10px;margin:14px 0 6px;color:var(--muted);
  text-transform:uppercase;letter-spacing:.14em;font-weight:600;
}
p{font-size:13.5px;margin:6px 0 8px;color:var(--ink-2)}
p.meta,p.note{
  color:var(--muted);font-size:12px;margin:0 0 10px;line-height:1.45;
}

/* ----------------------------------- status tags ----------------------- */
.tag{
  display:inline-block;padding:2px 8px;border-radius:4px;
  font-family:"IBM Plex Mono",monospace;
  font-size:10px;font-weight:600;line-height:14px;
  letter-spacing:.06em;text-transform:uppercase;
  border:1px solid transparent;
}
.tag.complete{background:var(--accent-soft);border-color:var(--accent);color:var(--accent-2)}
.tag.partial {background:var(--info-soft);border-color:var(--info);color:var(--info)}
.tag.notcap  {background:var(--surface-2);border-color:var(--rule-2);color:var(--muted)}
.tag.review  {background:var(--info-soft);border-color:var(--info);color:var(--info)}
.tag.conflict{background:var(--warn-soft);border-color:var(--warn);color:var(--warn)}
.tag.yes     {background:var(--accent-soft);border-color:var(--accent);color:var(--accent-2);padding:2px 7px}
.tag.no      {background:var(--surface-2);border-color:var(--rule-2);color:var(--muted);padding:2px 7px}

/* ----------------------------------- result pill ----------------------- */
.pill{
  display:inline-block;padding:2px 7px;border-radius:3px;
  font-family:"IBM Plex Mono",monospace;
  font-size:10.5px;font-weight:600;color:#ffffff;min-width:20px;
  text-align:center;line-height:14px;letter-spacing:.04em;
}
.pill.W{background:var(--accent)}
.pill.L{background:var(--warn)}
.pill.D,.pill.T{background:var(--ink-2)}
.pill.NR,.pill.A{background:var(--muted)}

/* ----------------------------------- coverage bar (data repo) ---------- */
.cov-row{display:flex;align-items:center;gap:10px;margin:5px 0;font-size:11.5px}
.cov-row .lbl{
  flex:0 0 64px;font-family:"IBM Plex Mono",monospace;
  font-size:9.5px;color:var(--muted);font-weight:600;
  text-transform:uppercase;letter-spacing:.1em;
}
.cov-row .bar{
  flex:1;height:6px;background:var(--surface-2);
  border:1px solid var(--rule);border-radius:3px;overflow:hidden;
}
.cov-row .bar > span{display:block;height:100%;background:var(--accent);
  transition:width .35s cubic-bezier(.2,.8,.2,1)}
.cov-row .bar.zero > span{background:var(--rule-2)}
.cov-row .bar.low  > span{background:var(--info)}
.cov-row .num{
  flex:0 0 42px;text-align:right;
  font-family:"IBM Plex Mono",monospace;
  font-variant-numeric:tabular-nums;
  color:var(--ink);font-weight:600;font-size:11.5px;
}

/* ----------------------------------- fixture cards (data repo) --------- */
.fix-list{display:flex;flex-direction:column;gap:0;margin:8px 0 0}
.fix{
  background:transparent;border:none;
  border-bottom:1px solid var(--rule);
  border-radius:0;padding:11px 0;
  box-shadow:none;
}
.fix:last-child{border-bottom:none}
.fix .row1{
  display:flex;justify-content:space-between;align-items:flex-start;
  gap:10px;margin-bottom:4px;
}
.fix .row1 .left{display:flex;flex-direction:column;gap:2px;min-width:0}
.fix .date{
  font-family:"IBM Plex Mono",monospace;
  font-size:10.5px;color:var(--muted);font-weight:500;
  letter-spacing:.04em;
}
.fix .opp{
  font-family:"IBM Plex Sans",sans-serif;
  font-size:14.5px;font-weight:600;color:var(--ink);
  white-space:normal;word-break:break-word;line-height:1.25;
  letter-spacing:-.01em;
}
.fix .opp a{color:var(--ink);border-bottom:1px solid var(--rule-2)}
.fix .opp a:hover{color:var(--accent);border-bottom-color:var(--accent)}
.fix .right{display:flex;align-items:center;gap:6px;flex-shrink:0;flex-wrap:wrap;justify-content:flex-end}
.fix .meta{
  font-size:11.5px;color:var(--muted);margin:2px 0 6px;
  font-family:"IBM Plex Mono",monospace;
}

/* ----------------------------------- row-list -------------------------- */
.row-list{display:flex;flex-direction:column;gap:0;margin:6px 0 0;
  border-top:1px solid var(--rule)}
.row-link{
  display:flex;align-items:center;justify-content:space-between;
  gap:12px;background:transparent;
  border:none;border-bottom:1px solid var(--rule);
  border-radius:0;padding:11px 4px;
  box-shadow:none;color:var(--ink);text-decoration:none;
  transition:background .12s;
}
.row-link:last-child{border-bottom:none}
.row-link:hover{background:var(--surface-2);text-decoration:none}
.row-link .name{
  font-family:"IBM Plex Sans",sans-serif;
  font-weight:600;font-size:14.5px;min-width:0;
  white-space:normal;word-break:break-word;line-height:1.25;flex:1;
  letter-spacing:-.005em;color:var(--ink);
}
.row-link .right{display:flex;align-items:center;gap:6px;flex-shrink:0;flex-wrap:wrap;justify-content:flex-end}
.row-link .count{
  font-family:"IBM Plex Mono",monospace;
  font-size:11px;color:var(--muted);font-weight:500;
  font-variant-numeric:tabular-nums;letter-spacing:.02em;
}
.row-link .count.bbb{color:var(--accent);font-weight:600}
.row-link .row-meta{
  font-family:"IBM Plex Mono",monospace;
  font-size:11px;color:var(--muted);margin-top:3px;
  line-height:1.35;
}
.row-link .row-meta .bbb-mark{
  color:var(--accent);font-weight:600;
}

/* ----------------------------------- rollup chips (clubs index) -------- */
.rollup{display:flex;gap:5px;flex-wrap:wrap;font-size:10px;margin-top:5px}
.rollup .chip{
  display:inline-flex;align-items:center;gap:3px;padding:1px 6px;
  background:var(--surface-2);
  border:1px solid var(--rule-2);
  border-radius:3px;
  font-family:"IBM Plex Mono",monospace;
  font-weight:500;color:var(--muted);
  font-variant-numeric:tabular-nums;font-size:10.5px;
  line-height:15px;
}
.rollup .chip.c{background:var(--accent-soft);border-color:var(--accent);color:var(--accent-2)}
.rollup .chip.p{background:var(--info-soft);border-color:var(--info);color:var(--info)}
.rollup .chip.r{background:var(--warn-soft);border-color:var(--warn);color:var(--warn)}

/* ----------------------------------- forms (player metadata submit) ---- */
form{
  background:var(--surface);border:1px solid var(--rule);border-radius:6px;
  padding:14px;box-shadow:none;margin-top:10px;
}
label{
  display:block;margin:10px 0 4px;
  font-family:"IBM Plex Mono",monospace;
  font-size:10px;color:var(--muted);
  font-weight:600;text-transform:uppercase;letter-spacing:.14em;
}
input,select,textarea{
  width:100%;padding:8px 10px;background:var(--surface);
  color:var(--ink);border:1px solid var(--rule-2);border-radius:4px;
  font-size:14.5px;font-family:"IBM Plex Sans",sans-serif;
}
input:focus,select:focus,textarea:focus{
  outline:none;border-color:var(--accent);
  box-shadow:0 0 0 1px var(--accent);
}
select[multiple]{padding:5px}
textarea{min-height:72px;resize:vertical}
.field-row{display:flex;gap:12px}
.field-row > div{flex:1}

.btn{
  display:inline-block;padding:9px 16px;border-radius:4px;
  background:var(--accent);color:#ffffff !important;text-decoration:none;
  font-family:"IBM Plex Sans",sans-serif;
  font-size:13px;font-weight:600;letter-spacing:.02em;
  border:1px solid var(--accent-2);margin-right:8px;text-align:center;
  transition:background .15s;
}
.btn:hover{background:var(--accent-2);text-decoration:none;color:#ffffff;
  border-bottom:1px solid var(--accent-2)}
.btn.secondary{background:var(--info);border-color:#1741b8}
.btn.secondary:hover{background:#1741b8}
.btn-row{display:flex;gap:10px;margin-top:14px;flex-wrap:wrap}
.btn-row .btn{flex:1;min-width:140px;margin-right:0}

/* ----------------------------------- meta-grid (player) ---------------- */
.meta-grid{
  display:grid;grid-template-columns:repeat(2,minmax(0,1fr));
  gap:1px;margin:8px 0 0;background:var(--rule);
  border:1px solid var(--rule);
}
.meta-grid > div{background:var(--surface);padding:10px 12px}
.meta-grid .k{
  font-family:"IBM Plex Mono",monospace;
  font-size:9.5px;text-transform:uppercase;letter-spacing:.14em;
  color:var(--muted);font-weight:600;
}
.meta-grid .v{
  font-family:"IBM Plex Sans",sans-serif;
  font-size:14.5px;font-weight:600;color:var(--ink);margin-top:3px;
}

/* ----------------------------------- generic tables ------------------- */
.table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch;margin:6px -4px}
table{
  width:100%;border-collapse:collapse;
  font-family:"IBM Plex Mono",monospace;
  font-size:11.5px;font-variant-numeric:tabular-nums;
}
th,td{
  padding:7px 7px;border-bottom:1px solid var(--rule);text-align:left;
  white-space:nowrap;
}
th{
  background:var(--surface-2);font-weight:600;color:var(--muted);
  border-bottom:1px solid var(--rule-2);font-size:9.5px;
  text-transform:uppercase;letter-spacing:.12em;
  font-family:"IBM Plex Mono",monospace;
}
td.num,th.num{text-align:right}
tr:last-child td{border-bottom:none}

/* ----------------------------------- season chips (data repo) --------- */
.season-chips{display:flex;gap:4px;flex-wrap:wrap;margin-top:14px}
.season-chip{
  background:var(--surface-2);border:1px solid var(--rule);
  border-radius:3px;padding:4px 8px;
  font-family:"IBM Plex Mono",monospace;
  font-size:10.5px;font-weight:500;
  color:var(--ink-2);font-variant-numeric:tabular-nums;
  display:inline-flex;align-items:baseline;gap:4px;
}
.season-chip .y{
  color:var(--accent);font-size:9.5px;
  letter-spacing:.04em;font-weight:600;
}

/* ----------------------------------- search input override ----------- */
input[type="search"]{
  background:var(--surface);
}

/* ----------------------------------- staggered reveal ---------------- */
@keyframes pageFadeUp{
  from{opacity:0;transform:translateY(4px)}
  to  {opacity:1;transform:none}
}
.hero,.card,.row-list{
  animation:pageFadeUp .32s cubic-bezier(.2,.8,.2,1) both;
}
.hero{animation-delay:0s}
.card{animation-delay:.06s}
.row-list{animation-delay:.12s}

@media (prefers-reduced-motion: reduce){
  .hero,.card,.row-list{animation:none}
  .btn,a,.row-link{transition:none}
}

/* ============================================================ DASHBOARD ==

   Two-tab dashboard (BATTING / BOWLING) styles. Lives here so build
   order doesn't matter — every builder calls write_static_assets().

*/

/* tab toggle ----------------------------------------------------------- */
.tabs{
  display:flex;gap:0;margin:8px 0 14px;
  border:1px solid var(--rule);border-radius:6px;overflow:hidden;
  background:var(--surface);
}
.tabs > button{
  flex:1;padding:11px 14px;background:var(--surface);color:var(--muted);
  border:none;border-right:1px solid var(--rule);cursor:pointer;
  font-family:"IBM Plex Mono",monospace;font-size:11px;font-weight:600;
  text-transform:uppercase;letter-spacing:.16em;
  transition:background .12s,color .12s;
}
.tabs > button:last-child{border-right:none}
.tabs > button:hover{background:var(--surface-2);color:var(--ink)}
.tabs > button.on{background:var(--ink);color:var(--surface)}

/* snapshot tile grid (in hero) ---------------------------------------- */
.snapshot{
  display:grid;grid-template-columns:repeat(3,minmax(0,1fr));
  gap:1px;margin-top:14px;background:var(--rule);
  border:1px solid var(--rule);
}
.snap{
  background:var(--surface);padding:10px 11px;
}
.snap[title]{cursor:help}
.snap .lbl{
  font-family:"IBM Plex Mono",monospace;
  font-size:9px;text-transform:uppercase;letter-spacing:.16em;
  color:var(--muted);font-weight:600;
}
.snap .v{
  font-family:"IBM Plex Mono",monospace;
  font-size:20px;font-weight:600;color:var(--ink);
  font-variant-numeric:tabular-nums lining-nums;letter-spacing:-.02em;
  margin-top:3px;
}
.snap .sub{
  font-family:"IBM Plex Mono",monospace;
  font-size:9.5px;color:var(--muted);margin-top:2px;
  font-variant-numeric:tabular-nums;
}

/* BBB coverage bar in hero -------------------------------------------- */
.bbb-bar-wrap{
  margin-top:12px;border:1px solid var(--rule);
  background:var(--surface-2);height:18px;
  position:relative;overflow:hidden;border-radius:3px;
}
.bbb-bar-wrap > .fill{
  display:block;height:100%;background:var(--accent);
  width:0;animation:bbbWipe .7s cubic-bezier(.2,.8,.2,1) forwards;
}
.bbb-bar-wrap > .lbl{
  position:absolute;inset:0;display:flex;
  align-items:center;justify-content:center;
  font-family:"IBM Plex Mono",monospace;font-size:10px;font-weight:600;
  color:var(--ink);letter-spacing:.06em;
}
@keyframes bbbWipe{from{width:0}to{width:var(--w,0%)}}

/* slicer panel -------------------------------------------------------- */
.filter-panel{
  background:var(--surface);border:1px solid var(--rule);border-radius:6px;
  margin-bottom:12px;overflow:hidden;
}
.filter-head{
  display:flex;align-items:center;justify-content:space-between;
  padding:10px 14px;cursor:pointer;user-select:none;
  background:var(--surface);border-bottom:1px solid transparent;
  transition:background .12s,border-color .12s;
}
.filter-head:hover{background:var(--surface-2)}
.filter-panel.open .filter-head{border-bottom-color:var(--rule);background:var(--surface-2)}
.filter-head .ttl{
  font-family:"IBM Plex Mono",monospace;font-size:11px;font-weight:600;
  letter-spacing:.16em;text-transform:uppercase;color:var(--ink);
  display:flex;align-items:center;gap:8px;
}
.filter-head .summary{
  font-family:"IBM Plex Mono",monospace;font-size:10.5px;color:var(--muted);
  font-weight:500;
}
.filter-head .chev{
  font-family:"IBM Plex Mono",monospace;font-size:13px;color:var(--muted);
  transition:transform .2s;
}
.filter-panel.open .filter-head .chev{transform:rotate(90deg)}
.filter-body{display:none;padding:10px 14px 14px}
.filter-panel.open .filter-body{display:block}

#slicer-rail{display:flex;flex-direction:column;gap:10px;margin-top:0}
.slicer-group{border-top:1px solid var(--rule);padding-top:10px}
.slicer-group:first-child{border-top:none;padding-top:0}
.slicer-group .lbl{
  font-family:"IBM Plex Mono",monospace;
  font-size:9.5px;text-transform:uppercase;letter-spacing:.14em;
  color:var(--muted);font-weight:600;margin-bottom:6px;display:block;
}
.slicer-chips{display:flex;flex-wrap:wrap;gap:5px}
.slicer-chip{
  display:inline-flex;align-items:center;gap:4px;
  padding:4px 10px;background:var(--surface);
  border:1px solid var(--rule-2);border-radius:3px;
  font-family:"IBM Plex Mono",monospace;font-size:11px;
  font-weight:500;color:var(--ink-2);cursor:pointer;user-select:none;
  transition:background .12s,border-color .12s,color .12s;
  white-space:nowrap;
}
.slicer-chip:hover{border-color:var(--accent);color:var(--accent)}
.slicer-chip.on{background:var(--accent);color:#ffffff;
  border-color:var(--accent-2)}
.slicer-chip.dim{opacity:.45;cursor:not-allowed}

.reset-btn{
  background:none;border:none;color:var(--warn);
  font-family:"IBM Plex Mono",monospace;font-size:10.5px;
  font-weight:600;letter-spacing:.1em;cursor:pointer;
  text-transform:uppercase;padding:0;
}
.reset-btn:hover{text-decoration:underline}

/* dashboard breakdown tables ------------------------------------------ */
.bar-cell{
  position:relative;text-align:left;
  padding:7px 8px;white-space:nowrap;
}
.bar-cell[title]{cursor:help}
.bar-cell .fill{
  position:absolute;left:0;top:4px;bottom:4px;
  background:rgba(13,122,82,.20);
  z-index:0;border-radius:0;
  transition:width .35s cubic-bezier(.2,.8,.2,1);
}
.bar-cell .fill.hot{background:rgba(236,72,153,.22)}
.bar-cell .val{position:relative;z-index:1;font-weight:600}

.bucket-table{
  width:100%;font-family:"IBM Plex Mono",monospace;
  font-size:11.5px;font-variant-numeric:tabular-nums;border-collapse:collapse;
}
.bucket-table th{
  font-family:"IBM Plex Mono",monospace;font-size:9.5px;
  text-transform:uppercase;letter-spacing:.12em;color:var(--muted);
  font-weight:600;padding:6px 6px;border-bottom:1px solid var(--rule-2);
  background:var(--surface-2);text-align:right;
}
.bucket-table th:first-child{text-align:left;padding-left:8px}
.bucket-table th.bar-th{text-align:left;padding-left:8px}
.bucket-table td{padding:7px 6px;border-bottom:1px solid var(--rule);
  text-align:right;color:var(--ink);}
.bucket-table td:first-child{text-align:left;padding-left:8px;
  color:var(--ink);font-weight:600}
.bucket-table tr:last-child td{border-bottom:none}
.bucket-table tr:hover td{background:var(--surface-2)}
.bucket-table tr:hover td.bar-cell{background:transparent}
.bucket-table .empty td{color:var(--muted);opacity:.55}
.bucket-table td.pos{color:var(--accent)}
.bucket-table td.neg{color:var(--warn)}

/* sparkline-style horizontal bars ------------------------------------- */
.bucket-bars{display:flex;flex-direction:column;gap:5px;margin-top:8px}
.bucket-bar{display:flex;align-items:center;gap:8px;font-size:11px;
  font-family:"IBM Plex Mono",monospace}
.bucket-bar .lbl{
  flex:0 0 56px;color:var(--muted);font-size:9.5px;
  letter-spacing:.04em;text-transform:uppercase;font-weight:600;
}
.bucket-bar .bar{
  flex:1;height:10px;background:var(--surface-2);
  border:1px solid var(--rule);border-radius:2px;overflow:hidden;
}
.bucket-bar .bar > span{
  display:block;height:100%;background:var(--accent);
  transition:width .35s cubic-bezier(.2,.8,.2,1);
}
.bucket-bar .bar.warn > span{background:var(--warn)}
.bucket-bar .bar.info > span{background:var(--info)}
.bucket-bar .num{
  flex:0 0 auto;text-align:right;
  font-variant-numeric:tabular-nums;color:var(--ink);font-weight:600;
  font-size:11px;
}

/* volume chip --------------------------------------------------------- */
.vol-chip{
  display:inline-block;padding:1px 7px;border:1px solid var(--accent);
  background:var(--accent-soft);color:var(--accent-2);
  border-radius:3px;
  font-family:"IBM Plex Mono",monospace;font-size:10px;font-weight:600;
  letter-spacing:.04em;margin-left:auto;
  font-variant-numeric:tabular-nums;vertical-align:middle;
}
.vol-chip.empty{background:var(--surface-2);border-color:var(--rule-2);color:var(--muted)}

/* empty state --------------------------------------------------------- */
.empty-note{
  color:var(--muted);font-size:13px;
  padding:10px 0;font-family:"IBM Plex Sans",sans-serif;
}
"""


def write_static_assets() -> None:
    """Write app/static/app.css once; pages link to it via <link>."""
    static_dir = APP_DIR / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    (static_dir / "app.css").write_text(CSS)


def hero(title: str, *, crumbs: list[tuple[str, str]] | None = None,
         lead: str = "", stats: list[tuple[str, str]] | None = None,
         extra_html: str = "") -> str:
    """
    The blue gradient hero block at the top of every page. `stats` is a
    list of (number, label) chips; `extra_html` is appended inside the
    hero (used for the per-season chips on the data repo).
    """
    crumbs = crumbs or []
    if crumbs:
        crumb_html = (
            '<div class="crumbs">'
            + '<span class="sep">&rsaquo;</span>'.join(
                f'<a href="{escape(href)}">{escape(label)}</a>' if href
                else f"<span>{escape(label)}</span>"
                for label, href in crumbs
            )
            + "</div>"
        )
    else:
        crumb_html = ""
    stats_html = ""
    if stats:
        stats_html = '<div class="stats">' + "".join(
            f'<div class="stat"><div class="n">{escape(n)}</div>'
            f'<div class="lbl">{escape(lbl)}</div></div>'
            for n, lbl in stats
        ) + "</div>"
    lead_html = f'<p class="lead">{lead}</p>' if lead else ""
    return (
        f'<header class="hero">'
        f"{crumb_html}"
        f"<h1>{escape(title)}</h1>"
        f"{lead_html}"
        f"{stats_html}"
        f"{extra_html}"
        f"</header>"
    )


FONT_CSS_HREF = (
    "https://fonts.bunny.net/css?family="
    "ibm-plex-sans:400,500,600,700"
    "|ibm-plex-mono:400,500,600,700"
    "&display=swap"
)


def page(body_html: str, *, title: str = "Rainham CC",
         css_href: str = "static/app.css",
         extra_head: str = "", extra_body: str = "") -> str:
    """
    Outer <html> shell. Body content (including any hero header) is
    rendered by the caller — this keeps mobile layout one column, no
    sticky header. Web fonts are pulled from Bunny Fonts (privacy-
    respecting Google Fonts mirror); CSS lives in `static/app.css`.
    """
    return (
        "<!doctype html>\n"
        f'<html lang="en"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<meta name="theme-color" content="#0d7a52">'
        f"<title>{escape(title)}</title>"
        f'<link rel="preconnect" href="https://fonts.bunny.net">'
        f'<link rel="stylesheet" href="{escape(FONT_CSS_HREF)}">'
        f'<link rel="stylesheet" href="{escape(css_href)}">'
        f"{extra_head}</head><body>"
        f"{body_html}"
        f"{extra_body}</body></html>"
    )


def coverage_bar(pct: float | None) -> str:
    if pct is None:
        return '<span class="muted">—</span>'
    cls = "coverage-bar"
    if pct == 0:
        cls += " zero"
    elif pct < 0.5:
        cls += " low"
    return (
        f'<span class="{cls}"><span style="width:{pct*100:.0f}%"></span></span>'
        f"{pct*100:.0f}%"
    )


def status_pill(status: str) -> str:
    cls_map = {
        "complete": "complete",
        "partial": "partial",
        "not captured": "notcap",
        "needs review": "review",
        "conflicting": "conflict",
    }
    return f'<span class="tag {cls_map.get(status, "notcap")}">{escape(status)}</span>'
