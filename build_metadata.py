#!/usr/bin/env python3
"""
Generate the static opposition-metadata pages under `app/metadata/`.

Site shape — kept SPA-ish to avoid a 14k-file commit:

  app/metadata/clubs.html           Generated overview of every club
  app/metadata/club/<club_id>.html  Per-club roster (~277 pages, small)
  app/metadata/player.html          ONE template, reads ?id=<player_id>
                                    from the URL and fetches
                                    app/data/players/<id>.json
  app/data/players/<player_id>.json Small per-player record
                                    (metadata + match list + video flag)
  app/static/app.css                Shared stylesheet (written by _app_lib)
  app/static/player.js              Player-page logic + submit form

The submit form has NO backend. Plain JS builds a structured plain-text
body and opens either a `mailto:` or `wa.me/` link. The user (Jono)
receives it on Android, pastes it into Claude Code, and Claude writes a
JSON file under `data/metadata/submissions/`.

Reads:  data/rainham.db, data/metadata/{players,videos,submissions}/
Writes: app/metadata/..., app/data/players/, app/static/player.js
"""

from __future__ import annotations

import json
import os
import sys
from html import escape

import _app_lib as L


# Submission destinations — set via env vars. Both end up baked into
# the generated player.js so the submitter never needs to type them.
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


# --------------------------------------------------------- per-player JSON --

def player_record(conn, pid: int, aliases, clubs_by_player,
                  videos: set[int], all_meta) -> dict:
    """The compact JSON the player.html template fetches."""
    cur = conn.cursor()
    name = (aliases.get(pid) or [f"#{pid}"])[0]
    seen_clubs = clubs_by_player.get(pid, [])
    meta_file = all_meta.get(pid) or {}
    meta = meta_file.get("metadata") or {}

    # Only include matches that HAVE video evidence — those are the only
    # ones the player page actually surfaces (in the evidence table and
    # in the form's match-picker). Skipping the rest keeps the JSON
    # files small for the 14k-player set.
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

def build_clubs_index(conn, all_meta, aliases, clubs_by_player,
                      pending: dict[int, int]) -> int:
    cur = conn.cursor()
    rows = cur.execute(f"""
        SELECT mp.club_id AS cid, c.club_name AS cname,
               COUNT(DISTINCT mp.player_id) AS n_players
        FROM match_players mp
        LEFT JOIN clubs c ON c.club_id = mp.club_id
        WHERE mp.club_id <> '{L.RAINHAM_CLUB_ID}' AND mp.club_id <> ''
        GROUP BY mp.club_id, c.club_name
        ORDER BY n_players DESC
    """).fetchall()

    body = ['<h1>Opposition clubs</h1>',
            '<p class="lead">Browse the roster of every club Rainham has '
            'played. Click a club name to view their players and submit '
            'metadata. Status pills count distinct <code>player_id</code>s.</p>',
            '<table><thead><tr>'
            '<th>Club</th>'
            '<th class="num">Players</th>'
            '<th class="num">Complete</th>'
            '<th class="num">Partial</th>'
            '<th class="num">Not captured</th>'
            '<th class="num">Needs review</th>'
            '</tr></thead><tbody>']

    for r in rows:
        cid = r["cid"]
        pids = [
            int(x[0]) for x in cur.execute(
                "SELECT DISTINCT player_id FROM match_players WHERE club_id = ?",
                (cid,)
            ).fetchall() if x[0] is not None
        ]
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
        cname = r["cname"] or cid
        body.append(
            "<tr>"
            f'<td><a href="club/{escape(cid)}.html">{escape(cname)}</a></td>'
            f'<td class="num">{r["n_players"]}</td>'
            f'<td class="num">{n_complete}</td>'
            f'<td class="num">{n_partial}</td>'
            f'<td class="num">{n_notcap}</td>'
            f'<td class="num">{n_review}</td>'
            "</tr>"
        )
    body.append("</tbody></table>")

    out = L.APP_DIR / "metadata" / "clubs.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(L.page("Opposition clubs", "".join(body),
        css_href="../static/app.css",
        crumbs=[
            ("Data repository", "../index.html"),
            ("Opposition clubs", ""),
        ]))
    return len(rows)


def build_club_pages(conn, all_meta, aliases, clubs_by_player,
                     videos: set[int], pending: dict[int, int]) -> int:
    cur = conn.cursor()
    rows = cur.execute(f"""
        SELECT DISTINCT mp.club_id AS cid, c.club_name AS cname
        FROM match_players mp
        LEFT JOIN clubs c ON c.club_id = mp.club_id
        WHERE mp.club_id <> '{L.RAINHAM_CLUB_ID}' AND mp.club_id <> ''
    """).fetchall()
    n_pages = 0
    for r in rows:
        cid = r["cid"]
        cname = r["cname"] or cid
        players = cur.execute("""
            SELECT mp.player_id AS pid,
                   COUNT(DISTINCT mp.match_id) AS n_matches,
                   MAX(m.match_date) AS last_seen
            FROM match_players mp
            JOIN matches m ON m.match_id = mp.match_id
            WHERE mp.club_id = ? AND mp.player_id IS NOT NULL
            GROUP BY mp.player_id
            ORDER BY n_matches DESC
        """, (cid,)).fetchall()

        # Pre-fetch the set of match_ids each player has, to know if any
        # of them have video evidence.
        all_pids = [int(p["pid"]) for p in players]
        video_by_pid: dict[int, bool] = {}
        if all_pids:
            qmarks = ",".join("?" * len(all_pids))
            mp_rows = cur.execute(f"""
                SELECT player_id, match_id
                FROM match_players
                WHERE player_id IN ({qmarks})
            """, all_pids).fetchall()
            for mr in mp_rows:
                if int(mr["match_id"]) in videos:
                    video_by_pid[int(mr["player_id"])] = True

        body = [
            f"<h1>{escape(cname)}</h1>",
            f'<p class="lead">{len(players)} distinct players on '
            f'<code>{escape(cid)}</code>. Click a name to view metadata '
            'and submit additions.</p>',
            '<table><thead><tr>'
            '<th>Player</th>'
            '<th>Status</th>'
            '<th class="num">Apps</th>'
            '<th>Last seen</th>'
            '<th>Video</th>'
            '</tr></thead><tbody>'
        ]
        for p in players:
            pid = int(p["pid"])
            name = (aliases.get(pid) or [f"#{pid}"])[0]
            base = L.metadata_status((all_meta.get(pid) or {}).get("metadata"))
            status = overlay_status(pid, base, pending)
            video_pill = ('<span class="tag yes">🎬</span>'
                          if video_by_pid.get(pid)
                          else '<span class="muted">—</span>')
            body.append(
                "<tr>"
                f'<td><a href="../player.html?id={pid}">{escape(name)}</a></td>'
                f"<td>{L.status_pill(status)}</td>"
                f'<td class="num">{p["n_matches"]}</td>'
                f'<td>{escape(p["last_seen"] or "")}</td>'
                f"<td>{video_pill}</td>"
                "</tr>"
            )
        body.append("</tbody></table>")

        out = L.APP_DIR / "metadata" / "club" / f"{cid}.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(L.page(cname, "".join(body),
            css_href="../../static/app.css",
            crumbs=[
                ("Data repository", "../../index.html"),
                ("Opposition clubs", "../clubs.html"),
                (cname, ""),
            ]))
        n_pages += 1
    return n_pages


def build_player_data(conn, all_meta, aliases, clubs_by_player,
                      videos: set[int]) -> int:
    """
    Single bundled `app/data/players.json` keyed by player_id.

    Why bundled? 14k tiny per-player files would burn ~56 MB in 4K
    filesystem blocks and be a chore to navigate in git. The bundled
    file is ~3 MB on disk, compresses to ~700 KB over HTTP, and is
    cached by the browser after the first hit.
    """
    cur = conn.cursor()
    pids = sorted({
        int(x[0]) for x in cur.execute(f"""
            SELECT DISTINCT player_id FROM match_players
            WHERE club_id <> '{L.RAINHAM_CLUB_ID}' AND club_id <> ''
                  AND player_id IS NOT NULL
        """).fetchall()
    })
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
    """One static `player.html` shell + a separate player.js with the form logic."""
    L.APP_DIR.joinpath("metadata").mkdir(parents=True, exist_ok=True)

    # Template. The page reads ?id= from the URL, fetches the JSON,
    # then renders into the marked div. Crumbs are static; the title
    # is rewritten by JS.
    body = (
        '<h1 id="player-name">Loading…</h1>'
        '<p class="lead" id="player-status"></p>'
        '<div id="meta-box"></div>'
        '<div id="video-box"></div>'
        '<div id="form-box"></div>'
    )
    page_html = L.page("Player metadata", body,
        css_href="../static/app.css",
        crumbs=[
            ("Data repository", "../index.html"),
            ("Opposition clubs", "clubs.html"),
            ("Player", ""),
        ],
        extra_body='<script src="../static/player.js"></script>',
    )
    (L.APP_DIR / "metadata" / "player.html").write_text(page_html)

    # The JS lives next to app.css under app/static/
    js = PLAYER_JS_TEMPLATE
    js = js.replace("__SUBMIT_EMAIL__", json.dumps(SUBMIT_EMAIL))
    js = js.replace("__SUBMIT_WHATSAPP__", json.dumps(SUBMIT_WHATSAPP))
    (L.APP_DIR / "static" / "player.js").write_text(js)


PLAYER_JS_TEMPLATE = r"""
// Player metadata page — runtime renderer + submission form.
//
// Reads ?id=<player_id> from the URL, fetches
// ../data/players/<id>.json, and builds the page. The submit form has
// NO backend; it composes a structured plain-text body and opens either
// a mailto: or wa.me/ link.
(function() {
  const SUBMIT_EMAIL = __SUBMIT_EMAIL__;
  const SUBMIT_WHATSAPP = __SUBMIT_WHATSAPP__;

  const params = new URLSearchParams(location.search);
  const pid = parseInt(params.get('id'), 10);
  if (!pid) {
    document.getElementById('player-name').textContent = 'Missing ?id=';
    return;
  }

  // One bundled JSON for every player; cached by the browser after
  // the first hit. Look the player up by id once it lands.
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
    nameEl.innerHTML = '';
    nameEl.appendChild(document.createTextNode(p.name + ' '));
    const sub = el('span', {class: 'muted', style: 'font-size:14px'});
    sub.textContent = '#' + p.player_id;
    nameEl.appendChild(sub);

    const stEl = document.getElementById('player-status');
    stEl.innerHTML = '';
    stEl.appendChild(document.createTextNode('Status: '));
    stEl.appendChild(statusPill(statusOf(p.metadata)));
    if (p.seen_clubs && p.seen_clubs.length) {
      stEl.appendChild(document.createTextNode(' · clubs seen: '));
      p.seen_clubs.forEach((c, i) => {
        if (i) stEl.appendChild(document.createTextNode(', '));
        const a = el('a', {href: 'club/' + c.club_id + '.html'});
        a.textContent = c.club_name;
        stEl.appendChild(a);
      });
    }

    // Current metadata
    const mb = document.getElementById('meta-box');
    mb.appendChild(el('h2', null, ['Current metadata']));
    const m = p.metadata || {};
    const fields = ['batting_hand','bowling_type','pace_type','spin_type',
                    'bowling_arm','angle_to_rhb'];
    const populated = fields.filter(k => m[k]);
    if (!populated.length) {
      mb.appendChild(el('p', {class: 'note'}, ['No metadata captured yet.']));
    } else {
      const row = el('div', {class: 'row', style: 'flex-wrap:wrap'});
      populated.forEach(k => {
        const cell = el('div', null, [
          el('label', null, [FIELD_LABELS[k]]),
          el('div', null, [String(m[k])]),
        ]);
        row.appendChild(cell);
      });
      mb.appendChild(row);
      if (m.notes) mb.appendChild(el('p', {class: 'note'}, [m.notes]));
    }

    // Video evidence
    const vb = document.getElementById('video-box');
    vb.appendChild(el('h2', null, ['Video evidence']));
    const withVideos = (p.evidence_matches || []);
    if (!withVideos.length) {
      vb.appendChild(el('p', {class: 'note'}, [
        'No videos recorded for matches involving this player yet. ' +
        '(Video links live in data/metadata/videos/<match_id>.json.)'
      ]));
    } else {
      const tbl = el('table');
      tbl.innerHTML =
        '<thead><tr><th>Date</th><th>Opp</th><th>Videos</th></tr></thead>';
      const tb = el('tbody');
      withVideos.forEach(mt => {
        const tr = el('tr');
        tr.appendChild(el('td', null, [mt.date]));
        tr.appendChild(el('td', null, [mt.opp]));
        const td = el('td');
        mt.videos.forEach((v, i) => {
          if (i) td.appendChild(document.createTextNode(' · '));
          const a = el('a', {href: v.url, target: '_blank'});
          a.textContent = v.label || 'watch';
          td.appendChild(a);
        });
        tr.appendChild(td);
        tb.appendChild(tr);
      });
      tbl.appendChild(tb);
      vb.appendChild(tbl);
    }

    // Submit form
    renderForm(p);
  }

  function renderForm(p) {
    const fb = document.getElementById('form-box');
    fb.appendChild(el('h2', null, ['Submit metadata']));
    fb.appendChild(el('p', {class: 'note'}, [
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
      const r = el('div', {class: 'row'});
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
                               multiple: 'multiple', size: '5'});
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
    form.appendChild(el('label', null,
      ['Evidence matches (Cmd/Ctrl-click for multi)']));
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

    const btnRow = el('div', {style: 'margin-top:14px'});
    const mailBtn = el('a', {href: '#', id: 'sendMail', class: 'btn'},
                       ['Send via email']);
    const waBtn   = el('a', {href: '#', id: 'sendWA', class: 'btn secondary'},
                       ['Send via WhatsApp']);
    btnRow.appendChild(mailBtn);
    btnRow.appendChild(waBtn);
    form.appendChild(btnRow);
    form.appendChild(el('p', {class: 'note'}, [
      'Receiving address: ',
      el('code', {id: 'addrPreview'}),
    ]));

    document.getElementById('form-box').appendChild(form);

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

    print("Building app/metadata/clubs.html ...", flush=True)
    n = build_clubs_index(conn, all_meta, aliases, clubs_by_player, pending)
    print(f"  {n} clubs listed", flush=True)

    print("Building app/metadata/club/<club_id>.html ...", flush=True)
    n = build_club_pages(conn, all_meta, aliases, clubs_by_player,
                         videos, pending)
    print(f"  {n} club pages", flush=True)

    print("Building app/data/players/<player_id>.json ...", flush=True)
    n = build_player_data(conn, all_meta, aliases, clubs_by_player, videos)
    print(f"  {n} player JSON files", flush=True)

    print("Writing app/metadata/player.html template + app/static/player.js ...",
          flush=True)
    write_player_template_and_js()

    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(build())
