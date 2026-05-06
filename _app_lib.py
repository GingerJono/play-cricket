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
  Wisden-inspired editorial cricket aesthetic.
  Cream parchment, cricket-ball red, forest pitch green.
  Serif display + JetBrains Mono for tabular numerics.
  Hairline rules, no drop shadows. Mobile-first, max-width: 540px.
*/

:root{
  --paper:#f4ead0;
  --paper-2:#ede0bd;
  --card:#fbf3da;
  --ink:#241612;
  --ink-2:#3a2922;
  --muted:#7a5e4a;
  --rule:#c4a886;
  --rule-2:#a78759;
  --ball:#7a1c1c;
  --ball-2:#5a1414;
  --pitch:#1f4e2c;
  --mustard:#a47218;
  --ink-blue:#2b3a6a;
  --hi:#f0d893;
}

@font-face{font-display:swap}

*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{
  padding:14px 14px 32px;
  font-family:'Source Serif Pro','Charter','Iowan Old Style','Georgia',serif;
  font-size:15px;line-height:1.5;color:var(--ink);
  background:
    radial-gradient(circle at 12% -8%,rgba(122,28,28,.06),transparent 38%),
    radial-gradient(circle at 92% 110%,rgba(31,78,44,.05),transparent 42%),
    var(--paper);
  background-attachment:fixed;
  max-width:540px;margin:0 auto;
  -webkit-font-smoothing:antialiased;
  text-rendering:optimizeLegibility;
  font-feature-settings:"kern","liga","onum";
  position:relative;
}
/* Inline SVG paper grain overlay — fixed, ~3% opacity. */
body::before{
  content:"";position:fixed;inset:0;z-index:0;pointer-events:none;
  background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='220' height='220' viewBox='0 0 220 220'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='1.4' numOctaves='2' seed='5' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 0.14 0 0 0 0 0.09 0 0 0 0 0.07 0 0 0 0.32 0'/></filter><rect width='220' height='220' filter='url(%23n)' opacity='0.45'/></svg>");
  mix-blend-mode:multiply;opacity:.18;
}
body > *{position:relative;z-index:1}

a{color:var(--ball);text-decoration:none;
  border-bottom:1px solid rgba(122,28,28,.25);
  transition:color .15s,border-color .15s}
a:hover{color:var(--ball-2);border-bottom-color:var(--ball-2)}

::selection{background:var(--hi);color:var(--ink)}

/* ----------------------------------- masthead-style hero ---------------- */
.hero{
  background:transparent;color:var(--ink);
  padding:18px 4px 22px;margin:0 0 18px;position:relative;
  border-top:3px double var(--ball);
  border-bottom:3px double var(--ball);
}
.hero::before,.hero::after{
  content:"";position:absolute;left:0;right:0;height:1px;
  background:var(--ball);opacity:.4;
}
.hero::before{top:6px}
.hero::after{bottom:6px}
.hero .crumbs{
  font-family:'Source Serif Pro',Georgia,serif;
  font-size:10px;text-transform:uppercase;letter-spacing:.22em;
  font-weight:600;color:var(--ball);margin-bottom:8px;
  font-feature-settings:"smcp";
}
.hero .crumbs a{color:var(--ball);border-bottom:none;opacity:.85}
.hero .crumbs a:hover{opacity:1;text-decoration:underline}
.hero .crumbs .sep{margin:0 6px;opacity:.45}
.hero h1{
  font-family:'Frank Ruhl Libre','Crimson Pro',Georgia,serif;
  font-size:30px;line-height:1.05;margin:0 0 8px;
  font-weight:900;letter-spacing:-.015em;color:var(--ink);
}
.hero p.lead{
  font-size:14px;margin:0;color:var(--ink-2);
  line-height:1.5;font-style:italic;max-width:46ch;
}
.hero .stats{
  display:flex;gap:0;margin-top:14px;flex-wrap:wrap;
  border-top:1px solid var(--rule);
}
.hero .stat{
  flex:1 1 0;min-width:0;padding:8px 10px 4px;
  border-right:1px solid var(--rule);
  border-bottom:1px solid var(--rule);
  text-align:left;
}
.hero .stat:nth-child(2n){border-right:none}
.hero .stat:nth-last-child(-n+2){border-bottom:none}
.hero .stat .n{
  font-family:'JetBrains Mono','IBM Plex Mono',monospace;
  font-size:22px;font-weight:700;line-height:1;color:var(--ball);
  font-variant-numeric:tabular-nums lining-nums;letter-spacing:-.02em;
}
.hero .stat .lbl{
  font-size:9.5px;text-transform:uppercase;letter-spacing:.16em;
  color:var(--muted);margin-top:5px;font-weight:700;
}

/* ----------------------------------- cards ------------------------------ */
.card{
  background:var(--card);
  border:1px solid var(--rule);
  border-radius:0;
  padding:16px 18px;margin:0 0 14px;
  box-shadow:none;
  position:relative;
}
.card::before{
  content:"";position:absolute;left:-1px;right:-1px;top:-1px;height:3px;
  background:linear-gradient(90deg,var(--ball) 0,var(--ball) 22%,
    transparent 22%,transparent 78%,var(--ball) 78%,var(--ball) 100%);
}
.card > h2:first-child{margin-top:0}

h2{
  font-family:'Frank Ruhl Libre',Georgia,serif;
  font-size:18px;margin:18px 0 10px;padding:0 0 6px;
  border-bottom:1px solid var(--rule);
  font-weight:800;letter-spacing:-.01em;color:var(--ink);
}
h2::after{
  content:"";display:block;width:38px;height:2px;background:var(--ball);
  margin:6px 0 -8px;
}
h3{
  font-family:'Source Serif Pro',Georgia,serif;
  font-size:11px;margin:14px 0 6px;color:var(--ball);
  text-transform:uppercase;letter-spacing:.18em;font-weight:700;
}
p{font-size:14px;margin:6px 0 10px;color:var(--ink-2)}
p.meta,p.note{
  color:var(--muted);font-size:12.5px;margin:0 0 12px;
  font-style:italic;line-height:1.45;
}

/* ----------------------------------- status tags ------------------------ */
.tag{
  display:inline-block;padding:3px 10px;border-radius:0;
  font-family:'Source Serif Pro',Georgia,serif;
  font-size:10px;font-weight:700;line-height:14px;
  letter-spacing:.14em;text-transform:uppercase;
  border:1px solid transparent;
  font-feature-settings:"smcp";
}
.tag.complete{background:#e1ead5;border-color:var(--pitch);color:var(--pitch)}
.tag.partial {background:#f5e3bd;border-color:var(--mustard);color:var(--mustard)}
.tag.notcap  {background:transparent;border-color:var(--rule-2);color:var(--muted)}
.tag.review  {background:#dde2ef;border-color:var(--ink-blue);color:var(--ink-blue)}
.tag.conflict{background:#f1d6cf;border-color:var(--ball);color:var(--ball)}
.tag.yes     {background:#e1ead5;border-color:var(--pitch);color:var(--pitch);padding:3px 8px}
.tag.no      {background:transparent;border-color:var(--rule-2);color:var(--muted);padding:3px 8px}

/* ----------------------------------- result pill ------------------------ */
.pill{
  display:inline-block;padding:2px 8px;border-radius:0;
  font-family:'JetBrains Mono',monospace;
  font-size:10.5px;font-weight:700;color:var(--paper);min-width:20px;
  text-align:center;line-height:15px;letter-spacing:.06em;
}
.pill.W{background:var(--pitch)}
.pill.L{background:var(--ball)}
.pill.D,.pill.T{background:var(--ink-2)}
.pill.NR,.pill.A{background:var(--mustard)}

/* ----------------------------------- coverage bar ----------------------- */
.cov-row{display:flex;align-items:center;gap:10px;margin:5px 0;font-size:12px}
.cov-row .lbl{
  flex:0 0 64px;font-size:9.5px;color:var(--muted);font-weight:700;
  text-transform:uppercase;letter-spacing:.14em;
  font-family:'Source Serif Pro',Georgia,serif;
}
.cov-row .bar{
  flex:1;height:8px;background:repeating-linear-gradient(
    90deg,var(--paper-2) 0 4px,transparent 4px 8px);
  border:1px solid var(--rule);overflow:hidden;
}
.cov-row .bar > span{display:block;height:100%;background:var(--ball)}
.cov-row .bar.zero > span{background:var(--rule-2)}
.cov-row .bar.low  > span{background:var(--mustard)}
.cov-row .num{
  flex:0 0 42px;text-align:right;
  font-family:'JetBrains Mono',monospace;
  font-variant-numeric:tabular-nums;
  color:var(--ink);font-weight:600;font-size:12px;
}

/* ----------------------------------- fixture cards ---------------------- */
.fix-list{display:flex;flex-direction:column;gap:0;margin:8px 0 0}
.fix{
  background:transparent;border:none;
  border-bottom:1px solid var(--rule);
  border-radius:0;padding:12px 0;
  box-shadow:none;
}
.fix:last-child{border-bottom:none}
.fix .row1{
  display:flex;justify-content:space-between;align-items:baseline;
  gap:10px;margin-bottom:4px;
}
.fix .row1 .left{display:flex;flex-direction:column;gap:2px;min-width:0}
.fix .date{
  font-family:'JetBrains Mono',monospace;
  font-size:10.5px;color:var(--muted);font-weight:600;
  letter-spacing:.05em;
}
.fix .opp{
  font-family:'Frank Ruhl Libre',Georgia,serif;
  font-size:16px;font-weight:700;color:var(--ink);
  white-space:normal;word-break:break-word;line-height:1.2;
  letter-spacing:-.005em;
}
.fix .opp a{color:var(--ink);border-bottom:1px dotted var(--rule-2)}
.fix .opp a:hover{color:var(--ball);border-bottom-color:var(--ball)}
.fix .right{display:flex;align-items:center;gap:8px;flex-shrink:0}
.fix .meta{
  font-size:11.5px;color:var(--muted);margin:2px 0 6px;
  font-style:italic;
}

/* ----------------------------------- row-list (clubs, players) ---------- */
.row-list{display:flex;flex-direction:column;gap:0;margin:6px 0 0;
  border-top:1px solid var(--rule)}
.row-link{
  display:flex;align-items:center;justify-content:space-between;
  gap:12px;background:transparent;
  border:none;border-bottom:1px solid var(--rule);
  border-radius:0;padding:13px 4px;
  box-shadow:none;color:var(--ink);text-decoration:none;
  transition:background .15s;
}
.row-link:last-child{border-bottom:none}
.row-link:hover{background:var(--paper-2);text-decoration:none;border-color:var(--rule-2)}
.row-link .name{
  font-family:'Frank Ruhl Libre',Georgia,serif;
  font-weight:700;font-size:16px;min-width:0;
  white-space:normal;word-break:break-word;line-height:1.2;flex:1;
  letter-spacing:-.005em;color:var(--ink);
}
.row-link .right{display:flex;align-items:center;gap:8px;flex-shrink:0}
.row-link .count{
  font-family:'JetBrains Mono',monospace;
  font-size:11px;color:var(--muted);font-weight:600;
  font-variant-numeric:tabular-nums;letter-spacing:.02em;
}
.row-link .count.bbb{color:var(--ball);font-weight:700}
.row-link .row-meta{
  font-size:11.5px;color:var(--muted);margin-top:3px;
  line-height:1.35;font-family:'Source Serif Pro',Georgia,serif;
  font-style:italic;
}
.row-link .row-meta .bbb-mark{
  font-style:normal;color:var(--ball);font-weight:700;
  font-family:'JetBrains Mono',monospace;
}

/* ----------------------------------- rollup chips ----------------------- */
.rollup{display:flex;gap:4px;flex-wrap:wrap;font-size:10px;margin-top:5px}
.rollup .chip{
  display:inline-flex;align-items:center;gap:3px;padding:1px 7px;
  background:transparent;
  border:1px solid var(--rule-2);
  border-radius:0;
  font-family:'JetBrains Mono',monospace;
  font-weight:600;color:var(--muted);
  font-variant-numeric:tabular-nums;font-size:10.5px;
  line-height:15px;
}
.rollup .chip.c{background:#e1ead5;border-color:var(--pitch);color:var(--pitch)}
.rollup .chip.p{background:#f5e3bd;border-color:var(--mustard);color:var(--mustard)}
.rollup .chip.r{background:#dde2ef;border-color:var(--ink-blue);color:var(--ink-blue)}

/* ----------------------------------- form (player submit) --------------- */
form{
  background:var(--paper);border:1px solid var(--rule);border-radius:0;
  padding:16px 18px;box-shadow:none;margin-top:10px;
}
label{
  display:block;margin:10px 0 4px;font-size:10px;color:var(--muted);
  font-family:'Source Serif Pro',Georgia,serif;
  font-weight:700;text-transform:uppercase;letter-spacing:.18em;
}
input,select,textarea{
  width:100%;padding:9px 11px;background:var(--card);
  color:var(--ink);border:1px solid var(--rule);border-radius:0;
  font-size:14.5px;font-family:'Source Serif Pro',Georgia,serif;
}
input:focus,select:focus,textarea:focus{
  outline:none;border-color:var(--ball);
  box-shadow:0 0 0 1px var(--ball);
}
select[multiple]{padding:5px}
textarea{min-height:72px;resize:vertical}
.field-row{display:flex;gap:12px}
.field-row > div{flex:1}

.btn{
  display:inline-block;padding:11px 20px;border-radius:0;
  background:var(--ball);color:var(--paper) !important;text-decoration:none;
  font-family:'Source Serif Pro',Georgia,serif;
  font-size:13px;font-weight:700;letter-spacing:.16em;text-transform:uppercase;
  border:1px solid var(--ball-2);margin-right:8px;text-align:center;
  transition:background .15s,transform .05s;
}
.btn:hover{background:var(--ball-2);text-decoration:none;color:var(--paper);
  border-bottom:1px solid var(--ball-2)}
.btn:active{transform:translateY(1px)}
.btn.secondary{background:var(--pitch);border-color:#143820}
.btn.secondary:hover{background:#143820}
.btn-row{display:flex;gap:10px;margin-top:16px;flex-wrap:wrap}
.btn-row .btn{flex:1;min-width:140px;margin-right:0}

/* ----------------------------------- meta-grid (player) ----------------- */
.meta-grid{
  display:grid;grid-template-columns:repeat(2,minmax(0,1fr));
  gap:12px 16px;margin:6px 0 0;
}
.meta-grid > div{
  border-left:2px solid var(--ball);padding-left:10px;
}
.meta-grid .k{
  font-size:9.5px;text-transform:uppercase;letter-spacing:.18em;
  color:var(--muted);font-weight:700;
  font-family:'Source Serif Pro',Georgia,serif;
}
.meta-grid .v{
  font-family:'Frank Ruhl Libre',Georgia,serif;
  font-size:15px;font-weight:700;color:var(--ink);margin-top:3px;
}

/* ----------------------------------- tables ---------------------------- */
.table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch;margin:8px -4px}
table{
  width:100%;border-collapse:collapse;
  font-family:'JetBrains Mono','IBM Plex Mono',monospace;
  font-size:12px;font-variant-numeric:tabular-nums;
}
th,td{
  padding:7px 8px;border-bottom:1px solid var(--rule);text-align:left;
  white-space:nowrap;
}
th{
  background:transparent;font-weight:700;color:var(--muted);
  border-bottom:2px solid var(--ball);font-size:9.5px;
  text-transform:uppercase;letter-spacing:.14em;
  font-family:'Source Serif Pro',Georgia,serif;
}
td.num,th.num{text-align:right}
tr:nth-child(even) td{background:rgba(196,168,134,.10)}
tr:last-child td{border-bottom:none}

/* ----------------------------------- season chips (data repo) ---------- */
.season-chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:14px}
.season-chip{
  background:transparent;border:1px solid var(--rule-2);
  border-radius:0;padding:4px 10px;
  font-family:'JetBrains Mono',monospace;
  font-size:11px;font-weight:600;
  color:var(--ink);font-variant-numeric:tabular-nums;
  display:inline-flex;align-items:baseline;gap:5px;
}
.season-chip .y{
  color:var(--ball);font-size:10px;
  letter-spacing:.04em;font-weight:700;
}

/* ----------------------------------- search input override ------------- */
input[type="search"]{
  background:var(--paper);
  font-style:italic;
}

/* ----------------------------------- staggered reveal ------------------ */
@keyframes pageFadeUp{
  from{opacity:0;transform:translateY(6px)}
  to  {opacity:1;transform:none}
}
.hero,.card,.row-list{
  animation:pageFadeUp .42s cubic-bezier(.2,.8,.2,1) both;
}
.hero{animation-delay:0s}
.card{animation-delay:.08s}
.row-list{animation-delay:.16s}

@media (prefers-reduced-motion: reduce){
  .hero,.card,.row-list{animation:none}
  .btn,a,.row-link{transition:none}
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
    "frank-ruhl-libre:400,700,900"
    "|source-serif-pro:400,400i,600,700"
    "|jetbrains-mono:400,500,700"
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
        f'<meta name="theme-color" content="#7a1c1c">'
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
