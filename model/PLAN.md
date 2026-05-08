# Innings-projection model — plan

## Goal

For every legal delivery in a limited-overs amateur match, output a
probability distribution over **the final innings total** of the side
currently batting, given:

- the **state of the innings** (balls in / out, runs, wickets, who's
  at the crease),
- the **skill** of the batters at the crease — kept as **separate
  metrics**: batting average `/100` AND strike rate `/100`, never a
  blended composite,
- the **skill** of the **remaining batters** still in the dressing
  room — same separation (avg `/100`, S/R `/100`),
- the **skill** of the **bowlers with overs still to bowl** — kept
  separate too: bowling average `/100` AND economy `/100`, plus
  bowling strike rate (balls per wicket) `/100`,
- the **ground's** historical scoring rate,
- the **time of year** (early/mid/late season — proxies pitch +
  weather).

**Why no blending.** Average and strike rate are correlated but
capture different things and matter at different innings stages —
average dominates early (preserve wickets), S/R dominates late
(accelerate). Same for bowlers: economy is run-suppression (matters
in the middle / death), bowling average is wicket-taking (matters
when you need a breakthrough). A single "/100 batter skill"
collapses information the model needs. We carry the metrics as
independent features and let the GBM learn the phase-dependent
interactions.

Each metric is separately percentile-ranked into `/100` against
the active league/tier cohort — so a player carries a small bundle
(`bat_avg_skill=84, bat_sr_skill=62, bowl_econ_skill=71,
bowl_avg_skill=58, bowl_sr_skill=55`) rather than one number.

Headline metric: a per-ball point estimate `μ̂` (predicted final
score) and an interval `(p10, p90)` calibrated on held-out matches.

## Toggle: remaining-batters factor

The "skill of the batters yet to come in" is the most uncertain
input — it depends on accurate skill snapshots for every player in
the order, and on knowing the **batting order** (which we don't always
have until they actually walk out, since #4 onwards may be
re-shuffled). We support a runtime toggle:

- `remaining_bat = 'real'` — feed the real `/100` aggregates
  (mean / min over the players yet to bat, kept as separate avg and
  S/R aggregates).
- `remaining_bat = 'default'` — feed a fixed cohort-median value;
  effectively tells the model "assume an average tail".
- `remaining_bat = 'masked'` — feed a sentinel value with the
  feature flagged as missing (for the model to handle natively, e.g.
  via LightGBM's `use_missing=True`).

Implementation: same model, three feature-input modes. We train with
the real values present; at inference time the caller picks. We
report all three predictions for every example so users can see how
much the assumption matters in their context.

## Universe

Hard filters, applied once into a `model_balls` materialised table:

```sql
WHERE m.match_type = 'Limited Overs'
  AND m.competition_type = 'League'
  AND m.competition_name NOT REGEXP '(T20|Twenty20|20-over|20/20|Smash)'
  AND m.season >= 2021                       -- BBB era
  AND m.is_complete = 1                      -- exclude rain-offs / abandoned
  AND b.match_id IN (SELECT match_id FROM match_bbb)
  AND m.result_description NOT LIKE '%D/L%'  -- DLS-affected: drop v1
  AND m.result_description NOT LIKE '%rain%'
```

Plus the same 1st-XI / non-junior / non-women filters as
`PLAN_AMATEUR_MODELLING.md` §"Division selection".

## Data shape

### Per-ball state vector

Each ball gets one row in `ball_features`. Skill metrics are kept
**unblended** — separate avg and S/R for batters, separate avg /
economy / S/R for bowlers — and aggregates over groups (remaining
batters, remaining bowlers) preserve that separation.

```
identity            match_id, innings_seq, ball_seq, over_no, ball_no_disp
match context       league_site_id, season, ground_id, match_date,
                    overs_per_innings, is_home_batting,
                    month_of_year, days_into_season
innings state       balls_gone, balls_left,
                    runs, wickets, run_rate,
                    wicket_rate (wkts per 6 overs),
                    required_rate (innings 2 only)
striker             batter_id, batter_runs_so_far, batter_balls_so_far,
                    batter_dot_pct_so_far,
                    batter_avg_skill,                       ← /100, career avg
                    batter_sr_skill,                        ← /100, career S/R
                    batter_recent_form_sr                   ← rolling, time-decayed
non-striker         same fields, suffix _ns
current bowler      bowler_id, bowler_overs_so_far,
                    bowler_econ_so_far_innings,
                    bowler_econ_skill,                      ← /100
                    bowler_avg_skill,                       ← /100
                    bowler_sr_skill                         ← /100
remaining bowlers   bowl_remaining_econ_mean,               ← /100 aggregate
                    bowl_remaining_econ_min,
                    bowl_remaining_avg_mean,                ← /100 aggregate
                    bowl_remaining_avg_min,
                    bowl_remaining_sr_mean,                 ← /100 aggregate
                    bowl_remaining_sr_min,
                    bowl_remaining_overs_total
remaining batters   bat_remaining_avg_mean,                 ← /100 aggregate
                    bat_remaining_avg_min,
                    bat_remaining_sr_mean,                  ← /100 aggregate
                    bat_remaining_sr_min,
                    bat_remaining_count
                    [these obey the toggle — see §Toggle above]
ground              ground_avg_total_recent (last N home games),
                    ground_overs_per_innings_avg
target (innings)    y_final_innings_runs                    ← labelled offline
```

`min` aggregates exist so the model can pick up tail risk: a side with
a long tail of weak batters has a low `bat_remaining_avg_min`, and the
projection should narrow the upper tail; a bowling side with no
genuine wicket-taker still to bowl has a high `bowl_remaining_avg_min`
and the projection should widen the upper tail of the batting side's
score.

`y_final_innings_runs` is the actual final score of the batting side
in **this** innings. For 2nd-innings successful chases it's
right-censored — handled in the loss function (§"2nd-innings handling").

### Generation pipeline

1. **Universe filter** → list of `(match_id, innings_seq)` pairs.
2. **For each innings**, walk the `balls` table in order and emit one
   `ball_features` row per legal delivery.
3. **Skill columns are joined in offline** from a separate
   `player_skill_snapshot(player_id, as_of_date, batting, bowling)`
   table — so we never recompute skill on the hot path, and historical
   rows reflect skill as it was *before* the ball was bowled (no
   data leakage).

## Player skill model — five `/100` metrics

Five metrics per player, none blended:

| Metric | Domain | What it measures |
|---|---|---|
| `bat_avg_skill` | batting | runs scored per dismissal, percentile-ranked |
| `bat_sr_skill` | batting | runs per 100 balls faced |
| `bowl_econ_skill` | bowling | runs conceded per over (lower = better, ranked accordingly) |
| `bowl_avg_skill` | bowling | runs per wicket (lower = better) |
| `bowl_sr_skill` | bowling | balls per wicket (lower = better) |

Each is the player's metric, percentile-ranked against the active
cohort in the same league tier and season, scaled to `[0, 100]`.
The continuous percentile is what the GBM consumes; the integer
`/100` is the UI surface.

The metrics aren't independent — a high-S/R batter often has a high
average too, a low-economy bowler often has a good strike rate — but
the model gets all five and learns the interactions itself. **We
never collapse them into a single number.**

### Definition

For each metric, at every snapshot date, compute the player's
**time-decayed weighted statistic** over their playing history:

- weight every ball / dismissal observed at date `d` by
  `exp(-(today - d) / τ)` with **τ = 12 months**;
- aggregate to runs / dismissals / balls / wickets;
- compute the headline metric (avg, S/R, econ);
- rank against the active cohort (same tier + season) → percentile
  → `/100`.

The 12-month decay is short enough that one stand-out season pulls
a young player's rating sharply upward, and a poor season properly
penalises a former regular. The 18-month default in v1 of the plan
felt too sticky for the rapid turnover at amateur level.

Both bat and bowl metrics are time-indexed: a 17-year-old debutant
in 2021 is not the same player in 2025.

### Estimation — staged

**Stage A: cold-start career baseline.** From the existing `batting`
and `bowling` aggregate tables (already loaded by `build_db.py`),
compute career S/R, average, and economy adjusted for opponent quality
(opponent batting/bowling skill from the same exercise, iterated to
fixed point). This is a lightweight Elo / coordinate-descent loop:
~5-10 iterations to converge on a snapshot.

**Stage B: BBB-grained refinement.** With cold-start as the prior,
estimate per-ball expected outcomes:

```
P(runs_bat = k | batter, bowler, state_features) = ...
```

via a logistic / multinomial GBM over the state features, with
batter-skill + bowler-skill embeddings as continuous inputs.

Player skills are then **the values that maximise the joint
likelihood** — a simple matrix-factorisation flavour:

```
log P(ball | batter, bowler, state) = α + β·b_skill + γ·B_skill + f(state)
```

We alternate (a) fit `f` and `α, β, γ` with skills fixed; (b) update
each player's skill given the residual on their balls. This is the
same loop used by Glicko / TrueSkill / cricmetric, applied to BBB.

**Stage C: time decay.** Weight each ball / dismissal by
`exp(-(today - date) / τ)` with **τ = 12 months**, so recent form
dominates but career baseline isn't lost.

### Output

```sql
CREATE TABLE player_skill_snapshot (
  player_id        INTEGER,
  as_of_date       TEXT,            -- yyyy-mm-dd, weekly snapshot
  bat_avg_skill    REAL,            -- /100
  bat_sr_skill     REAL,            -- /100
  bowl_econ_skill  REAL,            -- /100
  bowl_avg_skill   REAL,            -- /100
  bowl_sr_skill    REAL,            -- /100
  bat_balls_n      INTEGER,         -- effective sample size after decay
  bowl_balls_n     INTEGER,
  PRIMARY KEY (player_id, as_of_date)
);
```

One row per (player, every Sunday of every season). Per-ball joins
look up the snapshot row whose `as_of_date <= match_date`, ordered
desc, `LIMIT 1`. Players below a sample-size threshold
(`bat_balls_n < 100` or equivalent) get the league-tier-cohort median
in place of their own metric — Bayesian shrinkage to a sensible prior
rather than wild estimates from 10 balls of evidence.

This snapshot is also what lights up the metadata-page UI: render
five small chips per player rather than one composite number.

## Modelling target — final innings score

### Innings 1 (clean)

Innings 1's final score is the **labelled target**, no censoring
issues unless they're bowled out before overs run out (which is its
own normal scoring outcome — predict that).

### Innings 2 (chase) — handling

This is the modelling subtlety. Three strategies, in increasing
sophistication:

1. **Drop innings 2 entirely (v1).** Train and evaluate on innings 1
   only. Innings 2 prediction is then "what would they score if they
   played out their full overs?", which can use the innings-1 model
   directly with `is_chasing = 1` as a feature flag and accept some
   miscalibration where the chase ended early. Quick, principled,
   half the data.

2. **Right-censored regression (v2).** Treat the observed final score
   on a successful chase as `y >= target + 1` and fit a survival /
   AFT model. LightGBM has `objective=tobit` via custom loss; XGBoost
   has Cox / AFT. This recovers the missing-data problem
   correctly.

3. **Joint two-innings model with a chase-pacing component.** A batter
   chasing 270 in 50 overs paces differently than chasing 110.
   Explicit interaction term `(target - runs) * (overs_left)`
   captures the asymmetry; or a separate "chase strategy" branch.
   v3+.

Headline: **start with innings 1 only**; reserve innings 2 for v2
once the simpler model works.

## ML approach

### Baseline — gradient boosted regressor

LightGBM, `objective=regression_l2`, ~50 features, monotonic
constraints on the obvious ones (`balls_left ↓ → predicted_total ↓
no, predicted_total roughly stable but uncertainty narrows`; actually
on `runs ↑ → predicted_total ↑` is monotonic). One model trained on
all of innings 1; quick to fit, easy to inspect.

Quantile regression heads at `q={0.1, 0.5, 0.9}` give the
prediction interval for free.

### Sequence model — only if baseline plateaus

Each innings is a sequence of balls. A small transformer (4 heads, 2
layers, ~1M params) over the per-ball feature stream emits a
projection at every ball. Likely earns 5-15 % MSE improvement over
GBM but harder to debug. **Only pursue if GBM tops out.**

### Feedback loop with skill model

The full system is two coupled models:

```
[BBB events] → [player skill model] → [ball features w/ skill] → [GBM projection]
                       ↑                                                   |
                       +———————————— skill residuals from GBM ←———————————+
```

This is the "ML refines the parameters" the user described. Concretely:

1. Initial fit of GBM with cold-start skills → predicted runs per ball.
2. Compute residuals = observed runs - GBM-predicted runs, attributed
   to (batter, bowler) pairs.
3. Update skills using residuals as new evidence.
4. Refit GBM with updated skills.
5. Repeat 2-4 until skills stop moving by more than ε.

3-5 outer iterations is usually enough.

## Evaluation

### Metrics

- **MAE** (runs) of `μ̂` vs `y_final_innings_runs`, **per ball
  position**: typical curves drop from ±35 runs at ball 1 to ±10
  runs at ball 250. A good model beats the naive baseline (current
  run rate × overs left) at every position.
- **Calibration** of the `(p10, p90)` interval: the true value should
  fall inside it on 80 % of held-out balls, evenly across the innings.
- **Win-probability** as a derived metric for innings 2: convert
  projected innings-2 distribution → P(chase succeeds), evaluate Brier
  score against actual outcome.

### Held-out split

- Train: leagues 1-25 + Essex 2021-2024.
- Validate: Essex 2025 (hold a known season as a tuning set).
- Test: leagues 26-33 + Essex 2026 (geography- AND time-held-out, so
  we measure generalisation to unseen leagues and a new season).

Fixed splits, never touched during model development. Every checkpoint
gets the same evaluation report.

## Schema additions

```sql
-- player skill snapshots (computed offline, refreshed weekly)
-- five separate /100 metrics, never blended
CREATE TABLE player_skill_snapshot (
  player_id        INTEGER,
  as_of_date       TEXT,
  bat_avg_skill    REAL,
  bat_sr_skill     REAL,
  bowl_econ_skill  REAL,
  bowl_avg_skill   REAL,
  bowl_sr_skill    REAL,
  bat_balls_n      INTEGER,
  bowl_balls_n     INTEGER,
  PRIMARY KEY (player_id, as_of_date)
);

-- per-ball feature rows (rebuilt fresh from balls each pipeline run)
CREATE TABLE ball_features (
  match_id INTEGER, innings_seq INTEGER, ball_seq INTEGER,
  -- ... 50-odd columns per §Per-ball state vector
  -- includes both 'real' and 'default' values for remaining-bat
  -- features (cohort-median fallback) so the toggle is a column-pick
  PRIMARY KEY (match_id, innings_seq, ball_seq)
);

-- ground rolling stats
CREATE TABLE ground_stats (
  ground_id     INTEGER,
  season        INTEGER,
  avg_first_innings_runs   REAL,
  avg_overs_per_innings    REAL,
  n_matches     INTEGER,
  PRIMARY KEY (ground_id, season)
);
```

The DB stays club-agnostic; nothing here is league-specific.

## Build order

Each step independently committable + runnable.

1. **Universe materialisation.** SQL view → `model_innings(match_id,
   innings_seq, overs_per_innings, ground_id, ...)` filtered to the
   limited-overs, BBB-covered universe. Sanity-check counts per
   league/season.
2. **Cold-start skill fit.** Career S/R + economy with opponent
   correction. Output → `player_skill_snapshot` for one cutoff date
   (today). Validate by spot-checking known-good Rainham players —
   their `/100` should rank intuitively.
3. **Ground stats.** `ground_stats` table.
4. **Ball-features generator.** Walk balls table; emit feature rows.
   Includes the skill snapshot join. Validate: pick 5 random
   innings, check feature values manually against the scorecard.
5. **GBM v1.** Innings 1 only, fixed skills. Get an MAE-per-ball
   curve. Compare to "current RR × overs left" baseline.
6. **Skill refinement loop.** Outer iteration; observe skills
   converge.
7. **GBM v2.** Add innings 2 with right-censored loss, evaluate
   chase-success Brier score.
8. **Time-of-year features.** `month_of_year`, `days_into_season`,
   `temperature` (external join, optional).
9. **Sequence-model exploration** if GBM plateaus.
10. **Surface as a report** — per-match "win probability over time"
    chart joining innings 1 + innings 2 projections, mirroring
    cricinfo's win-prob graphic.

Steps 1-5 deliver a working v1; everything after is refinement.

## Risks / open questions

- **Skill-model identifiability.** With limited data per player a
  naive update can overshoot. Use Bayesian shrinkage to a league-tier
  prior (`Premier ≠ Div 4`).
- **Cohort effects.** A 2021 Premier batter and a 2025 Premier batter
  aren't directly comparable; the league overall shifts. The
  time-decay window should partially handle this; verify by checking
  league-wide mean batting skill stays at ~50 by construction.
- **Overs-format mix.** 40 / 45 / 50 / 60-over matches all coexist.
  `overs_per_innings` as a feature handles it; check it doesn't
  dominate (regularise).
- **Ground vs pitch.** Some grounds run multiple strips. We can't
  distinguish; expect residual variance.
- **Cold matches early in season.** A late-April 200 isn't worth a
  late-July 200; the time-of-year feature should pick this up.
- **Unattributed balls.** ~5-10 % of balls have NULL batter or bowler
  IDs (per `_disambig.py` notes). Drop them from skill updates;
  include them with mean-imputed skill in the projection model.
- **Data drift.** Each new season the snapshot must be retrained.
  Make the pipeline cheap enough that it runs end-to-end in <1 hour.

## Out of scope (for now)

- Live in-match prediction — this is an after-the-fact analytical
  model. Live serving is a separate problem.
- Player-vs-player matchup priors (specific batter X usually scores
  N off bowler Y). Sparse for amateur cricket; revisit if useful.
- Fielder-effect modelling. We have catches/run-outs in the BBB
  data but matching field positions to outcomes needs more work.
- Toss / weather — flagged as "v2" features, not blockers for v1.

## Folder layout

```
model/
  PLAN.md                  ← this file
  FINDINGS.md              ← what the Essex test taught us
  build_skill.py           (TBD — Stage B skill iteration)
  build_features.py        (TBD — ball_features generator)
  train_gbm.py             (TBD — model training)
  evaluate.py              (TBD — held-out evaluation)
  notebooks/               (TBD — exploratory)
```

Everything new lives under `model/`; we don't pollute the existing
`stats/` modules with modelling code. Reuse `_app_lib.py` etc. via
imports as needed, but keep the directories distinct.
