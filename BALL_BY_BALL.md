# Ball-by-ball data

## Is it on the public Play-Cricket API?

**No.** `match_detail.json` (used by `fetch.py`) stops at scorecard
granularity — innings totals, batting/bowling cards, fall-of-wickets, but
**no per-ball stream**. The `balls` field on a batter row is the count of
deliveries faced, not the deliveries themselves.

There's no `ball_by_ball` endpoint anywhere in `https://play-cricket.com/api/v2/`.

## How the public match-centre widget gets it

Every Play-Cricket match page (`/website/results/<match_id>`) embeds an
ECB-hosted React widget called the **match centre** (loaded from
`d1a6rr1txsj63h.cloudfront.net/match-centre/<v>/main.js`). That widget is
a thin client over **ResultsVault** — the system PCS / PCS Pro upload to.
ResultsVault holds the ball-level data; Play-Cricket is essentially the
public face of it.

The widget makes three calls per match:

```
1.  Map the public match_id to the internal RV match_id:
    GET https://api.resultsvault.co.uk/rv/mappings/4/12/<match_id>/?sportid=1
        -> {"object_id1": <rv_match_id>, ...}

2.  Pull the detailed match (innings list, result_ids per team):
    GET https://api.resultsvault.co.uk/rv/130000/matches/<rv_match_id>/?strmflg=3
        -> {MatchTeams: [
              {result_id, Innings: [{innings_id, innings_number, innings_order, ...}]}
            ], was_live_scored, ...}

3.  Pull every ball for one innings:
    GET https://api.resultsvault.co.uk/rv/130000/matches/<rv_match_id>/
            ?action=getballs&sportid=1
            &resultid=<result_id>&inningsnumber=<innings_number>
        -> [<ball>, <ball>, ...]
```

Constants (from the public JS bundle):
- `mappings/4/12/...`  — `4` = mapping instance, `12` = match object type
- `130000`             — ECB master entity id
- `apiid=1003`         — public api id, on every request

## Auth

Every RV request needs:
- `apiid=1003` query param
- `X-IAS-API-REQUEST: <base64-DES-encrypted-timestamp>` header

The shared secret + the obfuscated DES routine ship in the JS bundle that
every visitor's browser downloads. The token is a base64'd DES
encryption of `Math.round(now/1000 - 60).toString()`, with the cache
window pegged at 30 minutes.

To stay correct against upstream changes, `fetch_balls.py` doesn't
re-implement this — it carves the original `ce()` function out of the
public bundle on first run, writes it to `_rv_token.js`, and shells
out to `node` to compute fresh tokens. Re-run with `--probe` to
re-bootstrap if the bundle hash changes.

## Sample ball

```json
{
  "ball_no": 7,         // sequence within the innings (counts wides/NBs)
  "ball_no_disp": 6,    // legal-balls-only display number
  "over_no": 19,
  "innings_number": 1,
  "result_id": 26058729,
  "batter_id": 11590850,
  "batter_id_ns": 11483353,
  "bowler_id": 11189280,
  "runs_bat": 4,
  "runs_extra": 0,
  "extras_type": null,  // 1=NB, 2=Wide, 3=Bye, 4=LB, 5=NB+Bye, 6=NB+LB
  "dismissed_batter_id": null,
  "dismissed_batter_inst_num": 1,
  "s_desc": " 4",
  "l_desc": " D Noller to G McWilliams: 4 runs",
  "ball_spot_x": null,  // pitch map; populated only when scored on PCS Pro
  "ball_spot_y": null,
  "shot_angle": null,   // wagon-wheel angle; ditto
  "shot_length": null,
  "highlight_event_id": null,
  "match_highlight_events": []
}
```

`ball_spot_*`, `shot_angle`, `shot_length` and `highlight_event_id` are
the PCS Pro fields. They're sparsely populated for the recreational
matches in this cache — most clubs use the free PCS app, which doesn't
capture pitch maps.

## Coverage in the current cache

Sampled the 30 most-recent **played** Rainham matches (Mar–May 2026):

- 24 / 30 returned non-empty ball streams (≈ 80 % digital-scoring rate)
- 6 / 30 returned empty arrays — the match was scored on paper

`was_live_scored` is **not** a reliable predictor; matches scored on
PCS but not live-streamed still expose the full ball stream after the
fact. The reliable signal is just "does `getballs` return ≥ 1 row".

## How to use it

```bash
# One-off: cache a single match
python3 fetch_balls.py --match-id 7674154

# Bulk: every Rainham fixture in 2026
python3 fetch_balls.py --site-id 5251 --season 2026 --workers 6

# Same, but also pick up matches we've already cached (e.g. corrupt re-fetch)
python3 fetch_balls.py --site-id 5251 --season 2026 --force

# Sanity-check auth + connectivity (no writes)
python3 fetch_balls.py --probe
```

Cache layout:

```
data/raw/rv_match/<match_id>.json
data/raw/balls/<match_id>/<innings_order>.json
```

Both are idempotent — re-runs skip files that already exist unless you
pass `--force`.

## What this unlocks

Things you can't currently answer from the scorecard cache but can with
the ball stream:

- Phase analysis: powerplay / middle / death economy + scoring rate.
- Bowler match-ups: how player X scores against off-spin vs. seam.
- Over-by-over momentum / required-rate charts (worm graph).
- Dot-ball %, boundary %, strike rotation patterns.
- Batter intent / aggression by ball number in innings.
- Partnership building (RPB, dot-ball %, who's farming strike).
- Auto-generated highlight reels by fielder / over.

## Next step (when wiring into the DB)

`fetch_balls.py` only writes raw JSON. To make this queryable:

1. Add a `balls` table to `build_db.py`:

   ```sql
   CREATE TABLE balls (
       match_id          INTEGER,
       innings_order     INTEGER,
       innings_number    INTEGER,
       over_no           INTEGER,
       ball_no           INTEGER,
       ball_no_disp      INTEGER,
       batter_id         INTEGER,
       non_striker_id    INTEGER,
       bowler_id         INTEGER,
       runs_bat          INTEGER,
       runs_extra        INTEGER,
       extras_type       INTEGER,
       dismissed_batter_id INTEGER,
       s_desc            TEXT,
       l_desc            TEXT,
       PRIMARY KEY (match_id, innings_order, ball_no)
   );
   CREATE INDEX idx_balls_bowler  ON balls(bowler_id, match_id);
   CREATE INDEX idx_balls_batter  ON balls(batter_id, match_id);
   CREATE INDEX idx_balls_match   ON balls(match_id, innings_order, over_no);
   ```

2. In `build_db.py`'s loader, walk `data/raw/balls/<match_id>/*.json`
   and bulk-insert.

3. Validation: cross-check the per-ball runs total against
   `innings.runs` from the existing scorecard load — they should match
   modulo penalty runs.

## Risk / etiquette

- The endpoints aren't documented as public, but the auth secret +
  `apiid` are baked into a CDN-hosted JS bundle every visitor downloads,
  and there's no rate-limit header in evidence beyond the standard 429
  back-off.
- Be conservative: workers ≤ 8, exponential back-off on 429/5xx already
  in `http_get`, and stick to the cache so re-runs don't re-hit upstream.
- Don't paste `apiSharedSecret` into a new file unnecessarily — the
  helper script grabs it from the live bundle so it stays in sync with
  upstream rotations.
