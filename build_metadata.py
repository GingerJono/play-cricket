#!/usr/bin/env python3
"""
Generate the static opposition-metadata pages under `app/metadata/`.

  app/metadata/clubs.html           Generated overview of every relevant club
  app/metadata/club/<club_id>.html  Per-club roster (one card per player)
  app/metadata/player.html          ONE template, reads ?id=<player_id>
                                    from the URL and fetches
                                    app/data/players.json
  app/data/players.json             Bundled per-player records (~3MB)
  app/static/app.css                Shared stylesheet (written by _app_lib)
  app/static/player.js              Player-page logic + submit form

Universe is restricted to **opposition we actually scout**: Rainham 1st
XI in League games or non-T20 Cup games (`_app_lib.first_xi_match_ids`).
A club / player only shows up if they appear on a `match_players` row
for one of those matches.

Submit form has NO backend. Plain JS builds a structured plain-text
body and opens either a `mailto:` or `wa.me/` link. The user (Jono)
receives it on Android, pastes it into Claude Code, and Claude writes a
JSON file under `data/metadata/submissions/`.

Reads:  data/rainham.db, data/metadata/{players,videos,submissions}/
Writes: app/metadata/..., app/data/players.json, app/static/player.js
"""

from __future__ import annotations

import json
import os
import sys
from html import escape

import _app_lib as L


SUBMIT_EMAIL = os.environ.get("RCC_SUBMIT_EMAIL", "")
SUBMIT_WHATSAPP = os.environ.get("RCC_SUBMIT_WHATSAPP", "")


# ---------------------------------------------------------------- helpers --

def player_aliases_and_clubs(conn) -> tuple[
    dict[int, list[str]], dict[int, list[tuple[str, str]]]
]:
    """Per-player: most-recent display name + every club ever played for."""
    aliases: dict[int, list[str]] = {}
    clubs: dict[int, list[tuple[str, str]]] = {}
    cur = conn.cursor()

    rows = cur.execute("""
        SELECT b.batsman_id AS pid, b.batsman_name AS name, m.match_date AS d
        FROM batting b
        JOIN matches m ON m.match_id = b.match_id
        WHERE b.batsman_id IS NOT NULL AND b.batsman_name <> ''
    """).fetchall()
    by_pid: dict[int, list[tuple[str, str]]] = {}
    for r in rows:
        by_pid.setdefault(r["pid"], []).append(
            (L.date_yyyymmdd(r["d"] or ""), r["name"])
        )
    for pid, lst in by_pid.items():
        lst.sort(reverse=True)
        seen: set[str] = set(); names = []
        for _, n in lst:
            if n and n not in seen:
                names.append(n); seen.add(n)
        aliases[pid] = names

    rows = cur.execute("""
        SELECT mp.player_id AS pid, mp.club_id AS cid, c.club_name AS cname,
               m.match_date AS d
        FROM match_players mp
        LEFT JOIN clubs c ON c.club_id = mp.club_id
        JOIN matches m ON m.match_id = mp.match_id
        WHERE mp.player_id IS NOT NULL AND mp.club_id <> ''
    """).fetchall()
    by_pid_c: dict[int, list[tuple[str, str, str]]] = {}
    for r in rows:
        by_pid_c.setdefault(r["pid"], []).append(
            (L.date_yyyymmdd(r["d"] or ""), r["cid"], r["cname"] or r["cid"])
        )
    for pid, lst in by_pid_c.items():
        lst.sort(reverse=True)
        seen: set[str] = set(); out: list[tuple[str, str]] = []
        for _, cid, cname in lst:
            if cid and cid not in seen:
                out.append((cid, cname)); seen.add(cid)
        clubs[pid] = out

    return aliases, clubs


def pending_submissions_by_player() -> dict[int, int]:
    out: dict[int, int] = {}
    if not L.SUBMISSIONS_META_DIR.exists():
        return out
    for p in L.SUBMISSIONS_META_DIR.glob("*.json"):
        try:
            d = json.loads(p.read_text())
            if d.get("status") != "pending":
                continue
            pid = int(d.get("player_id"))
        except Exception:
            continue
        out[pid] = out.get(pid, 0) + 1
    return out


def overlay_status(player_id: int, base_status: str,
                   pending_counts: dict[int, int]) -> str:
    n = pending_counts.get(player_id, 0)
    if n >= 2:
        return "conflicting"
    if n == 1:
        return "needs review"
    return base_status


# --------------------------------------------------------- universe scope --

# Match the data-repo window so fixture / club / player counts stay in
# sync across the site.
SEASONS_BACK = 10


def relevant_match_ids(conn) -> set[int]:
    """
    1st XI / League + non-T20 Cup, last 10 seasons.

    Includes BOTH played and scheduled-but-unplayed fixtures so that a
    club we'll face this season (e.g. Spartans CC's two 2026 League
    games) shows up even before we've played them.
    """
    import datetime as dt
    season_min = dt.date.today().year - SEASONS_BACK + 1
    return set(L.first_xi_match_ids(
        conn, season_min=season_min, include_unplayed=True,
    ))


def relevant_club_player_pairs(
    conn, match_ids: set[int]
) -> dict[str, set[int]]:
    """
    For each non-Rainham club we have a relevant fixture against
    (past OR upcoming), the set of every `player_id` we've ever seen
    on that club's `match_players` rows.

    Rationale: a club like Spartans CC, who we'll play for the first
    time this season, has no past Rainham fixtures — but we have 350+
    of their players cached from their own scouting fetches. Showing
    their full roster lets Jono start submitting metadata before we
    play them.
    """
    if not match_ids:
        return {}
    cur = conn.cursor()
    qmarks = ",".join("?" * len(match_ids))

    # Step 1: opposition club_ids that show up on any side of a
    # relevant fixture (using the matches table directly so we don't
    # need rosters cached on those matches).
    opp_clubs: set[str] = set()
    for r in cur.execute(f"""
        SELECT home_club_id, away_club_id
        FROM matches WHERE match_id IN ({qmarks})
    """, list(match_ids)).fetchall():
        for cid in r:
            if cid and cid != L.RAINHAM_CLUB_ID:
                opp_clubs.add(cid)
    if not opp_clubs:
        return {}

    # Step 2: every player_id ever seen on those clubs' rosters.
    cmarks = ",".join("?" * len(opp_clubs))
    out: dict[str, set[int]] = {}
    for r in cur.execute(f"""
        SELECT club_id, player_id
        FROM match_players
        WHERE club_id IN ({cmarks})
          AND player_id IS NOT NULL
    """, list(opp_clubs)).fetchall():
        out.setdefault(r[0], set()).add(int(r[1]))

    # Make sure clubs with zero cached roster still appear (e.g. a
    # 2026 fixture vs a club we've never seen players from).
    for cid in opp_clubs:
        out.setdefault(cid, set())

    return out


# --------------------------------------------------------- per-player JSON --

def player_record(conn, pid: int, aliases, clubs_by_player,
                  videos: set[int], all_meta) -> dict:
    cur = conn.cursor()
    name = (aliases.get(pid) or [f"#{pid}"])[0]
    seen_clubs = clubs_by_player.get(pid, [])
    meta_file = all_meta.get(pid) or {}
    meta = meta_file.get("metadata") or {}

    rows = cur.execute("""
        SELECT m.match_id AS mid, m.match_date AS d,
               CASE WHEN mp.team_side = 'home' THEN m.away_club_name
                    ELSE m.home_club_name END AS opp
        FROM match_players mp
        JOIN matches m ON m.match_id = mp.match_id
        WHERE mp.player_id = ?
        ORDER BY m.match_date DESC
    """, (pid,)).fetchall()

    evidence_matches = []
    for r in rows:
        mid = int(r["mid"])
        if mid not in videos:
            continue
        evidence_matches.append({
            "match_id": mid,
            "date": r["d"] or "",
            "opp": r["opp"] or "",
            "videos": L.load_videos_for(mid),
        })

    return {
        "player_id": pid,
        "name": name,
        "aliases": aliases.get(pid, []),
        "seen_clubs": [{"club_id": c, "club_name": n}
                       for c, n in seen_clubs],
        "metadata": meta,
        "approved_at": meta_file.get("approved_at"),
        "approved_by": meta_file.get("approved_by"),
        "evidence_matches": evidence_matches,
    }


# ----------------------------------------------------------------- pages --

def fixture_summary_for_hero(
    conn, match_ids: set[int]
) -> tuple[list[tuple[str, str]], dict[int, int]]:
    """Returns (hero stats list, per-season fixture count)."""
    if not match_ids:
        return ([("0", "fixtures"), ("0", "clubs"), ("0", "players")], {})
    qmarks = ",".join("?" * len(match_ids))
    season_counts = {}
    rows = conn.execute(f"""
        SELECT season, COUNT(*) AS n
        FROM matches WHERE match_id IN ({qmarks})
        GROUP BY season
    """, list(match_ids)).fetchall()
    for r in rows:
        season_counts[int(r["season"] or 0)] = int(r["n"])
    return season_counts


def build_clubs_index(conn, all_meta, aliases, clubs_by_player,
                      pending: dict[int, int],
                      match_ids: set[int],
                      players_by_club: dict[str, set[int]]) -> int:
    cur = conn.cursor()

    # Fixtures-vs-us per club, across the relevant 1st-XI universe.
    # Counts both played and upcoming-this-season — same as the player
    # universe — so a brand-new opponent (Spartans, in 2026) shows up
    # even before we've played them.
    fixtures_by_club: dict[str, int] = {}
    if match_ids:
        qm = ",".join("?" * len(match_ids))
        for r in cur.execute(f"""
            SELECT CASE WHEN home_club_id = ? THEN away_club_id
                        ELSE home_club_id END AS opp_cid,
                   COUNT(*) AS n
            FROM matches
            WHERE match_id IN ({qm})
            GROUP BY opp_cid
        """, (L.RAINHAM_CLUB_ID, *match_ids)).fetchall():
            fixtures_by_club[r[0]] = r[1]

    # Some opposition clubs (e.g. a club we have a 2026 fixture against
    # but no roster cached because the fixture is unplayed) won't have
    # a row in `players_by_club`. Make sure they still appear.
    all_cids = set(players_by_club) | set(fixtures_by_club)

    rows = []
    for cid in all_cids:
        pids = players_by_club.get(cid, set())
        cname_row = cur.execute(
            "SELECT club_name FROM clubs WHERE club_id = ?", (cid,)
        ).fetchone()
        cname = (cname_row[0] if cname_row else cid) or cid
        n_complete = n_partial = n_notcap = n_review = 0
        for pid in pids:
            base = L.metadata_status((all_meta.get(pid) or {}).get("metadata"))
            s = overlay_status(pid, base, pending)
            if s == "complete":
                n_complete += 1
            elif s == "partial":
                n_partial += 1
            elif s in ("needs review", "conflicting"):
                n_review += 1
            else:
                n_notcap += 1
        rows.append({
            "cid": cid, "cname": cname,
            "n": len(pids),
            "n_fixtures": fixtures_by_club.get(cid, 0),
            "n_complete": n_complete, "n_partial": n_partial,
            "n_notcap": n_notcap, "n_review": n_review,
        })
    # Sort by fixtures-vs-us (desc), then by squad size as tie-breaker.
    rows.sort(key=lambda r: (r["n_fixtures"], r["n"]), reverse=True)

    # Hero stats
    n_clubs = len(rows)
    n_players_total = sum(r["n"] for r in rows)
    n_complete_total = sum(r["n_complete"] for r in rows)
    season_counts = fixture_summary_for_hero(conn, match_ids)
    n_fixtures = sum(season_counts.values())

    chips = "".join(
        f'<span class="season-chip">'
        f'<span class="y">{s}</span> {n}'
        f'</span>'
        for s, n in sorted(season_counts.items(), reverse=True)[:6]
    )

    body = [
        L.hero(
            "Opposition clubs",
            crumbs=[
                ("Rainham CC", "../index.html"),
                ("Clubs", ""),
            ],
            lead=("Every club Rainham 1st XI has played in League or "
                  "non-T20 Cup fixtures across the last 10 seasons. Tap "
                  "a club to see its players and start filling in their "
                  "metadata."),
            stats=[
                (str(n_fixtures), "fixtures"),
                (str(n_clubs), "clubs"),
                (str(n_players_total), "players"),
                (str(n_complete_total), "complete"),
            ],
            extra_html=f'<div class="season-chips">{chips}</div>',
        ),
        '<div class="card">',
        '<h2>Clubs</h2>',
        '<p class="note">Sorted by squad size — the most-encountered '
        'opposition first.</p>',
        '<div class="row-list">',
    ]

    for r in rows:
        chips_html = (
            f'<span class="chip c">{r["n_complete"]}✓</span>'
            f'<span class="chip p">{r["n_partial"]}</span>'
            f'<span class="chip">{r["n_notcap"]}</span>'
            + (f'<span class="chip r">{r["n_review"]}!</span>'
               if r["n_review"] else "")
        )
        nf = r["n_fixtures"]
        f_label = f'{nf} fix' if nf else 'no fixtures'
        body.append(
            f'<a class="row-link" href="club/{escape(r["cid"])}.html">'
            f'<div class="name">{escape(r["cname"])}'
            f'<div class="rollup" style="margin-top:4px">{chips_html}</div>'
            f'</div>'
            f'<div class="right">'
            f'<span class="count">{f_label}</span>'
            f'<span class="count" style="opacity:.65">· {r["n"]} pl</span>'
            f'</div>'
            f'</a>'
        )
    body.append('</div></div>')

    out = L.APP_DIR / "metadata" / "clubs.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(L.page("".join(body),
        title="Opposition clubs",
        css_href="../static/app.css",
    ))
    return len(rows)


def build_club_pages(conn, all_meta, aliases, clubs_by_player,
                     videos: set[int], pending: dict[int, int],
                     players_by_club: dict[str, set[int]],
                     match_ids: set[int]) -> int:
    cur = conn.cursor()
    n_pages = 0
    qmark_matches = ",".join("?" * len(match_ids)) if match_ids else "NULL"

    for cid, pids in players_by_club.items():
        cname_row = cur.execute(
            "SELECT club_name FROM clubs WHERE club_id = ?", (cid,)
        ).fetchone()
        cname = (cname_row["club_name"] if cname_row else cid) or cid

        # Per-player stats: # of relevant fixtures vs Rainham + last seen
        # vs us (when applicable), plus a fallback "last seen on this
        # club" so fresh opponents (Spartans 2026) sort by squad
        # recency rather than by alphabetical accident.
        pid_stats: list[dict] = []
        for pid in pids:
            vs_us = cur.execute(f"""
                SELECT COUNT(*) AS n, MAX(m.match_date) AS last_seen
                FROM match_players mp
                JOIN matches m ON m.match_id = mp.match_id
                WHERE mp.player_id = ?
                  AND mp.club_id = ?
                  AND mp.match_id IN ({qmark_matches})
            """, (pid, cid, *match_ids)).fetchone()
            n_vs = vs_us[0] or 0
            last_vs = vs_us[1] or ""
            on_club = cur.execute("""
                SELECT COUNT(*) AS n, MAX(m.match_date) AS last_seen
                FROM match_players mp
                JOIN matches m ON m.match_id = mp.match_id
                WHERE mp.player_id = ? AND mp.club_id = ?
            """, (pid, cid)).fetchone()
            n_club = on_club[0] or 0
            last_club = on_club[1] or ""
            has_video = bool(videos) and any(
                int(m[0]) in videos for m in cur.execute(
                    "SELECT match_id FROM match_players WHERE player_id = ?",
                    (pid,)).fetchall()
            )
            pid_stats.append({
                "pid": pid,
                "name": (aliases.get(pid) or [f"#{pid}"])[0],
                "n_vs_us":      n_vs,
                "last_seen_vs": last_vs,
                "ymd_vs":       L.date_yyyymmdd(last_vs),
                "n_on_club":    n_club,
                "last_on_club": last_club,
                "ymd_club":     L.date_yyyymmdd(last_club),
                "has_video":    has_video,
            })
        # Sort: apps-vs-us first, fall back to total club apps + recency.
        pid_stats.sort(
            key=lambda x: (x["n_vs_us"], x["ymd_vs"],
                           x["n_on_club"], x["ymd_club"]),
            reverse=True,
        )

        # Status rollup (re-using the same logic as the index)
        n_c = n_p = n_n = n_r = 0
        for pid in pids:
            base = L.metadata_status((all_meta.get(pid) or {}).get("metadata"))
            s = overlay_status(pid, base, pending)
            if s == "complete":   n_c += 1
            elif s == "partial":  n_p += 1
            elif s in ("needs review", "conflicting"): n_r += 1
            else: n_n += 1

        # Differentiate played-against vs upcoming-only opponents in
        # the lead text and roster note.
        any_apps_vs_us = any(s["n_vs_us"] for s in pid_stats)
        if any_apps_vs_us:
            lead = (f"Players seen on {escape(cname)} when they faced "
                    f"our 1st XI in League or non-T20 Cup fixtures.")
            note = ("Sorted by appearances against our 1st XI. Tap a "
                    "name to view metadata or submit additions.")
        else:
            lead = (f"We haven't played {escape(cname)} yet in the "
                    f"1st-XI universe — these are players seen on their "
                    f"roster from any context, sorted most-active first.")
            note = ("Sorted by appearances on this club's roster. Tap "
                    "a name to view metadata or submit additions.")

        body = [
            L.hero(
                cname,
                crumbs=[
                    ("Rainham CC", "../../index.html"),
                    ("Clubs", "../clubs.html"),
                    (cname, ""),
                ],
                lead=lead,
                stats=[
                    (str(len(pids)), "players"),
                    (str(n_c), "complete"),
                    (str(n_p), "partial"),
                    (str(n_n), "missing"),
                ],
            ),
            '<div class="card">',
            '<h2>Roster</h2>',
            f'<p class="note">{note}</p>',
            '<div class="row-list">',
        ]

        for s in pid_stats:
            base = L.metadata_status((all_meta.get(s["pid"]) or {}).get("metadata"))
            status = overlay_status(s["pid"], base, pending)
            video_chip = ('<span class="tag yes">🎬</span>' if s["has_video"]
                          else "")
            if s["n_vs_us"]:
                meta_line = (f'{s["n_vs_us"]} app vs us · '
                             f'last seen {escape(s["last_seen_vs"])}')
            elif s["n_on_club"]:
                meta_line = (f'{s["n_on_club"]} app for {escape(cname)} · '
                             f'last seen {escape(s["last_on_club"])}')
            else:
                meta_line = "no record"
            body.append(
                f'<a class="row-link" href="../player.html?id={s["pid"]}">'
                f'<div class="name">{escape(s["name"])}'
                f'<div class="meta" style="font-size:10.5px;color:var(--muted);'
                f'margin-top:2px">{meta_line}</div></div>'
                f'<div class="right">{video_chip}{L.status_pill(status)}</div>'
                f'</a>'
            )
        body.append("</div></div>")

        out = L.APP_DIR / "metadata" / "club" / f"{cid}.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(L.page("".join(body), title=cname,
            css_href="../../static/app.css"))
        n_pages += 1
    return n_pages


def build_player_data(conn, all_meta, aliases, clubs_by_player,
                      videos: set[int],
                      players_by_club: dict[str, set[int]]) -> int:
    """Single bundled players.json keyed by player_id."""
    pids = sorted({pid for pids in players_by_club.values() for pid in pids})
    bundle: dict[str, dict] = {}
    for pid in pids:
        bundle[str(pid)] = player_record(
            conn, pid, aliases, clubs_by_player, videos, all_meta
        )
    out = L.APP_DIR / "data" / "players.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(bundle, separators=(",", ":")))
    return len(bundle)


def write_player_template_and_js() -> None:
    L.APP_DIR.joinpath("metadata").mkdir(parents=True, exist_ok=True)

    # The page is rendered entirely from JS once the bundle loads.
    body = (
        '<header class="hero" id="hero">'
        '<div class="crumbs">'
        '<a href="../index.html">Rainham CC</a>'
        '<span class="sep">&rsaquo;</span>'
        '<a href="clubs.html">Clubs</a>'
        '<span class="sep">&rsaquo;</span>'
        '<span>Player</span>'
        '</div>'
        '<h1 id="player-name">Loading…</h1>'
        '<p class="lead" id="player-status"></p>'
        '</header>'
        '<main>'
        '<div id="meta-box"></div>'
        '<div id="video-box"></div>'
        '<div id="form-box"></div>'
        '</main>'
    )
    page_html = L.page(body, title="Player metadata",
        css_href="../static/app.css",
        extra_body='<script src="../static/player.js"></script>',
    )
    (L.APP_DIR / "metadata" / "player.html").write_text(page_html)

    js = (PLAYER_JS_TEMPLATE
          .replace("__SUBMIT_EMAIL__", json.dumps(SUBMIT_EMAIL))
          .replace("__SUBMIT_WHATSAPP__", json.dumps(SUBMIT_WHATSAPP)))
    (L.APP_DIR / "static" / "player.js").write_text(js)


PLAYER_JS_TEMPLATE = r"""
// Player metadata page — runtime renderer + submission form.
//
// Reads ?id=<player_id> from the URL, fetches ../data/players.json
// (one bundled file, ~3 MB, browser-cached after first hit) and looks
// the player up by id. The submit form has NO backend; it composes a
// structured plain-text body and opens either a mailto: or wa.me/ link.
(function() {
  const SUBMIT_EMAIL = __SUBMIT_EMAIL__;
  const SUBMIT_WHATSAPP = __SUBMIT_WHATSAPP__;

  const params = new URLSearchParams(location.search);
  const pid = parseInt(params.get('id'), 10);
  if (!pid) {
    document.getElementById('player-name').textContent = 'Missing ?id=';
    return;
  }

  fetch('../data/players.json')
    .then(r => r.ok ? r.json() : Promise.reject(r.status))
    .then(bundle => {
      const p = bundle[String(pid)];
      if (!p) {
        document.getElementById('player-name').textContent =
          'Player #' + pid + ' not in bundle';
        return;
      }
      render(p);
    })
    .catch(err => {
      document.getElementById('player-name').textContent =
        'Failed to load players.json (' + err + ')';
    });

  const FIELD_LABELS = {
    batting_hand: 'Batting hand',
    bowling_type: 'Bowling type',
    pace_type:    'Pace type',
    spin_type:    'Spin type',
    bowling_arm:  'Bowling arm',
    angle_to_rhb: 'Angle to RHB',
  };

  const FIELD_OPTIONS = {
    batting_hand: [['right','Right-handed'],['left','Left-handed'],['unknown','Unknown']],
    bowling_type: [['pace','Pace'],['spin','Spin'],['none',"Doesn't bowl"],['unknown','Unknown']],
    pace_type:    [['fast','Fast'],['medium','Medium'],['slow','Slow'],['unknown','Unknown']],
    spin_type:    [['wrist','Wrist'],['finger','Finger'],['unknown','Unknown']],
    bowling_arm:  [['right','Right arm'],['left','Left arm'],['unknown','Unknown']],
    angle_to_rhb: [['over','Over the wicket'],['round','Round the wicket'],
                   ['varies','Varies'],['unknown','Unknown']],
  };

  function el(tag, attrs, children) {
    const e = document.createElement(tag);
    if (attrs) for (const k in attrs) {
      if (k === 'class') e.className = attrs[k];
      else if (k === 'html') e.innerHTML = attrs[k];
      else e.setAttribute(k, attrs[k]);
    }
    if (children) for (const c of children) {
      e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    }
    return e;
  }

  function statusOf(meta) {
    if (!meta) return 'not captured';
    const has = k => meta[k] != null && meta[k] !== '';
    if (!has('batting_hand') || !has('bowling_type')) return 'partial';
    if (meta.bowling_type === 'pace' || meta.bowling_type === 'spin') {
      if (!has('bowling_arm') || !has('angle_to_rhb')) return 'partial';
      if (meta.bowling_type === 'pace' && !has('pace_type')) return 'partial';
      if (meta.bowling_type === 'spin' && !has('spin_type')) return 'partial';
    }
    return 'complete';
  }

  function statusPill(s) {
    const cls = {
      'complete': 'complete',
      'partial': 'partial',
      'not captured': 'notcap',
    }[s] || 'notcap';
    const sp = el('span', {class: 'tag ' + cls});
    sp.textContent = s;
    return sp;
  }

  function render(p) {
    document.title = p.name + ' — metadata';
    const nameEl = document.getElementById('player-name');
    nameEl.textContent = p.name;

    const stEl = document.getElementById('player-status');
    stEl.innerHTML = '';
    stEl.appendChild(document.createTextNode('#' + p.player_id + ' · '));
    stEl.appendChild(statusPill(statusOf(p.metadata)));
    if (p.seen_clubs && p.seen_clubs.length) {
      stEl.appendChild(document.createTextNode(' · '));
      const clubsBit = el('span', {style: 'opacity:.85'});
      p.seen_clubs.forEach((c, i) => {
        if (i) clubsBit.appendChild(document.createTextNode(', '));
        const a = el('a', {href: 'club/' + c.club_id + '.html',
                           style: 'color:#fff'});
        a.textContent = c.club_name;
        clubsBit.appendChild(a);
      });
      stEl.appendChild(clubsBit);
    }

    // Current metadata
    const mb = document.getElementById('meta-box');
    const card = el('div', {class: 'card'});
    card.appendChild(el('h2', null, ['Current metadata']));
    const m = p.metadata || {};
    const fields = ['batting_hand','bowling_type','pace_type','spin_type',
                    'bowling_arm','angle_to_rhb'];
    const populated = fields.filter(k => m[k]);
    if (!populated.length) {
      card.appendChild(el('p', {class: 'note'}, ['No metadata captured yet.']));
    } else {
      const grid = el('div', {class: 'meta-grid'});
      populated.forEach(k => {
        grid.appendChild(el('div', null, [
          el('div', {class: 'k'}, [FIELD_LABELS[k]]),
          el('div', {class: 'v'}, [String(m[k])]),
        ]));
      });
      card.appendChild(grid);
      if (m.notes) card.appendChild(el('p', {class: 'note'}, [m.notes]));
    }
    mb.appendChild(card);

    // Video evidence
    const vb = document.getElementById('video-box');
    const vCard = el('div', {class: 'card'});
    vCard.appendChild(el('h2', null, ['Video evidence']));
    const withVideos = (p.evidence_matches || []);
    if (!withVideos.length) {
      vCard.appendChild(el('p', {class: 'note'}, [
        'No videos recorded for matches involving this player yet. ' +
        'Video links live in data/metadata/videos/<match_id>.json.'
      ]));
    } else {
      const list = el('div', {class: 'fix-list'});
      withVideos.forEach(mt => {
        const card = el('div', {class: 'fix'});
        const r1 = el('div', {class: 'row1'});
        const left = el('div', {class: 'left'});
        left.appendChild(el('div', {class: 'date'}, [mt.date]));
        left.appendChild(el('div', {class: 'opp'}, ['vs ' + mt.opp]));
        r1.appendChild(left);
        const right = el('div', {class: 'right'});
        right.appendChild(el('span', {class: 'tag yes'},
          ['🎬 ' + mt.videos.length]));
        r1.appendChild(right);
        card.appendChild(r1);
        mt.videos.forEach(v => {
          const a = el('a', {href: v.url, target: '_blank',
                              style: 'display:block;font-size:12px;' +
                                     'margin-top:3px;font-weight:600'});
          a.textContent = v.label || 'watch';
          card.appendChild(a);
        });
        list.appendChild(card);
      });
      vCard.appendChild(list);
    }
    vb.appendChild(vCard);

    renderForm(p);
  }

  function renderForm(p) {
    const fb = document.getElementById('form-box');
    const card = el('div', {class: 'card'});
    card.appendChild(el('h2', null, ['Submit metadata']));
    card.appendChild(el('p', {class: 'note'}, [
      'Fill in what you know. The button below opens your email or ' +
      'WhatsApp with a structured message; Jono receives it and adds ' +
      'it to the queue. Nothing is sent until you press the button in ' +
      'your mail / WA app.'
    ]));

    const form = el('form', {id: 'metaForm', onsubmit: 'return false;'});
    const m = p.metadata || {};

    function selectFor(name, opts) {
      const sel = el('select', {id: 'f-' + name, name: name});
      sel.appendChild(el('option', {value: ''}, ['(unchanged)']));
      opts.forEach(([v, l]) => {
        const o = el('option', {value: v}, [l]);
        if ((m[name] || '') === v) o.setAttribute('selected', 'selected');
        sel.appendChild(o);
      });
      return sel;
    }
    function row(items) {
      const r = el('div', {class: 'field-row'});
      items.forEach(it => r.appendChild(it));
      return r;
    }
    function labeled(lbl, ctrl) {
      return el('div', null, [el('label', null, [lbl]), ctrl]);
    }

    form.appendChild(row([
      labeled('Batting hand',     selectFor('batting_hand', FIELD_OPTIONS.batting_hand)),
      labeled('Bowling type',     selectFor('bowling_type', FIELD_OPTIONS.bowling_type)),
    ]));
    form.appendChild(row([
      labeled('Pace type (if pace)', selectFor('pace_type', FIELD_OPTIONS.pace_type)),
      labeled('Spin type (if spin)', selectFor('spin_type', FIELD_OPTIONS.spin_type)),
    ]));
    form.appendChild(row([
      labeled('Bowling arm',  selectFor('bowling_arm', FIELD_OPTIONS.bowling_arm)),
      labeled('Angle to RHB', selectFor('angle_to_rhb', FIELD_OPTIONS.angle_to_rhb)),
    ]));

    const vidMatches = (p.evidence_matches || []);
    const evid = el('select', {id: 'f-evidence', name: 'evidence_match_ids',
                               multiple: 'multiple', size: '4'});
    if (vidMatches.length) {
      vidMatches.forEach(mt => {
        const lbl = mt.date + ' — ' + mt.opp + ' (' + mt.videos.length +
                    ' video' + (mt.videos.length !== 1 ? 's' : '') + ')';
        evid.appendChild(el('option', {value: String(mt.match_id)}, [lbl]));
      });
    } else {
      const o = el('option', null, ['(no matches with video evidence yet)']);
      o.setAttribute('disabled', 'disabled');
      evid.appendChild(o);
    }
    form.appendChild(el('label', null, ['Evidence matches (multi-select)']));
    form.appendChild(evid);

    form.appendChild(el('label', null, ['Notes / reasoning (optional)']));
    form.appendChild(el('textarea', {
      id: 'f-notes', name: 'notes',
      placeholder: 'Saw him bowl in the U13 game…',
    }));
    form.appendChild(el('label', null, ['Your name (optional)']));
    form.appendChild(el('input', {
      id: 'f-by', name: 'submitted_by',
      placeholder: 'e.g. Jane Smith',
    }));

    const btnRow = el('div', {class: 'btn-row'});
    const mailBtn = el('a', {href: '#', id: 'sendMail', class: 'btn'},
                       ['Send via email']);
    const waBtn   = el('a', {href: '#', id: 'sendWA', class: 'btn secondary'},
                       ['Send via WhatsApp']);
    btnRow.appendChild(mailBtn);
    btnRow.appendChild(waBtn);
    form.appendChild(btnRow);
    form.appendChild(el('p', {class: 'note', style: 'margin-top:10px'},
      ['Receiving address: ', el('code', {id: 'addrPreview'})]));

    card.appendChild(form);
    fb.appendChild(card);

    document.getElementById('addrPreview').textContent =
      SUBMIT_EMAIL ? SUBMIT_EMAIL :
      (SUBMIT_WHATSAPP ? '+' + SUBMIT_WHATSAPP + ' (WhatsApp)' :
       '(not configured)');

    function valOf(name) {
      const e = document.getElementById('f-' + name);
      if (!e) return '';
      if (e.multiple) {
        return Array.from(e.selectedOptions).map(o => o.value).join(', ');
      }
      return e.value;
    }
    function buildBody() {
      return [
        'PLAYER METADATA SUBMISSION',
        'player_id: ' + p.player_id,
        'player_name: ' + p.name,
        'batting_hand: ' + valOf('batting_hand'),
        'bowling_type: ' + valOf('bowling_type'),
        'pace_type: ' + valOf('pace_type'),
        'spin_type: ' + valOf('spin_type'),
        'bowling_arm: ' + valOf('bowling_arm'),
        'angle_to_rhb: ' + valOf('angle_to_rhb'),
        'evidence_match_ids: ' + valOf('evidence'),
        'notes: ' + valOf('notes'),
        'submitted_by: ' + valOf('by'),
      ].join('\n');
    }

    mailBtn.addEventListener('click', e => {
      e.preventDefault();
      if (!SUBMIT_EMAIL) { alert('Email destination not configured'); return; }
      const subject = 'Player metadata: ' + p.name + ' (#' + p.player_id + ')';
      location.href = 'mailto:' + encodeURIComponent(SUBMIT_EMAIL)
        + '?subject=' + encodeURIComponent(subject)
        + '&body=' + encodeURIComponent(buildBody());
    });
    waBtn.addEventListener('click', e => {
      e.preventDefault();
      if (!SUBMIT_WHATSAPP) { alert('WhatsApp destination not configured'); return; }
      window.open('https://wa.me/' + SUBMIT_WHATSAPP
        + '?text=' + encodeURIComponent(buildBody()), '_blank');
    });
  }
})();
"""


# ----------------------------------------------------------------- main --

def build() -> int:
    L.write_static_assets()
    conn = L.open_db()
    all_meta = L.load_all_player_meta()
    aliases, clubs_by_player = player_aliases_and_clubs(conn)
    videos = L.matches_with_videos()
    pending = pending_submissions_by_player()

    match_ids = relevant_match_ids(conn)
    if not match_ids:
        print("No relevant 1st-XI fixtures found — abort.", file=sys.stderr)
        return 1
    players_by_club = relevant_club_player_pairs(conn, match_ids)

    print("Building app/metadata/clubs.html ...", flush=True)
    n = build_clubs_index(conn, all_meta, aliases, clubs_by_player,
                          pending, match_ids, players_by_club)
    print(f"  {n} clubs listed", flush=True)

    print("Building app/metadata/club/<club_id>.html ...", flush=True)
    n = build_club_pages(conn, all_meta, aliases, clubs_by_player,
                         videos, pending, players_by_club, match_ids)
    print(f"  {n} club pages", flush=True)

    print("Building app/data/players.json bundle ...", flush=True)
    n = build_player_data(conn, all_meta, aliases, clubs_by_player,
                          videos, players_by_club)
    print(f"  {n} players in bundle", flush=True)

    print("Writing app/metadata/player.html template + app/static/player.js ...",
          flush=True)
    write_player_template_and_js()

    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(build())
