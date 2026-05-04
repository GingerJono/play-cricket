#!/usr/bin/env python3
"""
Build a single `reports/index.html` listing every report under
`reports/<category>/...`.

Categories are the immediate subdirs of `reports/` (e.g. `scouting`,
`ad-hoc`, `league`, etc.). A category folder may contain anything; this
script discovers reports by walking each category root and listing the
deepest folder that holds a `.md` / `.html` / `.png` artefact.

Conventions per category:

  scouting/<YYYY-MM-DD>/<slug>/v<N>/scout.{md,html,png}
                              ^ versioned, has a `latest` symlink
  ad-hoc/<name>.md            ← one-off scripts each emit a single md
  ad-hoc/<slug>/<file>.md     ← also fine
  league/<YYYY-MM-DD>/<slug>/...
  ...                         (any new category gets the same treatment)

Run it any time: `python3 build_index.py`. The scout / ad-hoc generators
also call this automatically after writing, so you almost never need to
run it by hand — but it's idempotent if you do.
"""

from __future__ import annotations

import datetime as dt
import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPORTS_DIR = ROOT / "reports"
OUT_PATH = REPORTS_DIR / "index.html"

CATEGORY_LABELS = {
    "scouting": "Scouting reports",
    "ad-hoc":   "Ad-hoc reports",
    "league":   "League reports",
}
CATEGORY_BLURBS = {
    "scouting": "Opposition 1st-XI scouting reports — see `scout.py`.",
    "ad-hoc":   "One-off Rainham stat queries promoted into committed "
                "scripts (top scorers, streaks, head-to-heads).",
    "league":   "League-wide reports — division standings, season "
                "round-ups.",
}
DEFAULT_CATEGORIES = ["scouting", "ad-hoc", "league"]


def _esc(x) -> str:
    return html.escape("" if x is None else str(x), quote=True)


# ----------------------------------------------------------- discovery ---

def _versions_in(slug_dir: Path):
    """Return sorted list of (n, path) for v<N> children, newest first."""
    out = []
    for p in slug_dir.iterdir():
        if p.is_dir() and re.fullmatch(r"v\d+", p.name):
            try:
                out.append((int(p.name[1:]), p))
            except ValueError:
                pass
    out.sort(key=lambda x: -x[0])
    return out


def _artefacts(p: Path):
    """Return dict of present artefacts (md/html/png) inside `p`."""
    out = {}
    for ext in ("md", "html", "png"):
        # Prefer canonical filenames first, then fall back to any *.<ext>.
        for cand in (p / f"scout.{ext}", *sorted(p.glob(f"*.{ext}"))):
            if cand.exists() and cand.is_file():
                out[ext] = cand
                break
    return out


def discover_scouting():
    """Yield report dicts for `reports/scouting/<date>/<slug>/v<N>/...`."""
    base = REPORTS_DIR / "scouting"
    if not base.exists():
        return
    for date_dir in sorted(base.iterdir(), reverse=True):
        if not date_dir.is_dir():
            continue
        for slug_dir in sorted(date_dir.iterdir()):
            if not slug_dir.is_dir():
                continue
            versions = _versions_in(slug_dir)
            if not versions:
                # Legacy un-versioned directory (md/html sit directly in slug).
                arts = _artefacts(slug_dir)
                if not arts:
                    continue
                yield {
                    "category": "scouting",
                    "date": date_dir.name,
                    "title": _slug_to_title(slug_dir.name),
                    "subtitle": "legacy",
                    "dir": slug_dir,
                    "artefacts": arts,
                    "versions": [],
                }
                continue
            top_n, top_path = versions[0]
            arts = _artefacts(top_path)
            yield {
                "category": "scouting",
                "date": date_dir.name,
                "title": _slug_to_title(slug_dir.name),
                "subtitle": f"v{top_n}" + (
                    f" · also v1–v{top_n - 1}" if top_n > 1 else ""),
                "dir": top_path,
                "artefacts": arts,
                "versions": versions,
            }


def discover_flat(category: str):
    """Yield reports for categories that don't use the date/slug/vN tree.

    Handles two shapes inside `reports/<category>/`:
      - bare files: `<name>.md` / `<name>.html`
      - folders:    `<slug>/<file>.{md,html,png}`
    """
    base = REPORTS_DIR / category
    if not base.exists():
        return
    seen = set()
    # Bare files
    for f in sorted(base.iterdir()):
        if f.is_file() and f.suffix in (".md", ".html"):
            stem = f.stem
            if stem in seen:
                continue
            seen.add(stem)
            arts = {f.suffix.lstrip("."): f}
            # Pair up siblings with the same stem.
            for ext in ("md", "html", "png"):
                sib = f.with_suffix(f".{ext}")
                if sib.exists():
                    arts[ext] = sib
            mtime = max(arts[k].stat().st_mtime for k in arts)
            yield {
                "category": category,
                "date": dt.date.fromtimestamp(mtime).isoformat(),
                "title": _slug_to_title(stem),
                "subtitle": "",
                "dir": base,
                "artefacts": arts,
                "versions": [],
            }
    # Folders
    for d in sorted(base.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        # If the folder looks like an ISO date, recurse one level (date/slug)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d.name):
            for slug_dir in sorted(d.iterdir()):
                if not slug_dir.is_dir():
                    continue
                arts = _artefacts(slug_dir)
                if not arts:
                    continue
                yield {
                    "category": category,
                    "date": d.name,
                    "title": _slug_to_title(slug_dir.name),
                    "subtitle": "",
                    "dir": slug_dir,
                    "artefacts": arts,
                    "versions": [],
                }
        else:
            arts = _artefacts(d)
            if not arts:
                continue
            mtime = max(arts[k].stat().st_mtime for k in arts)
            yield {
                "category": category,
                "date": dt.date.fromtimestamp(mtime).isoformat(),
                "title": _slug_to_title(d.name),
                "subtitle": "",
                "dir": d,
                "artefacts": arts,
                "versions": [],
            }


def _slug_to_title(slug: str) -> str:
    return slug.replace("_", " ").replace("-", " ").strip().title()


def discover_all():
    """Run all discoverers and return a {category: [report, ...]} dict.

    Categories are auto-discovered from `reports/` so adding a new folder
    just works — but well-known ones get a friendlier label."""
    categories = []
    if REPORTS_DIR.exists():
        for p in sorted(REPORTS_DIR.iterdir()):
            if p.is_dir() and not p.name.startswith("."):
                categories.append(p.name)
    # Stable order: known first, then any extras
    ordered = [c for c in DEFAULT_CATEGORIES if c in categories]
    ordered += [c for c in categories if c not in DEFAULT_CATEGORIES]

    out = {}
    for cat in ordered:
        if cat == "scouting":
            items = list(discover_scouting())
        else:
            items = list(discover_flat(cat))
        out[cat] = items
    return out


# --------------------------------------------------------------- render ---

CSS = """
:root{
  --bg:#f3f4f8;--card:#fff;--ink:#10172a;--muted:#5a657a;
  --line:#e6e9f0;--accent:#1d4ed8;--shadow:0 1px 2px rgba(16,23,42,.04),
  0 4px 14px rgba(16,23,42,.06);
}
*{box-sizing:border-box}
body{margin:0;padding:18px 16px 60px;font-family:-apple-system,
  BlinkMacSystemFont,'Segoe UI',Inter,Roboto,'Helvetica Neue',Arial,sans-serif;
  font-size:14.5px;line-height:1.45;color:var(--ink);
  background:linear-gradient(180deg,#eef1f7 0,#f3f4f8 320px);
  max-width:980px;margin:0 auto}
.hero{background:linear-gradient(135deg,#0f1f4a 0%,#1d4ed8 100%);color:#fff;
  border-radius:16px;padding:18px 22px;box-shadow:var(--shadow);
  margin:0 0 22px;position:relative;overflow:hidden}
.hero::after{content:"";position:absolute;inset:0;background:
  radial-gradient(circle at 92% -20%,rgba(255,255,255,.18),transparent 50%);
  pointer-events:none}
.hero .eyebrow{font-size:11px;text-transform:uppercase;letter-spacing:.12em;
  opacity:.78;font-weight:700}
.hero h1{font-size:26px;margin:4px 0 6px;font-weight:800;letter-spacing:-.01em}
.hero p{margin:0;font-size:13.5px;opacity:.88;max-width:640px}
.cat{margin:0 0 26px}
.cat.empty-cat{opacity:.6}
.cat > h2{font-size:16px;margin:0 0 10px;padding:0 0 8px;
  border-bottom:1px solid var(--line);font-weight:700;
  display:flex;align-items:center;gap:10px}
.cat > h2 .count{background:#e6ecf9;color:var(--accent);font-size:11.5px;
  font-weight:700;padding:2px 9px;border-radius:9px}
.cat.empty-cat > h2 .count{background:#f0f1f5;color:var(--muted)}
.cat > p.blurb{margin:0 0 12px;color:var(--muted);font-size:12.5px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));
  gap:12px}
.r{background:var(--card);border:1px solid var(--line);border-radius:12px;
  padding:13px 15px;box-shadow:var(--shadow);display:flex;flex-direction:column;
  gap:8px;min-height:122px}
.r .meta{display:flex;align-items:center;gap:8px;font-size:11px;
  color:var(--muted);font-weight:700;letter-spacing:.04em;
  text-transform:uppercase}
.r .meta .date{color:var(--muted)}
.r .meta .ver{background:#e6ecf9;color:var(--accent);padding:1px 7px;
  border-radius:6px;letter-spacing:.04em;font-size:10.5px}
.r .meta .legacy{background:#f0eede;color:#a07300;padding:1px 7px;
  border-radius:6px;letter-spacing:.04em;font-size:10.5px;
  text-transform:none;font-weight:700}
.r h3{margin:0;font-size:15px;font-weight:700;letter-spacing:-.005em;
  word-break:break-word;line-height:1.25}
.r .links{display:flex;gap:6px;flex-wrap:wrap;margin-top:auto;padding-top:6px}
.r .links a{flex:0 0 auto;display:inline-flex;align-items:center;gap:5px;
  padding:4px 10px;border-radius:7px;font-size:11.5px;font-weight:700;
  text-decoration:none;border:1px solid var(--line);background:#fafbfd;
  color:var(--accent)}
.r .links a:hover{background:#eef2fb}
.r .links a.png{background:#eef1fb;color:var(--accent);border-color:#d4dcf3}
.r .links a.html{background:#eaf5ec;color:#15803d;border-color:#cde6d2}
.r .links a.md{background:#f1ecfb;color:#5b21b6;border-color:#dccdf3}
.empty{padding:14px;background:#fff;border:1px dashed var(--line);
  border-radius:10px;color:var(--muted);font-size:12.5px}
.footer{margin-top:32px;font-size:11.5px;color:var(--muted);text-align:center}
"""


def _link(rel_path, kind):
    label = {"md": "MD", "html": "HTML", "png": "PNG"}.get(kind, kind.upper())
    icon = {"md": "📝", "html": "🌐", "png": "🖼"}.get(kind, "")
    return (f"<a class='{kind}' href='{_esc(rel_path)}' target='_blank' "
            f"rel='noopener'>{icon} {label}</a>")


def _render_card(r):
    parts = ["<div class='r'>"]
    parts.append("<div class='meta'>")
    parts.append(f"<span class='date'>{_esc(r['date'])}</span>")
    sub = (r["subtitle"] or "").strip()
    if sub:
        # vN gets the accent pill; "(unversioned)"-style notes get a softer one.
        if re.match(r"^v\d", sub):
            head, _, tail = sub.partition(" · ")
            parts.append(f"<span class='ver'>{_esc(head)}</span>")
            if tail:
                parts.append(f"<span>{_esc(tail)}</span>")
        else:
            parts.append(f"<span class='legacy'>{_esc(sub)}</span>")
    parts.append("</div>")
    parts.append(f"<h3>{_esc(r['title'])}</h3>")
    arts = r["artefacts"]
    parts.append("<div class='links'>")
    for kind in ("png", "html", "md"):
        if kind in arts:
            rel = arts[kind].relative_to(REPORTS_DIR).as_posix()
            parts.append(_link(rel, kind))
    parts.append("</div>")
    parts.append("</div>")
    return "".join(parts)


def render(by_cat) -> str:
    today = dt.date.today().isoformat()
    total = sum(len(v) for v in by_cat.values())
    parts = ["<!doctype html>",
             '<html lang="en"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width,initial-scale=1">',
             "<title>Rainham CC — reports index</title>",
             f"<style>{CSS}</style></head><body>"]
    parts.append("<div class='hero'>")
    parts.append("<div class='eyebrow'>Rainham CC · stats</div>")
    parts.append("<h1>Reports index</h1>")
    parts.append(f"<p>Every committed report under <code>reports/</code>, "
                 f"grouped by category. {total} report"
                 f"{'s' if total != 1 else ''} indexed · "
                 f"refreshed {_esc(today)}.</p>")
    parts.append("</div>")

    for cat, items in by_cat.items():
        label = CATEGORY_LABELS.get(cat, cat.replace("-", " ").title())
        blurb = CATEGORY_BLURBS.get(cat, "")
        cls = " empty-cat" if not items else ""
        parts.append(f"<section class='cat{cls}'>")
        parts.append(f"<h2>{_esc(label)} "
                     f"<span class='count'>{len(items)}</span></h2>")
        if blurb:
            parts.append(f"<p class='blurb'>{_esc(blurb)}</p>")
        if not items:
            parts.append("<div class='empty'>No reports yet.</div>")
        else:
            parts.append("<div class='grid'>")
            for r in items:
                parts.append(_render_card(r))
            parts.append("</div>")
        parts.append("</section>")

    parts.append(f"<div class='footer'>Generated {_esc(today)} by "
                 f"<code>build_index.py</code> · run it after creating any "
                 f"new report so this page stays in sync.</div>")
    parts.append("</body></html>")
    return "\n".join(parts)


def build() -> Path:
    """Discover all reports, write `reports/index.html`, return its path."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    by_cat = discover_all()
    OUT_PATH.write_text(render(by_cat))
    return OUT_PATH


if __name__ == "__main__":
    p = build()
    print(f"Wrote {p}")
    sys.exit(0)
