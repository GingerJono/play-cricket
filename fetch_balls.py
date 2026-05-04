#!/usr/bin/env python3
"""
Fetch ball-by-ball data for digitally-scored Play-Cricket matches.

The official Play-Cricket REST API (used by `fetch.py`) does NOT expose
ball-level data — `match_detail.json` stops at scorecard granularity. The
Play-Cricket *match centre* widget on each match page, however, is a thin
React client over ResultsVault (api.resultsvault.co.uk), which DOES hold
the ball-by-ball stream for every match scored with PCS / PCS Pro.

This script talks to that backend directly:

  1.  https://api.resultsvault.co.uk/rv/mappings/4/12/<match_id>/?sportid=1
        -> {object_id1: <rv_match_id>}                     (rv_match cache)
  2.  https://api.resultsvault.co.uk/rv/130000/matches/<rv_match_id>/?strmflg=3
        -> per-team innings list with `result_id` + `innings_number`
        -> also exposes `was_live_scored`                  (rv_match cache)
  3.  https://api.resultsvault.co.uk/rv/130000/matches/<rv_match_id>/
            ?action=getballs&sportid=1
            &resultid=<result_id>&inningsnumber=<n>
        -> JSON list of every ball (one row per delivery, plus auto-text
           commentary in `s_desc` / `l_desc`).             (balls cache)

Auth: the API requires an `X-IAS-API-REQUEST` header (a base64'd
DES-encrypted timestamp) plus the public `apiid=1003` query param. The
shared secret + the obfuscated token routine are shipped to every
browser visiting a match page; we re-use the upstream JS verbatim by
shelling out to `node` so we don't have to reimplement DES in Python.

Cache layout (all idempotent, all committed):
  data/raw/rv_match/<play_cricket_match_id>.json
      { "rv_match_id": int,
        "external_match_id": int,
        "was_live_scored": bool,
        "innings": [
          {"team_name": ..., "is_home": bool,
           "result_id": int, "innings_number": int,
           "innings_id": int, "innings_order": int,
           "runs": int, "wickets": int, "overs_bowled": float}
        ]
      }
  data/raw/balls/<play_cricket_match_id>/<innings_order>.json
      [<ball>, <ball>, ...]   # exactly the upstream payload, untouched

Usage:
  python3 fetch_balls.py --match-id 7674154
  python3 fetch_balls.py --site-id 5251 --season 2026
  python3 fetch_balls.py --site-id 5251 --season 2026 --workers 8
  python3 fetch_balls.py --match-id 7674154 --force
  python3 fetch_balls.py --probe        # just test that auth works
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parent
RAW_DIR = ROOT / "data" / "raw"
MATCHES_DIR = RAW_DIR / "matches"
RV_MATCH_DIR = RAW_DIR / "rv_match"
BALLS_DIR = RAW_DIR / "balls"
NV_MATCH_DIR = RAW_DIR / "nv_match"   # NV Play scorecard (fallback when RV is empty)

# Auto-generated; regenerated whenever the upstream JS bundle hash changes.
TOKEN_JS = ROOT / "_rv_token.js"

PC_MATCH_PAGE = "https://rainhamcc.play-cricket.com/website/results/{match_id}"
RV_API = "https://api.resultsvault.co.uk/rv"
RV_MAPPING_INSTANCE = 4         # constant for ECB/Play-Cricket
RV_OBJECT_TYPE_MATCH = 12       # constant for "match" objects
RV_MASTER_ENTITY_ID = 130000    # ECB top-level org
RV_APIID = 1003                 # public, ships in the JS bundle

# NV Play (separate scoring product). Match-centre/RV does NOT have ball
# data for matches scored on NV Play; we have to hit a different backend.
# All Play-Cricket pages embed the same `<nvplay customer-id=...>` widget,
# so the customer id is fixed.
NV_CUSTOMER_ID = "5e401d65-10ec-4a28-a0f6-1c084ce30445"
NV_AUTH_URL = "https://w-auth.nvplay.com/api/widgetauthorisation/{cid}"
# After auth, the widget calls `{ApiBaseUrl}/api/scorecard/<match_id>` —
# ApiBaseUrl is fixed per Play-Cricket so we can cache it after first call.
_nv_api_base: str | None = None

USER_AGENT = "rainham-cc-stats/1.0 (+https://github.com/gingerjono/rcc-2020-site)"


# ---------- HTTP helpers -----------------------------------------------------

def http_get(url: str, headers: dict | None = None, attempts: int = 5) -> bytes:
    backoff = 1.0
    last_err: Exception | None = None
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    for _ in range(attempts):
        try:
            req = Request(url, headers=hdrs)
            with urlopen(req, timeout=60) as r:
                return r.read()
        except HTTPError as e:
            last_err = e
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(backoff); backoff *= 2; continue
            raise
        except (URLError, TimeoutError, ConnectionError) as e:
            last_err = e
            time.sleep(backoff); backoff *= 2
    raise RuntimeError(f"Failed after {attempts} attempts: {url} ({last_err})")


def http_get_json(url: str, headers: dict | None = None) -> object:
    return json.loads(http_get(url, headers=headers))


# ---------- Auth token (re-uses upstream JS verbatim) ------------------------

def _bootstrap_token_js() -> Path:
    """
    Pull the upstream match-centre bundle, carve out `function ce(...)`,
    write it to TOKEN_JS so node can run it. Idempotent — only runs if
    TOKEN_JS is missing.
    """
    if TOKEN_JS.exists():
        return TOKEN_JS
    if not shutil.which("node"):
        raise RuntimeError(
            "node is not on PATH but is needed to compute the ResultsVault "
            "auth token. Install Node 18+ or vendor a pure-Python DES port."
        )

    print("Bootstrapping _rv_token.js from upstream match-centre bundle...",
          flush=True)
    pc_html = http_get(
        "https://rainhamcc.play-cricket.com/website/results/7674154"
    ).decode("utf-8", "replace")
    m = re.search(r'src="(https://[^"]+/match-centre/[^"]+/main\.js)"', pc_html)
    if not m:
        raise RuntimeError("Could not find match-centre/main.js in PC page")
    main_js_url = m.group(1)
    main_js = http_get(main_js_url).decode("utf-8", "replace")
    m = re.search(r'(https://[^"]+/match-centre/[^"]+/main\.[a-f0-9]+\.chunk\.js)',
                  main_js)
    if not m:
        raise RuntimeError("Could not find main.<hash>.chunk.js in match-centre loader")
    bundle_url = m.group(1)
    bundle = http_get(bundle_url).decode("utf-8", "replace")

    i = bundle.find("function ce(")
    if i < 0:
        raise RuntimeError("Could not find ce() in match-centre bundle")
    depth = 0; started = False; end = i
    for j in range(i, len(bundle)):
        c = bundle[j]
        if c == "{": depth += 1; started = True
        elif c == "}":
            depth -= 1
            if started and depth == 0:
                end = j + 1; break
    ce_src = bundle[i:end]
    # Inline the literal secret + apiid so the helper has no upstream deps.
    m_secret = re.search(r'apiSharedSecret:"([0-9A-Fa-f]+)"', bundle)
    if not m_secret:
        raise RuntimeError("Could not find apiSharedSecret in bundle")
    secret = m_secret.group(1)
    ce_src = ce_src.replace("ne.apiSharedSecret", f'"{secret}"')

    TOKEN_JS.write_text(
        "// Auto-generated by fetch_balls.py from the public match-centre\n"
        f"// bundle ({bundle_url}). Re-run `python3 fetch_balls.py --probe`\n"
        "// to regenerate after upstream bumps.\n"
        "var oe, se = 0;\n"
        f"{ce_src}\n"
        "process.stdout.write(ce(true));\n"
    )
    return TOKEN_JS


def auth_token() -> str:
    _bootstrap_token_js()
    out = subprocess.check_output(["node", str(TOKEN_JS)], timeout=15)
    return out.decode("ascii").strip()


def auth_headers() -> dict:
    return {"X-IAS-API-REQUEST": auth_token()}


# ---------- Endpoint wrappers ------------------------------------------------

def fetch_rv_mapping(match_id: int, headers: dict) -> dict:
    url = (f"{RV_API}/mappings/{RV_MAPPING_INSTANCE}/{RV_OBJECT_TYPE_MATCH}/"
           f"{match_id}/?" + urlencode({"sportid": 1, "apiid": RV_APIID}))
    return http_get_json(url, headers=headers)


def fetch_rv_match(rv_match_id: int, headers: dict) -> dict:
    url = (f"{RV_API}/{RV_MASTER_ENTITY_ID}/matches/{rv_match_id}/?"
           + urlencode({"strmflg": 3, "apiid": RV_APIID}))
    return http_get_json(url, headers=headers)


def fetch_rv_balls(rv_match_id: int, result_id: int, innings_number: int,
                   headers: dict) -> list:
    url = (f"{RV_API}/{RV_MASTER_ENTITY_ID}/matches/{rv_match_id}/?"
           + urlencode({"action": "getballs", "sportid": 1,
                        "resultid": result_id, "inningsnumber": innings_number,
                        "apiid": RV_APIID}))
    return http_get_json(url, headers=headers)


# ---------- NV Play fallback -------------------------------------------------

def _nv_resolve_api_base() -> str:
    """First call hits widgetauthorisation to discover ApiBaseUrl; cached."""
    global _nv_api_base
    if _nv_api_base:
        return _nv_api_base
    auth = http_get_json(NV_AUTH_URL.format(cid=NV_CUSTOMER_ID))
    base = auth.get("ApiBaseUrl") or "https://w-api2.ecb.nvplay.net"
    _nv_api_base = base.rstrip("/")
    return _nv_api_base


def fetch_nv_scorecard(match_id: int) -> dict | None:
    """
    Fetch the NV Play scorecard for a match. Returns None on 404 / empty.

    Endpoint + params come from `widgets.nvplay.scorecard.js`:

      const url = apiBaseUrl + "/api/scorecard/" + matchId
                 + "?idType=" + matchIdType
                 + "&customerId=" + customerId
                 + (playerLinks ? "&playerids=true" : "")
                 + (loadFullData ? "&stats=true&commentary=true" : "")

    `commentary=true` is the magic switch — without it, each ball's
    `C` field comes back null. We need it to extract per-ball "X to Y"
    text instead of reconstructing striker/non-striker from scratch.
    """
    base = _nv_resolve_api_base()
    url = (f"{base}/api/scorecard/{match_id}?"
           + urlencode({
               "idType": "play-cricket",
               "customerId": NV_CUSTOMER_ID,
               "playerids": "true",
               "stats": "true",
               "commentary": "true",
           }))
    try:
        body = http_get(url, headers={
            "Referer": "https://rainhamcc.play-cricket.com/",
        })
    except HTTPError as e:
        if e.code == 404:
            return None
        raise
    if not body:
        return None
    try:
        return json.loads(body)
    except Exception:
        return None


def nv_innings_balls_count(scorecard: dict) -> int:
    """Sum of all balls across all innings — used as the 'is there data' check."""
    n = 0
    for inn in scorecard.get("Innings", []) or []:
        for over in inn.get("Overs", []) or []:
            n += len(over.get("Balls", []) or [])
    return n


# ---------- Per-match orchestration ------------------------------------------

def summarise_innings(rv_match: dict) -> list[dict]:
    out: list[dict] = []
    for team in rv_match.get("MatchTeams", []) or []:
        for inn in team.get("Innings", []) or []:
            out.append({
                "team_name":      team.get("team_name"),
                "club_name":      team.get("club_name"),
                "is_home":        team.get("is_home"),
                "result_id":      team.get("result_id"),
                "innings_id":     inn.get("innings_id"),
                "innings_number": inn.get("innings_number"),
                "innings_order":  inn.get("innings_order"),
                "runs":           inn.get("runs"),
                "wickets":        inn.get("wickets"),
                "overs_bowled":   inn.get("overs_bowled"),
            })
    out.sort(key=lambda d: d.get("innings_order") or 0)
    return out


def summarise_team_members(rv_match: dict) -> list[dict]:
    """
    Per-team rosters from RV's `MatchTeams[].TeamMembers[]`.

    These are the ROSTER-LEVEL records (one per player on the
    teamsheet), and they're the only place RV exposes its internal
    `player_id` (11M-range) alongside the parsed names. We use them at
    DB-load time to build an RV->PC player_id translation table —
    avoids the ambiguity of per-ball name parsing.
    """
    out: list[dict] = []
    for team in rv_match.get("MatchTeams", []) or []:
        for m in team.get("TeamMembers", []) or []:
            out.append({
                "team_name":   team.get("team_name"),
                "club_name":   team.get("club_name"),
                "is_home":     team.get("is_home"),
                "rv_player_id": m.get("player_id"),
                "f_name":      m.get("f_name"),
                "l_name":      m.get("l_name"),
                "player_name": m.get("player_name"),    # "Last, First"
                "player_name2": m.get("player_name2"),  # "First Last"
                "player_name3": m.get("player_name3"),  # "F Last"
                "sel_number":  m.get("sel_number"),
            })
    return out


def cache_match(match_id: int, force: bool, headers: dict) -> tuple[int, str]:
    """Returns (n_balls, status). status in {'cached','fetched','no-mapping','no-data'}."""
    rv_path = RV_MATCH_DIR / f"{match_id}.json"
    if rv_path.exists() and not force:
        meta = json.loads(rv_path.read_text())
    else:
        try:
            mapping = fetch_rv_mapping(match_id, headers)
        except HTTPError as e:
            return (0, f"map-{e.code}")
        rv_match_id = mapping.get("object_id1") or 0
        if not rv_match_id:
            return (0, "no-mapping")
        rv_match = fetch_rv_match(rv_match_id, headers)
        meta = {
            "rv_match_id":        rv_match_id,
            "external_match_id":  rv_match.get("external_match_id"),
            "was_live_scored":    bool(rv_match.get("was_live_scored")),
            "scores_updated":     rv_match.get("scores_updated"),
            "match_format_id":    rv_match.get("match_format_id"),
            "innings":            summarise_innings(rv_match),
            "team_members":       summarise_team_members(rv_match),
        }
        rv_path.parent.mkdir(parents=True, exist_ok=True)
        rv_path.write_text(json.dumps(meta, indent=2))

    balls_dir = BALLS_DIR / str(match_id)
    balls_dir.mkdir(parents=True, exist_ok=True)
    total_balls = 0
    fetched_any = False
    for inn in meta["innings"]:
        order = inn["innings_order"]
        result_id = inn["result_id"]
        inn_no = inn["innings_number"]
        if not (order and result_id and inn_no):
            continue
        out = balls_dir / f"{order}.json"
        if out.exists() and not force:
            try:
                total_balls += len(json.loads(out.read_text()))
            except Exception:
                pass
            continue
        balls = fetch_rv_balls(meta["rv_match_id"], result_id, inn_no, headers)
        if not isinstance(balls, list):
            balls = []
        out.write_text(json.dumps(balls))
        total_balls += len(balls)
        fetched_any = True

    if total_balls == 0:
        # ResultsVault has nothing — try NV Play, the other scoring backend
        # used by Play-Cricket (matches scored on PCS Pro Live / NV-streamed
        # games show up here even when `was_live_scored` is False on RV).
        nv_path = NV_MATCH_DIR / f"{match_id}.json"
        if nv_path.exists() and not force:
            try:
                nv = json.loads(nv_path.read_text())
            except Exception:
                nv = None
        else:
            nv = fetch_nv_scorecard(match_id)
            if nv is not None:
                nv_path.parent.mkdir(parents=True, exist_ok=True)
                nv_path.write_text(json.dumps(nv))
                fetched_any = True
        if nv:
            n_nv = nv_innings_balls_count(nv)
            if n_nv > 0:
                return (n_nv, "nv-fetched" if fetched_any else "nv-cached")
        return (0, "no-data")
    return (total_balls, "fetched" if fetched_any else "cached")


# ---------- CLI --------------------------------------------------------------

def collect_match_ids(site_id: int, seasons: list[int]) -> list[int]:
    ids: list[int] = []
    for s in seasons:
        path = MATCHES_DIR / str(site_id) / f"{s}.json"
        if not path.exists():
            continue
        d = json.loads(path.read_text())
        for m in d.get("matches", []):
            try:
                ids.append(int(m["id"]))
            except (KeyError, TypeError, ValueError):
                continue
    seen: set[int] = set()
    out: list[int] = []
    for i in ids:
        if i not in seen:
            seen.add(i); out.append(i)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--match-id", type=int, action="append", default=[],
                    help="Single play-cricket match_id (repeatable)")
    ap.add_argument("--site-id", type=int,
                    help="Pull every match in this club's matches/<site>/<season>.json")
    ap.add_argument("--season", type=int, action="append", default=[],
                    help="Restrict --site-id to this season (repeatable; default: all cached)")
    ap.add_argument("--force", action="store_true",
                    help="Re-fetch even if cached")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--probe", action="store_true",
                    help="Just verify auth + connectivity (then exit)")
    args = ap.parse_args()

    headers = auth_headers()

    if args.probe:
        # Smoke test: fetch the mapping for one known match.
        sample = 7674154
        m = fetch_rv_mapping(sample, headers)
        print(f"OK  apiid={RV_APIID}  match {sample} -> rv_match_id={m.get('object_id1')}")
        return 0

    targets: list[int] = list(args.match_id)
    if args.site_id:
        site_dir = MATCHES_DIR / str(args.site_id)
        if not site_dir.exists():
            print(f"  no cached seasons under {site_dir} — run fetch.py first",
                  file=sys.stderr)
            return 2
        seasons = args.season or sorted(
            int(p.stem) for p in site_dir.glob("*.json")
        )
        targets.extend(collect_match_ids(args.site_id, seasons))

    # Dedup, preserve order
    seen: set[int] = set()
    ordered: list[int] = []
    for t in targets:
        if t not in seen:
            seen.add(t); ordered.append(t)
    if not ordered:
        ap.error("no targets — pass --match-id and/or --site-id")

    print(f"Fetching ball-by-ball for {len(ordered)} match(es) "
          f"(workers={args.workers})", flush=True)

    counts = {"fetched": 0, "cached": 0, "no-mapping": 0, "no-data": 0}
    failures: list[tuple[int, str]] = []
    n_balls_total = 0

    def worker(mid: int) -> tuple[int, int, str]:
        try:
            # Each worker recomputes the auth token once; tokens last 30 min so
            # the recompute is cheap, and we want fresh tokens if we span >30min.
            n, status = cache_match(mid, args.force, headers)
            return (mid, n, status)
        except Exception as e:
            return (mid, 0, f"err:{e}")

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(worker, mid): mid for mid in ordered}
        done = 0
        for fut in as_completed(futs):
            mid, n, status = fut.result()
            done += 1
            if status.startswith("err") or status.startswith("map-"):
                failures.append((mid, status))
            else:
                counts[status] = counts.get(status, 0) + 1
                n_balls_total += n
            if done % 25 == 0 or done == len(ordered):
                print(f"  {done}/{len(ordered)}  "
                      + "  ".join(f"{k}={v}" for k, v in counts.items())
                      + f"  balls={n_balls_total}",
                      flush=True)

    if failures:
        print(f"\n{len(failures)} failures:", file=sys.stderr)
        for mid, why in failures[:20]:
            print(f"  {mid}: {why}", file=sys.stderr)
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
