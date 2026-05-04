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
:root {
  --bg: #0f1218;
  --surface: #161b22;
  --surface-2: #1f2630;
  --border: #2a313c;
  --text: #e6edf3;
  --muted: #8b97a8;
  --accent: #2da44e;
  --warn: #d29922;
  --bad: #f85149;
  --link: #58a6ff;
  --pill-bg: #21262d;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
               Helvetica, Arial, sans-serif;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text); }
.container { max-width: 1100px; margin: 0 auto; padding: 16px; }
header { padding: 16px; border-bottom: 1px solid var(--border);
         background: var(--surface); }
header .title { font-size: 18px; font-weight: 600; }
header .crumbs { font-size: 13px; color: var(--muted); margin-top: 4px; }
header a { color: var(--link); text-decoration: none; }
header a:hover { text-decoration: underline; }
h1 { font-size: 22px; margin: 0 0 8px; }
h2 { font-size: 17px; margin: 24px 0 8px;
     border-bottom: 1px solid var(--border); padding-bottom: 4px; }
p.lead { color: var(--muted); margin: 0 0 16px; font-size: 14px; }
.tag { display: inline-block; padding: 1px 8px; border-radius: 99px;
       font-size: 11px; background: var(--pill-bg); color: var(--text);
       border: 1px solid var(--border); }
.tag.complete { background: #1a3d23; border-color: #2da44e; color: #7ee787; }
.tag.partial { background: #3d2c00; border-color: #d29922; color: #f0c674; }
.tag.notcap  { background: #2a2a2a; border-color: var(--border); color: var(--muted); }
.tag.review  { background: #1f2a44; border-color: #58a6ff; color: #79c0ff; }
.tag.conflict{ background: #3a1a1a; border-color: #f85149; color: #ffa198; }
.tag.yes { color: #7ee787; }
.tag.no  { color: var(--muted); }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 6px 8px; text-align: left;
         border-bottom: 1px solid var(--border); white-space: nowrap; }
th { color: var(--muted); font-weight: 500; font-size: 11px;
     text-transform: uppercase; letter-spacing: 0.04em; }
tr:hover td { background: var(--surface-2); }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.coverage-bar { display: inline-block; width: 60px; height: 6px;
                background: var(--surface-2); border-radius: 3px;
                vertical-align: middle; margin-right: 6px; overflow: hidden; }
.coverage-bar > span { display: block; height: 100%; background: var(--accent); }
.coverage-bar.zero > span { background: var(--bad); }
.coverage-bar.low > span { background: var(--warn); }
a { color: var(--link); }
.muted { color: var(--muted); }
.btn { display: inline-block; padding: 6px 14px; border-radius: 6px;
       background: var(--accent); color: #fff !important; text-decoration: none;
       font-size: 13px; font-weight: 500; margin-right: 6px; }
.btn.secondary { background: var(--surface-2); color: var(--text) !important;
                 border: 1px solid var(--border); }
form { background: var(--surface); padding: 16px; border-radius: 8px;
       border: 1px solid var(--border); margin-top: 16px; }
label { display: block; margin: 8px 0 2px; font-size: 12px;
        color: var(--muted); }
input, select, textarea {
  width: 100%; padding: 6px 8px; background: var(--surface-2);
  color: var(--text); border: 1px solid var(--border); border-radius: 4px;
  font-size: 14px; font-family: inherit;
}
textarea { min-height: 60px; resize: vertical; }
.row { display: flex; gap: 12px; }
.row > div { flex: 1; }
.note { font-size: 12px; color: var(--muted); margin-top: 4px; }
"""


def write_static_assets() -> None:
    """Write app/static/app.css once; pages link to it via <link>."""
    static_dir = APP_DIR / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    (static_dir / "app.css").write_text(CSS)


def page(title: str, body_html: str, *,
         crumbs: list[tuple[str, str]] | None = None,
         css_href: str = "static/app.css",
         extra_head: str = "",
         extra_body: str = "") -> str:
    """
    Wrap `body_html` in the standard <html> shell.

    `css_href` is the relative path to the stylesheet from the page's
    own location — pass `"../static/app.css"` for a page two levels deep.
    """
    crumbs = crumbs or []
    crumb_html = " &rsaquo; ".join(
        f'<a href="{escape(href)}">{escape(label)}</a>' if href
        else f"<span>{escape(label)}</span>"
        for label, href in crumbs
    )
    return (
        "<!doctype html>\n"
        f'<html><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)}</title>"
        f'<link rel="stylesheet" href="{escape(css_href)}">'
        f"{extra_head}</head><body>"
        f'<header><div class="container">'
        f'<div class="title">Rainham CC — opposition data</div>'
        f'<div class="crumbs">{crumb_html}</div>'
        f"</div></header>"
        f'<main class="container">{body_html}</main>'
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
