#!/usr/bin/env python3
"""
Generate the static data-repository page at `app/index.html`.

One row per **played Rainham fixture** in the last 10 seasons, with:

  * date, opposition, format, score
  * BBB ✓/✗  (whether ball-by-ball is loaded for this match_id)
  * opp batting metadata coverage %  (only when BBB is loaded)
  * opp bowling metadata coverage %  (only when BBB is loaded)

Coverage = fraction of opposition legal-balls whose batter / bowler has
a player metadata file with status `complete`. Numbers will be 0 % until
`data/metadata/players/` is populated; that's expected.

Reads:  data/rainham.db, data/metadata/players/
Writes: app/index.html
"""

from __future__ import annotations

import datetime as dt
import sys
from html import escape

import _app_lib as L


PC_MATCH_URL = "https://rainhamcc.play-cricket.com/website/results/{mid}"
SEASONS_BACK = 10


def opposition_for(row) -> tuple[str, str]:
    """Returns (opp_club_name, opp_short_label) for a Rainham fixture."""
    if row["home_club_id"] == L.RAINHAM_CLUB_ID:
        return row["away_club_name"] or "?", row["away_team_name"] or ""
    return row["home_club_name"] or "?", row["home_team_name"] or ""


def collect_rows(conn, since_season: int) -> list[dict]:
    cur = conn.cursor()
    matches = cur.execute(f"""
        SELECT m.*, b.n_balls, b.n_legal
        FROM matches m
        LEFT JOIN match_bbb b ON b.match_id = m.match_id
        WHERE (m.home_club_id = '{L.RAINHAM_CLUB_ID}'
            OR m.away_club_id = '{L.RAINHAM_CLUB_ID}')
          AND m.result <> ''
          AND m.season >= ?
        ORDER BY m.match_date DESC
    """, (since_season,)).fetchall()

    # Filter to "played" by parsing the date and dropping future fixtures.
    # `result` is non-empty only after the match has been completed; the
    # WHERE above usually suffices, but belt-and-braces.
    today = dt.date.today()
    today_yyyymmdd = today.strftime("%Y%m%d")

    covered = L.covered_player_ids()

    out: list[dict] = []
    for m in matches:
        ymd = L.date_yyyymmdd(m["match_date"] or "")
        if ymd and ymd > today_yyyymmdd:
            continue
        opp_club, opp_team = opposition_for(m)
        has_bbb = (m["n_balls"] or 0) > 0

        bat_cov = bowl_cov = None
        if has_bbb:
            row = cur.execute("""
                SELECT
                  SUM(CASE WHEN b.is_legal_ball=1 THEN 1 ELSE 0 END) AS legal,
                  SUM(CASE WHEN b.is_legal_ball=1 AND b.batter_id IN ({covered})
                           THEN 1 ELSE 0 END) AS covered
                FROM balls b
                WHERE b.match_id = ?
                  AND b.team_batting_club_id <> '{rcc}'
            """.format(
                covered=",".join(str(p) for p in covered) or "NULL",
                rcc=L.RAINHAM_CLUB_ID,
            ), (m["match_id"],)).fetchone()
            if row and (row["legal"] or 0) > 0:
                bat_cov = (row["covered"] or 0) / row["legal"]

            row = cur.execute("""
                SELECT
                  SUM(CASE WHEN b.is_legal_ball=1 THEN 1 ELSE 0 END) AS legal,
                  SUM(CASE WHEN b.is_legal_ball=1 AND b.bowler_id IN ({covered})
                           THEN 1 ELSE 0 END) AS covered
                FROM balls b
                WHERE b.match_id = ?
                  AND b.team_bowling_club_id <> '{rcc}'
            """.format(
                covered=",".join(str(p) for p in covered) or "NULL",
                rcc=L.RAINHAM_CLUB_ID,
            ), (m["match_id"],)).fetchone()
            if row and (row["legal"] or 0) > 0:
                bowl_cov = (row["covered"] or 0) / row["legal"]

        out.append({
            "match_id": m["match_id"],
            "date": m["match_date"],
            "ymd": ymd,
            "season": m["season"],
            "opp_club_name": opp_club,
            "opp_team_name": opp_team,
            "format": m["competition_type"] or m["match_type"] or "",
            "rainham_team": (m["home_team_name"]
                             if m["home_club_id"] == L.RAINHAM_CLUB_ID
                             else m["away_team_name"]) or "",
            "result_desc": m["result_description"] or "",
            "has_bbb": has_bbb,
            "bat_cov": bat_cov,
            "bowl_cov": bowl_cov,
        })
    return out


def render_summary(rows: list[dict]) -> str:
    n = len(rows)
    n_bbb = sum(1 for r in rows if r["has_bbb"])
    by_season: dict[int, dict[str, int]] = {}
    for r in rows:
        s = by_season.setdefault(r["season"] or 0, {"n": 0, "bbb": 0})
        s["n"] += 1
        if r["has_bbb"]:
            s["bbb"] += 1

    season_chips = "".join(
        f'<span class="tag">{s}: {d["bbb"]}/{d["n"]}</span> '
        for s, d in sorted(by_season.items(), reverse=True)
    )
    return (
        f"<h1>Rainham CC — data repository</h1>"
        f'<p class="lead">Every played Rainham fixture in the cache from '
        f'the last {SEASONS_BACK} seasons. <strong>{n_bbb}/{n}</strong> '
        f'have ball-by-ball data loaded ('
        f'{n_bbb*100//max(n,1)}%).</p>'
        f'<p class="lead">Coverage % counts the fraction of opposition '
        f"legal balls whose batter/bowler has a <em>complete</em> metadata "
        f"file under <code>data/metadata/players/</code>. "
        f"Both columns sit at 0 % until that queue is worked through.</p>"
        f'<div style="margin:8px 0 24px">{season_chips}</div>'
    )


def render_table(rows: list[dict]) -> str:
    head = (
        "<thead><tr>"
        "<th>Date</th>"
        "<th>Rainham team</th>"
        "<th>Opposition</th>"
        "<th>Fmt</th>"
        "<th>Result</th>"
        "<th>BBB</th>"
        '<th class="num">Bat cov</th>'
        '<th class="num">Bowl cov</th>'
        "</tr></thead>"
    )
    body_rows = []
    for r in rows:
        bbb_pill = (
            '<span class="tag yes">✓</span>' if r["has_bbb"]
            else '<span class="tag no">✗</span>'
        )
        link = PC_MATCH_URL.format(mid=r["match_id"])
        body_rows.append(
            "<tr>"
            f'<td><a href="{escape(link)}" target="_blank">{escape(r["date"])}</a></td>'
            f"<td>{escape(r['rainham_team'])}</td>"
            f"<td>{escape(r['opp_club_name'])}<br>"
            f'<span class="muted" style="font-size:11px">{escape(r["opp_team_name"])}</span></td>'
            f"<td>{escape(r['format'])}</td>"
            f"<td>{escape(r['result_desc'])}</td>"
            f"<td>{bbb_pill}</td>"
            f'<td class="num">{L.coverage_bar(r["bat_cov"])}</td>'
            f'<td class="num">{L.coverage_bar(r["bowl_cov"])}</td>'
            "</tr>"
        )
    return "<table>" + head + "<tbody>" + "".join(body_rows) + "</tbody></table>"


def build() -> int:
    conn = L.open_db()
    L.write_static_assets()
    today = dt.date.today()
    since = today.year - SEASONS_BACK + 1
    rows = collect_rows(conn, since_season=since)
    if not rows:
        print("No matches found in window — abort.", file=sys.stderr)
        return 1

    body = render_summary(rows) + render_table(rows) + (
        '<p class="lead" style="margin-top:24px">'
        f'Rebuilt at {dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")}. '
        'Browse <a href="metadata/clubs.html">opposition clubs</a> to '
        "submit player metadata.</p>"
    )

    out = L.APP_DIR / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(L.page("Rainham CC — data repository", body,
                          crumbs=[("Data repository", "")]))
    print(f"Wrote {out}  ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(build())
