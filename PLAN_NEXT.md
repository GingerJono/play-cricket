# Plan — visual rebuild + RCC player dashboard + BBB analytics

This is the iteration plan for the next chunk of work. Three threads,
to be done in order:

1. **Visual rebuild** of the existing metadata pages, applying the
   `frontend-design` skill (see `FRONTEND_DESIGN.md`) with mobile as
   the primary surface.
2. **RCC player-stats dashboard** — replicate the
   `rainhamcc.co.uk/viewplayer.php?playerid=…` content, but as a
   filter/slicer dashboard where every dimension is an axis you can
   crossfilter on.
3. **BBB-driven analytics** layered onto (2): innings phase buckets,
   per-player innings buckets, bowling spells, batting-hand splits,
   and a clear visual indicator of the **volume** of BBB data behind
   each stat.

Each phase is independently committable; nothing in N blocks N+1
from being designed.

---

## Phase 1 — Visual rebuild of `app/metadata/*`

### Scope

Affected pages:

- `app/metadata/clubs.html` (index of opposition clubs)
- `app/metadata/club/<club_id>.html` (per-club roster, ~49 pages)
- `app/metadata/player.html` (player template + form)
- `app/index.html` (data repo) — kept consistent with the new system

### Aesthetic direction

**Wisden-inspired editorial cricket**, leaning into the heritage of
the sport:

- **Palette**: cream parchment (`#f4ecd8`-ish base), deep cricket-ball
  red (`#8b1a1a`) as primary accent, forest pitch green (`#1f4e2c`)
  for secondary, oxblood ink for body text, soft tobacco-card sepia
  for shadows / dividers. Status pills lean into traditional
  almanack ink colours rather than web pastels.
- **Typography**:
  - Display: a strong serif with cricket-press feel — `"Frank Ruhl
    Libre"` or `"Crimson Pro"` (fallback `Georgia`). Heavy weight on
    h1/h2; tight leading, slight negative letter-spacing.
  - Body: `"Source Serif 4"` (fallback `Charter`, `Georgia`).
  - Numerals: `"JetBrains Mono"` with `font-feature-settings:
    "tnum","ss01"` so all stats column-align.
- **Texture & detail**: subtle paper grain (SVG noise overlay on the
  body, ~3% opacity), hairline rules between rows in the
  almanack style, small marginal ornaments (▲ / § / ❘) instead of
  emoji. Cards have a single 1 px hairline border, no drop shadow.
  The hero gets a deep-red horizontal rule at top + bottom (newsprint
  masthead pattern).
- **Mobile constraints**: keep `max-width: 540px`. Page chrome stays
  one column. Use the extra design budget on density and typographic
  hierarchy, not width.

### Functional invariants (must survive the rebuild)

Nothing in this list is allowed to regress:

- **Sort**: clubs page primary by BBB games desc, then fixtures desc.
  Roster page primary by balls bowled in BBB desc, then apps vs us.
- **BBB chips**: `🎯 N bbb` (clubs) and `🎯 N bbb balls` (roster) must
  remain visible — they justify the sort. Re-skin them but keep the
  signal.
- **Status pills**: complete / partial / not captured / needs review /
  conflicting — same five states, restyled.
- **Search box** on club pages — keep the same vanilla-JS filter.
- **Submit form** (mailto / wa.me) on player pages — keep behaviour,
  restyle.

### Implementation

- Edit `_app_lib.CSS` in-place. The two builders (`build_data_repo.py`,
  `build_metadata.py`) read the same constant, so one CSS rewrite
  re-skins the whole site. Keep the class taxonomy (`.hero`, `.card`,
  `.row-link`, `.tag.complete`, etc.) so the Python doesn't have to
  change much.
- Web fonts: load via Bunny Fonts CDN (`fonts.bunny.net`) so we don't
  hit Google Analytics. Single `<link>` with all weights subset to
  Latin.
- Add a tiny `noise.svg` under `app/static/` for the paper-grain
  background.
- Keep the page emit functions (`hero()`, `page()`) signatures stable;
  only their CSS shifts.
- Run both builders, sanity-check the output in `app/`, eyeball one
  representative page for each surface (clubs / club / player / data
  repo).

### Acceptance

- Rendered pages look unmistakably "almanack/editorial cricket" rather
  than the current generic light theme.
- All sorts and chips behave as before.
- Page weight change ≤ +60 KB (fonts + CSS + noise SVG).
- Lighthouse mobile (or eyeball) contrast: body text ≥ 7:1, secondary
  text ≥ 4.5:1.

---

## Phase 2 — RCC player-stats dashboard

### Scope

Build `app/rcc/player.html?id=<pid>` (single template) plus
`app/rcc/index.html` (list of Rainham players to choose from). Inspired
by the layout of
`https://web.archive.org/web/20240530025543/https://rainhamcc.co.uk/viewplayer.php?playerid=20972`
(career batting / bowling summary, season splits, best performances,
match list), but rebuilt as a **dashboard with slicers** rather than a
static profile.

### Slicer dimensions (every one of these is a filter)

The page is one big AND across all active slicers; multi-select within
a slicer is OR.

Slicer surfaces, in order of priority:

1. **Season** — `2017` … `2026`. Default: all.
2. **Home / Away** — toggle pair.
3. **Result** — Won / Lost / Drawn / Tie / Abandoned.
4. **Competition** — League / Cup (split out non-T20 cup competitions
   if needed).
5. **Opposition club** — typeahead, multi-select.
6. **Format** — Limited Overs / Declaration (timed). Inferred from the
   universe filter, only relevant for completeness.
7. **Batting position** — chips 1, 2, 3, 4, 5, 6, 7+, "did not bat".
   Filters innings stats; ignored for bowling stats.
8. **Bat 1st / Bat 2nd** — order in which Rainham batted.
9. **Toss** — Won / Lost (already in `matches.toss_won_by`).

BBB-only slicers (light up only when the player has BBB coverage; greyed
out otherwise):

10. **Bowler type vs me** (batting view) — pace / spin / unknown,
    derived from opposition-bowler metadata.
11. **Bowler arm vs me** (batting view) — left / right / unknown.
12. **Pace flavour** — fast / medium / slow.
13. **Spin flavour** — finger / wrist.
14. **Batter type I bowled to** (bowling view) — RHB / LHB / unknown.
15. **Innings phase** — overs 0–10, 11–20, 21–30, 31–40, 41–50.
16. **Player innings phase** (batting view) — balls 0–10, 11–20,
    21–50, 51–100.
17. **Spell** (bowling view) — 1st spell / 2nd+ spells.

A slicer chip shows the count of matching rows (or balls, depending on
what's downstream) — so the user always knows "this filter combo is
still backed by N balls / M innings".

### Stats panels (each respects the active slicer combo)

Card grid, mobile-first single column. Each card shows:

- A headline metric (avg, SR, econ, wickets, etc.)
- A volume sub-label: `"N balls · X% of universe"` or
  `"M innings"` so the user can never read a stat without seeing how
  thin the slice is.

Panels:

A. **Career snapshot** — innings, runs, HS, avg, SR, 50s, 100s, ducks.
B. **Bowling snapshot** — overs, maidens, runs, wickets, avg, econ,
   SR, BBI.
C. **Per-season splits** — small line/bar chart (CSS-only) for runs &
   wickets per season. Honors slicers.
D. **Innings list** — every innings the slicers admit, with pos,
   runs, balls, SR, how-out, opp, date, link to the match. Bowling
   list mirrors this.
E. **Best performances** — top 5 batting innings + top 5 bowling
   spells, by the active filter combo.
F. **Phase splits** (BBB-only) — see Phase 3.
G. **Spell breakdown** (BBB-only) — see Phase 3.
H. **Vs LHB / RHB** (BBB-only, bowling view) — see Phase 3.
I. **Bowler-type splits** (BBB-only, batting view) — vs pace / spin /
   left-arm / etc.

### Data plumbing

- New builder: `build_rcc_dashboard.py` (sibling of
  `build_data_repo.py`). Reuses `_app_lib`.
- For each Rainham player who appears on a 1st-XI universe roster in
  the last 10 seasons, write `app/data/rcc/<player_id>.json`:
  - `meta`: name, dob if known, role.
  - `matches`: every relevant match they appeared in, with
    `match_id`, `match_date`, `season`, `home_away`, `result`,
    `competition`, `opp_club_id`, `opp_club_name`, `bat_first`
    (bool), `toss_won` (bool).
  - `batting`: per-innings rows joined to the match metadata above
    (position, runs, balls, fours, sixes, how_out, bowler_id,
    captain/keeper flags).
  - `bowling`: per-innings rows (overs, maidens, runs, wickets, econ,
    bowl_position).
  - `balls_faced`: ball-by-ball where `batter_id = pid` (only
    populated for matches with BBB; each ball carries `over_no`,
    `runs_bat`, `runs_extra`, `bowler_id`, plus the resolved
    bowler-type metadata fields snapshotted at build time).
  - `balls_bowled`: ball-by-ball where `bowler_id = pid` (each ball
    carries `over_no`, `runs_bat`, `runs_extra`, `wicket`,
    `batter_id`, plus the batter's batting_hand if known).
- Per-player JSONs avoid one giant bundle (would balloon to 50+ MB).
  Page lazy-fetches `../data/rcc/<id>.json` on load.
- Master index: `app/data/rcc/index.json` lists `[{player_id, name,
  apps, runs, wkts, has_bbb}]` for the chooser page.

### Page structure (vanilla JS)

- One template: `app/rcc/player.html`.
- Header: name, hero stats chips (career runs / wkts / apps /
  BBB-coverage %).
- Slicer rail: scrollable horizontal pills. State stored in URL
  search params so it's shareable.
- Stat cards below, all reactive to the slicer state.
- All filtering is client-side over the bundled JSON. Recompute on
  every chip toggle.
- Animations: staggered fade-in on card grid (CSS keyframes,
  ≤ 300 ms total). Respect `prefers-reduced-motion`.

### Acceptance

- Dashboard loads in < 1 s on 4G with cached fonts.
- Toggling any slicer feels instant (< 50 ms reflow).
- "Volume" sub-labels are visible on every BBB-derived stat. No stat
  reads as "30 SR vs spin" without "based on 12 balls" right next to
  it.
- The page degrades gracefully when no BBB data exists for a player —
  BBB slicers are greyed and the BBB panels collapse with a single
  explanatory line.

---

## Phase 3 — BBB analytics

These are the panels the dashboard above will call into. Build the
maths once in `_bbb_buckets.py`, share between the builder and any
report scripts that want them.

### A. Innings phase buckets (team innings)

- Buckets: `0-10`, `11-20`, `21-30`, `31-40`, `41-50` overs (also a
  `50+` bucket for declaration-format games).
- Bucket assignment uses `over_no` (0-indexed → over 1 means 0-9).
  Bucket = `min(over_no // 10 + 1, 5)` for limited-overs.
- Stats per bucket:
  - **Batting**: runs, balls, dismissals, dots, 4s, 6s; derive
    avg = runs / dismissals; SR = runs/balls × 100; dot % =
    dots/legal balls.
  - **Bowling**: balls bowled in that bucket, runs conceded, wickets;
    derive econ, SR, dot %.
- Surface as a horizontal bar group (the bucket with most volume gets
  full width; others scaled). Mobile-friendly because the labels are
  short.

### B. Player-innings buckets (batter only)

- Buckets: `0-10`, `11-20`, `21-50`, `51-100`, `101+`.
- Numbered against `ball_faced_index` — count of legal balls the
  player has faced **so far in that innings** at the moment of this
  ball.
- Stats per bucket: runs, SR, dot %, boundary %, dismissal rate.
  Shows the acceleration curve: "settles in for 0–10, takes off
  31–50."
- Visualisation: a step-line of cumulative SR by bucket plus a stack
  of where his runs come from.

### C. Spell breakdown (bowler only)

- Spell detection on the bowler's overs in a single innings:
  1. Sort the distinct `over_no`s asc.
  2. Walk: if next over is more than `+2` from the previous (i.e.
     gap of 3 or more empty overs), start a new spell. Standard
     alternate-end pattern (1, 3, 5, 7) keeps one spell.
- Stats per spell:
  - 1st spell: balls, runs, wkts, econ, dot %, opening overs
    (yes/no), avg over_no.
  - 2nd+ spells: same, aggregated across all subsequent spells.
- Render as two side-by-side mini-cards on the bowler view.

### D. Vs LHB / RHB (bowler only)

- Resolve each ball's `batter_id` against
  `data/metadata/players/<id>.json → metadata.batting_hand` at build
  time, snapshotted into the per-player JSON so the dashboard JS
  doesn't have to load the metadata bundle.
- Buckets: RHB / LHB / unknown.
- Stats per bucket: balls, runs, wkts, econ, SR, dot %, boundary %.
- Surface as three stacked rows on the bowler view.

### E. Vs pace / spin etc. (batter only)

- Mirror image: each ball's `bowler_id` → opposition metadata blob
  for `bowling_type`, `pace_type`, `spin_type`, `bowling_arm`,
  `angle_to_rhb`. Snapshot into the per-player JSON.
- Buckets:
  - bowling_type: pace / spin / unknown
  - bowling_arm: left / right / unknown
  - flavour: fast / medium / slow / wrist / finger / unknown
- Same stat shape as D.

### F. BBB volume visualisation (cross-cutting)

The user explicitly wants to **see** how thin or fat the BBB sample
is. Two surfaces:

1. **Per-card volume chip**: every BBB-derived card carries a small
   `🎯 N balls · X% covered` chip in its corner.
2. **Top-of-page coverage bar**: under the player name, render a
   stacked bar:
   - Width = total balls faced/bowled in the universe (estimated
     from scorecard balls, since not every match has BBB).
   - Filled segment = balls with BBB recorded.
   - Tooltip: "BBB covers X / Y balls (Z%)".
   - Animates on load (left-to-right wipe, 600 ms).
3. **Per-bucket volume**: each bucket bar's tail shows the raw count
   in muted serif numerals so you can sanity-check at a glance.

### Build-time outputs

- `_bbb_buckets.py` exports:
  - `team_phase(rows) -> dict[bucket -> stats]`
  - `player_phase(rows) -> dict[bucket -> stats]`
  - `bowler_spells(rows) -> list[spell_dict]`
  - `vs_hand(rows, hand_lookup) -> dict[hand -> stats]`
  - `vs_bowler_type(rows, bowler_meta_lookup) -> dict[type -> stats]`
- These take iterables of ball dicts, not SQL rows, so they're pure
  and testable.
- `build_rcc_dashboard.py` calls them once per player at build time
  for any **pre-aggregated** views, but ships the raw balls so the
  client can re-aggregate under any slicer combo.

### Acceptance

- All five BBB exhibits render on a player who has ≥ 1 BBB innings
  (e.g. Jono O'Neill, player_id 20972).
- Switching slicers recomputes buckets without a network round-trip.
- A player with zero BBB cleanly hides phases A–E and shows only the
  scorecard-derived A/B career snapshots, with an explanatory note.
- `team_phase` matches the scorecard `innings.runs - penalty_runs`
  to within ±1 % when summed across buckets.

---

## Order of execution

1. Phase 1 (visual rebuild) — single PR / branch. Sets the design
   vocabulary the next two phases live inside.
2. Phase 2 + 3 are intertwined: build `_bbb_buckets.py` first (cheap,
   testable), then `build_rcc_dashboard.py` data plumbing, then the
   client-side template + slicers. Each card type lands as it's
   ready — career snapshot first (no BBB), then phase A, then B, C,
   D, E.

## Don'ts

- **Don't** centralise the dashboard data in one giant JSON bundle.
  Per-player file = better caching + easier to iterate on.
- **Don't** add a backend or a search index server. Slicer state in
  URL params, computation in client-side JS.
- **Don't** widen the universe filter for the dashboard. Same 1st-XI
  / League + non-T20 Cup window as everywhere else.
- **Don't** desktop-ify any layout, even if the dashboard tempts.
  `max-width: 540px` survives this redesign too.
- **Don't** ship volume-blind stats. Every BBB-derived number needs
  the volume chip beside it.
