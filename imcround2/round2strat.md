# Round 2 – "Growing Your Outpost" — Master Strategy

## 0. Calibration anchor — the R1 benchmark

`imcround2/template_alg.py` (the current v2 code, unchanged for R2) generated **≈ 90,000 XIRECs in Round 1**. That number is the ground truth we plan against. All estimates in this document are anchored to it, not to theoretical maxima.

Important scoring detail from `claude.md`:

> *Historical testing: 1,000 iterations. Final scoring: 10,000 iterations.*

**Final scoring runs ~10,000 ticks (≈ one full "day" of the training data), not the 30,000 ticks we see in the combined CSVs.** So the practical ceiling per product, per round, is:

- PEPPER hold-at-+80 over 10,000 ticks × 0.1 mid/tick = **80,000 XIRECs** (absolute PEPPER ceiling).
- OSMIUM MM + taker + MR: bounded by fill volume; empirically ~10–25k from R1.

This means the R1 template was **already capturing ~85–95% of the PEPPER ceiling** and a modest slice of OSMIUM. Absolute headroom for Round 2 is therefore limited; our plan targets **+10–30k of marginal lift**, not a doubling of PnL. Overshooting these numbers would be suspicious.

### Where the R1 template leaks (to be fixed in R2)

Verified by reading `template_alg.py`:

1. **`PEPPER_LOAD_TARGET = 60`** — we cap the aggressive load at 60 units, leaving 20 units of drift-exposure on the table during the entire holding phase. Cost: up to 20 × 9,500 ticks × 0.1 ≈ **19,000 XIRECs** if the residual 20 units never get filled passively.
2. **Passive PEPPER ask quoted at `fair + 7 − pos_skew + long_bias`** — even while near full long, we're still showing a sell at fair + ~3 to +5. Every time it fills we give up future drift on that unit. Over 10k ticks, even an occasional handful of unplanned sells compounds, and worse, we lose position we then can't easily rebuild (the passive bid fills slowly).
3. **`PEPPER_LOAD_PRICE_TOL = 1`** — we only overpay by 1 XIREC while loading. A single unit bought at fair+5 instead of fair+1 breaks even in 40 ticks of drift, so we are *under-paying* for early load. Especially painful on the first 100 ticks of the session.
4. **`OSMIUM_MAKER_OFFSET = 3`** — we quote at fair ± 3 inside a structurally-16-wide book. This is not wrong, but we can probably be slightly tighter (2) without getting run over, given σ ≈ 5 and fast mean reversion.
5. **Snap tolerance `OSMIUM_SNAP_TOL = 5`** — holds the EMA anchored to 10,000 even when the EMA has drifted 4–5 away. Combined with offset=3, that means in biased regimes the effective quote is 2 XIRECs off, eating our edge.
6. **No guard on aggregate position-limit rejection**: current code clamps per-fill, not per-side aggregate. It *happens* to stay safe because quotes are posted after takes, but this is fragile — easy to trip when we add more order layers.

These are concrete, small, low-risk fixes. Adding them up: +15–30k realistic lift.

## 1. Objective and scoring model

Round 2 scores your Round-2 algorithmic PnL only. New rule: the **Market Access Fee (MAF)** blind auction.

- `bid(self) -> int` returns a one‑shot XIREC bid.
- Top 50% of bids across all participants (median tie-broken upward) win and see **100% of the bot quote feed**; losers see the default ~80% (slightly randomized each tick to discourage overfit).
- Winners have their bid subtracted from R2 trading PnL. Losers pay nothing.
- The MAF is *not* applied in the sandbox — we always see the 80% feed locally. So **algorithmic logic must be robust to missing quotes**; it must not assume we will always see the inside of the book.

Products and position limits (unchanged from R1):

| Product | Limit | Nature |
| --- | --- | --- |
| `INTARIAN_PEPPER_ROOT` | ±80 | Deterministic linear drift +0.1 mid/timestamp (verified on day ‑1, 0, 1) |
| `ASH_COATED_OSMIUM` | ±80 | Stationary, mean 10,000, σ≈5, **strongly mean‑reverting (AC1 ≈ 0.7)** |

Realistic PnL estimate (anchored to the 90k R1 benchmark, 10k-iteration scoring run):

| Source | R1 captured (est.) | R2 target (with fixes) |
| --- | --- | --- |
| PEPPER hold-the-long (80‑ceiling = 80k) | ~70–78k | **~77–80k** (load to 75, kill the passive ask) |
| PEPPER free taker edges | ~1–3k | ~2–4k |
| OSMIUM MM on 16-wide book | ~8–15k | **~12–20k** (tighter offset, better snap) |
| OSMIUM taker + MR sweeps | ~2–5k | ~4–8k |
| **Gross total** | **~85–100k** | **~100–115k** |
| MAF cost (if we win) | — | -(0 to 4,000) |
| **Net target** | — | **~95–115k** |

So a disciplined R2 submission should clear the R1 template by ~10–25k. Anything we backtest over the full 30k-tick training set should show roughly ~3× that (since our logic scales linearly with ticks-held), which is why internal backtests may read 280–330k — that is *not* the scoring scale.

---

## 2. Data-driven facts we will exploit

Directly measured from `imcround2/ROUND_2/prices_round_2_day_*.csv` (zero‑mid rows removed):

### 2.1 `INTARIAN_PEPPER_ROOT`

- Slope per timestamp unit: **0.1000** on every day (day ‑1: 0.1000, day 0: 0.1000, day 1: 0.1000). Since a tick advances timestamp by 100, that's **+10 mid per tick**. Across 10,000 ticks/day = +1,000 per day. 30,000 ticks end-to-end = +3,000 net.
- Detrended residual: std ≈ 2.2–2.5, max |dev| ≤ 11, AC1 ≈ 0 → essentially IID Gaussian noise around the line.
- Book spread: mode 13–14, almost always in [12, 19]; occasional narrow prints (spread 2–4) which are our free taker opportunities.
- Average top-of-book volume per side: ~11.6. Level‑2 presence 65%; level‑3 rare (1.4%).
- Free taker edges: ask < fair ~1.5% of ticks, mean edge 4.2 when they occur. Bid > fair ~1.6%, mean edge 3.3.

**Interpretation**: PEPPER is a *pure accumulation play*. Every unit long earns +0.1 XIREC per timestamp (≈ +10 per tick). Holding +80 continuously for one 10k‑tick day = +8,000 XIRECs. Not being full is directly leaving money on the table.

### 2.2 `ASH_COATED_OSMIUM`

- Slope ≈ 0 on all days (|slope/tick| < 0.001).
- Residual std ≈ 4.5–5.6; observed range ≈ ±20 around 10,000.
- **AC1 ≈ 0.65–0.79** → persistent but bounded. A dislocation tends to continue for a few ticks before reverting.
- Revert statistics: 600 spikes with |dev|>10 across 30k ticks; **median time back to |dev|<3 is 3 ticks**, mean 9.3, 90th percentile 28. Bars snap back fast.
- Spread: mode **16** (59% of ticks), otherwise a long tail at 18–19 and a short thin tail at 5–12. Dominance of 16 is our structural edge — we can quote well inside it.
- Average top-of-book size ≈ 14. Level‑2 65%, level‑3 2%.
- Taker edges: ask < 10,000 ~3.2% of ticks (mean edge 2.9); bid > 10,000 ~5.0% (mean 3.0). About 8% of ticks have some taker opportunity worth ≥ 2 XIREC.

**Interpretation**: OSMIUM is a *market-making + mean-reversion* asset. The fair is 10,000 with tight EMA tracking. Quote tight and skewed inside the 16-wide spread; fade large deviations; use MR-sweeps on the rare excursions.

### 2.3 Cross-asset

- Correlation of detrended PEPPER residuals vs OSMIUM residuals = **0.0015**. No exploitable cross-signal. The two legs are run independently.

---

## 3. Algorithmic strategy (the `Trader.run` method)

Overall pattern (see `imcround2/template_alg.py` for the v2 scaffold; this plan is the v3 upgrade target — write as a new file, e.g. `imcround2/trader_v3.py`):

```
class Trader:
    def bid(self) -> int: ...          # MAF auction — §4
    def run(self, state): ...          # Main strategy — §3
```

Shared infra:

- Persist state via `traderData = json.dumps(...)`. Keep payload <5 KB — well under the 50k cap. Stored fields:
  - `pepper_c_hat`: running intercept estimate for PEPPER line (float).
  - `pepper_c_n`: number of samples in running avg.
  - `osm_ema`: microprice‑blended EMA for OSMIUM.
  - `osm_hist_tail`: last 20 residuals (for short‑horizon regime check).
  - `last_ts`: last seen timestamp, for sanity/epsilon guards.
- Robust to missing sides: if `order_depth.buy_orders` or `sell_orders` is empty for a symbol, skip that leg that tick (also happens when we're losers in the MAF and the feed is thinned).
- Budget: <5 ms/tick — all math is O(1) per tick except tiny fixed-size list operations. No numpy dependency inside `run()`.

### 3.1 `INTARIAN_PEPPER_ROOT` — "drift accumulator"

Model: `fair(ts) = c + 0.1 * ts`, where `c` is the current estimate of the y-intercept. **Slope 0.1 is a hard-coded prior** (observed identical on all 3 training days — treating it as part of the world model, not estimating it).

Each tick:

1. Compute `best_bid, best_ask, mid` (skip if either side empty).
2. Compute the implied per-tick intercept `c_t = mid - 0.1 * state.timestamp`.
3. Update `c_hat` as a slow running mean: `c_hat = c_hat + (c_t - c_hat) / min(n+1, 500)` (essentially a Welford mean, capped window to stay adaptive). First-sample bootstrap: `c_hat = c_t`.
4. Compute `fair = round(c_hat + 0.1 * state.timestamp)` — integer, since all prices are integers.
5. Also compute `fair_fwd = round(c_hat + 0.1 * (state.timestamp + HORIZON))` with `HORIZON = 200` timestamp-units (2 ticks). The maker quotes are priced off `fair_fwd` to internalize drift expectations.

**Aggressive loading phase (the money maker)**:
- Define `LOAD_TARGET = 75`, `LOAD_SLACK = 5` (we never chase all 80 in one tick to leave room for passive fills).
- While `position < LOAD_TARGET`:
  - Compute an allowed overpay `K_pay`:
    - `pos < 20 → K_pay = 5`  (we desperately want fills; 5 XIRECs of overpay recouped in 50 ticks at +0.1/tick).
    - `20 ≤ pos < 50 → K_pay = 3`.
    - `50 ≤ pos < 70 → K_pay = 2`.
    - `70 ≤ pos < 75 → K_pay = 1`.
  - Walk `sell_orders` ascending. Take every level where `ask_price ≤ fair_fwd + K_pay`, up to `need = LOAD_TARGET - position` and `buy_cap = 80 - position`.
- After load phase:
  - Taker pass: take any ask at `ask_price ≤ fair - 0` (lift anything at or below current fair — it's a free expected win given the drift).
  - We **do not sell** PEPPER unless the bid is egregiously rich relative to *forward* fair (see below).

**Passive quoting**:
- Maker bid: `fair_fwd - pos_skew`, where `pos_skew = round(4 * position / 80)`. So when empty we bid near fair, when near full we step away.
  - Example: `position = 0 → bid at fair_fwd`; `position = 80 → bid at fair_fwd − 4` (effectively disabled by `buy_cap = 0`).
- Maker ask: we normally **do not quote an ask at all** for PEPPER. Rationale: drift is +0.1/tick, passive shorts are strictly negative EV.
  - Only post an ask when `position == 80` *and* best_bid is within 3 of fair_fwd+3 — i.e., we're trying to harvest a momentary spike. Quote an ask at `fair_fwd + 4` with size equal to the smallest of 10 and `sell_cap`.
- Opportunistic taker sell: if `best_bid ≥ fair_fwd + 5`, sell up to `min(best_bid_volume, sell_cap, 10)`. Edge captured ≥ 5 and we plan to re-buy within a few ticks at fair. Cap so we don't blow out inventory.

Order submission safety:

- Always submit `size ≤ buy_cap` / `≤ sell_cap` to avoid the **aggregate-rejection** rule (breaching either side kills all orders on that side, not just overflow).
- If `maker_bid >= maker_ask` (shouldn't happen here because we usually skip asks), degenerate to `fair_fwd − 1` / `fair_fwd + 3`.

### 3.2 `ASH_COATED_OSMIUM` — "tight MM with MR overlay"

Model: fair ≈ 10,000 with a microprice-blended EMA, plus a snap-to-anchor tolerance.

Each tick:

1. Compute `best_bid, best_ask`; skip if either side empty.
2. `mid = (best_bid + best_ask)/2`.
3. `micro = (best_ask * bb_vol + best_bid * ba_vol) / (bb_vol + ba_vol)` — volume-weighted inversion; reflects book pressure.
4. `price_signal = 0.5 * mid + 0.5 * micro`.
5. `ema = α * price_signal + (1-α) * ema`, with `α = 0.05`.
6. Snap logic: if `|ema - 10,000| ≤ 3 → fair = 10,000`, else `fair = round(ema)`. The tolerance of 3 (tightened from the v2 value of 5 after seeing σ≈5) lets us reanchor faster to small biases while still ignoring noise.
7. Compute `deviation = mid - fair`.

**Taker pass** (free edges):
- Walk asks: take any `ask_price ≤ fair - 2`, size ≤ `buy_cap`.
- Walk bids: take any `bid_price ≥ fair + 2`, size ≤ `sell_cap`.
- Edge of 2 is comfortably above the expected spread+slippage.

**Mean-reversion sweep** (fat-tail insurance):
- If `deviation ≥ 8` and `sell_cap > 0`: for every resting bid at or above `fair`, sell up to `min(bid_vol, sell_cap, 15)` per level. Walk down the whole book until the condition fails.
- Symmetric on the buy side for `deviation ≤ -8`.
- Threshold 8 ≈ 1.6 σ — chosen because median revert time from |dev|>10 is 3 ticks, so entering at 8 typically gets a profitable reversion within <10 ticks.
- Sweep uses `min(… ,15)` per level to avoid blowing through the 80 limit on a transient spike.

**Passive quoting**:
- Base maker offset: `BASE = 2` (quotes sit fair ± 2, well inside the 16‑wide book).
- Position skew: `pos_skew = round(4 * position / 80)` (max ±4).
- Deviation skew: `mr_shift = -1 if deviation > 4 else (+1 if deviation < -4 else 0)`. Lean us against the current dislocation.
- Quotes:
  - `maker_bid = fair - BASE - pos_skew + mr_shift` with size `buy_cap`.
  - `maker_ask = fair + BASE - pos_skew + mr_shift` with size `sell_cap`.
- Safety: if `maker_bid >= maker_ask`, reset to `fair - 2` / `fair + 2`.

**Quoting inventory caution**:
- If `|position| > 60`, widen the aggressive side: instead of `BASE + pos_skew`, use `BASE + pos_skew + 1`. Avoids whipsaw fills right at the limit.
- If one side's capacity is 0, skip quoting that side (don't submit zero‑size orders).

### 3.3 Execution order inside `run()`

1. Load state (defaults on empty).
2. Handle `INTARIAN_PEPPER_ROOT` (it's the bigger PnL driver; do it first — cheap).
3. Handle `ASH_COATED_OSMIUM`.
4. Serialize state → `traderData`.
5. Return `{PEPPER: [...], OSMIUM: [...]}, 0, traderData`.

All operations are O(L) in book depth (≤3 levels) → trivially fits the 900ms budget.

---

## 4. MAF auction — the `bid()` method

The MAF decision is now firmly calibrated against the 90k R1 benchmark, not a theoretical upper bound. Two competing effects:

**What winning the MAF actually buys us** (25% more quote feed, so ~100% vs 80%):

- **PEPPER**: dominated by position holding. Loading and the hold are bottlenecked by our own +80 position limit, not by how many quotes we see. The extra 25% of the feed only helps marginally on the rare cheap-ask prints (~1.5% of ticks → ~1.9% with full feed). Extra PnL: **≤ 1,000 XIRECs**.
- **OSMIUM**: MM fill rate scales roughly with counterparty flow visibility, and so does taker-edge detection. A ~25% wider view of bot flow plausibly adds ~15–25% to OSMIUM PnL. On a 15–25k base: **+2,500 to +6,000 XIRECs**.
- **Combined expected uplift from winning: ~3,500 – 7,000 XIRECs.** Plausibly lower (~2–4k) if OSMIUM MM is already competing for the top of book and extra quote volume doesn't convert to incremental fills.

**What we pay**: our exact bid, subtracted 1:1 from R2 PnL. So the bid must be *strictly less* than our expected uplift, and should leave a margin to cover the uncertainty in that uplift estimate.

### Bid-sizing math

- Expected uplift E[U] ≈ 4,000 XIRECs (midpoint of the range above; conservative given our PEPPER leg doesn't gain much).
- Standard-deviation-ish uncertainty around that: ± ~2,500.
- We want `P(bid < U) × (E[U|win] − bid) − P(bid ≥ U) × 0` to be maximal subject to also clearing the median of all bids.
- Past Prosperity auctions (e.g. R1 manual bid clearing prices) tell us the median on "pay-to-play for a small structural advantage" lands in the low thousands — typically **1,000–3,000 XIRECs**, with a heavy right tail of overbidders.
- Clearing the median is what we care about, not winning outright. A bid of **2,500** plausibly clears the median (most teams either bid 0/1 or overbid) and leaves ~1,500 XIRECs of expected surplus even in the pessimistic uplift scenario.
- Going higher (5k+) would only make sense if we thought median bids were themselves in the 3k+ range, which seems unlikely given that (a) many teams will treat the MAF as a curiosity and bid 0, (b) the structural value of 25% more feed is not widely advertised as huge.

**Decision**: return **2,500**.

```python
def bid(self) -> int:
    return 2500
```

### Robustness contract (this is the algorithmic-side MAF work)

Because we plan for both the winning-MAF and losing-MAF scenarios, the algorithm **must not assume we see the full feed**. Concretely, add to the R2 code:

1. **Skip silently on empty sides.** `if not order_depth.buy_orders or not order_depth.sell_orders: return []` for that symbol that tick. The template already does this for book pricing; extend it to the taker/MR passes too so a missing top-of-book doesn't crash or corrupt state.
2. **Don't let a single tick's missing quote change the EMA / intercept drastically.** Already handled by the α = 0.05 EMA and the capped-window intercept estimator; just confirm in code review that we skip the EMA update on a missing feed rather than feeding it a stale mid.
3. **Maker quotes are sized to the full remaining capacity**, so even if 20% of ticks have our quote invisible to some bots, the other 80% still fill aggressively. This is free — no code change needed, just confirm we don't accidentally post size‑1 probing quotes.
4. **Never rely on a specific bot's trade flow being present.** The feed thinning is randomized per tick, so any bot-identity-based logic (there is none today, but if we ever add it) would break. Keep the strategy purely price- and depth-based.

### If we later learn the actual MAF median

After the first live R2 submission posts, we'll see our realized PnL and can back out roughly whether we won the MAF. If we *won* at 2,500 and our PnL-after-fee is higher than expected, we can hold. If we *lost* (unlikely) and the leaderboard tells us median was much higher, we adjust upward in a follow-up submission. Do not re-bid speculatively.

---

## 5. Risk controls and defensive coding

1. **Position-limit aggregate rule**: the engine rejects *all* orders on a side if the aggregate would breach ±80. We always recompute `buy_cap = 80 - position`, `sell_cap = 80 + position` and clamp every individual order to those caps cumulatively as we emit them.
2. **Missing feed robustness**: when `buy_orders` or `sell_orders` is empty for a product (either because we're a MAF loser and got thinned feed or because the book is one-sided), skip pricing this tick for that product. Do not synthesize a quote out of thin air.
3. **State corruption fallback**: if `json.loads(state.traderData)` throws, reset to defaults. Add `.setdefault` guards for every expected key to survive version upgrades.
4. **Time budget**: current design is O(levels in book) per tick ≈ 6 levels × 2 products = negligible. No regression or numpy. Target <2 ms/tick.
5. **Numerical guards**: clamp `pepper_c_hat` bootstraping so `fair_fwd` is within [mid − 50, mid + 50] of observed mid on tick 0 (prevents a single outlier from poisoning the run).
6. **Unit-test the taker loops**: for each side we accumulate positions in-variable so we never double-count a level.
7. **No global mutable state** (AWS Lambda may reuse or cold-start containers). All state lives in `traderData`.

---

## 6. Implementation checklist (for the coding pass)

- [ ] Create `imcround2/trader_v3.py` derived from `imcround2/template_alg.py`.
- [ ] Replace the PEPPER OLS with the *locked-slope* intercept estimator (§3.1). Keep the loading phase but widen `LOAD_TARGET` to 75 and tier the overpay by position.
- [ ] Remove PEPPER passive-ask quoting entirely, replace with an optional "rich bid harvester" (§3.1).
- [ ] Tighten OSMIUM snap tolerance to 3, set BASE=2, add the `mr_shift` and the 8-threshold MR sweep.
- [ ] Implement `bid(self) → 5000`.
- [ ] Add state persistence with `pepper_c_hat`, `pepper_c_n`, `osm_ema`, `last_ts` (+ optional 20‑sample OSMIUM residual ring buffer).
- [ ] Add try/except around `json.loads` with a clean defaults dict.
- [ ] Add a final `sanity()` clamp before each `orders.append` enforcing `sum of buy sizes ≤ buy_cap` and `sum of |sell sizes| ≤ sell_cap`.
- [ ] Local backtest against the three CSVs in `imcround2/ROUND_2/` via `run.py`; verify per-product PnL and confirm:
    1. PEPPER position ramps to +70 within the first ~200 ticks of each day.
    2. PEPPER never goes net-short except on opportunistic `bid ≥ fair+5` sells that are mean-reverted within 20 ticks.
    3. OSMIUM average position oscillates roughly in [−30, +30] and fills regularly on both sides of the 16-wide book.
- [ ] Sanity-check tick-time: average < 5 ms, worst < 50 ms.

---

## 7. What I am *not* doing (and why)

- **Fancy Kalman / Fourier for OSMIUM**: residual series is close to an AR(1) with ρ≈0.7. An AR(1) predictor adds only ~1 XIREC of edge over the microprice EMA, not worth the complexity and extra tick time.
- **Deeper book L2/L3 scraping**: level-3 appears <2% of ticks; all edges I care about (≥2 XIREC for OSMIUM, ≥0 for PEPPER) live at level 1. Walking L2 only helps on MR sweeps, which we already do.
- **Cross-asset hedging**: correlation is 0, no hedge.
- **Dynamic MAF bidding logic**: `bid()` is called once per submission, and we have no information about other bidders. It's a game-theoretic one-shot — a point estimate is correct.
- **Fitting the PEPPER slope from data every tick**: wastes budget and is a regression to known truth. We *lock* slope = 0.1 and only estimate the intercept.

---

## 8. Expected outcome (calibrated to the 90k R1 benchmark)

On the **10,000-iteration scoring run** (one "day" equivalent):

- PEPPER PnL: **~78–83k XIRECs** (close to the 80k absolute ceiling, vs ~70–78k captured by the R1 template).
- OSMIUM PnL: **~15–28k XIRECs** (vs ~10–20k captured by the R1 template).
- MAF bid: **−2,500 XIRECs** if we clear the median (else 0).
- **Target Round-2 final PnL: ~95,000 – 115,000 XIRECs net of MAF.** Stretch: ~120k if both fixes land clean.

On a **full 30k-tick training backtest** (e.g. `python run.py` over the three R2 CSVs), these numbers scale ~3×, so expect ~280–340k in local backtests. Do **not** report that as "target PnL" — scoring is 1 day, not 3.

The concrete deltas vs `template_alg.py` are (in rough order of contribution):

1. **Kill the PEPPER passive ask** and raise `LOAD_TARGET` to 75 with tiered overpay → +6–15k.
2. **Tighten OSMIUM `MAKER_OFFSET` 3→2 and `SNAP_TOL` 5→3** → +2–5k, subject to inventory skew.
3. **Add an aggregate-position-limit clamp** and empty-book guards → not PnL, but eliminates a possible catastrophic rejection that wastes a tick's worth of drift.
4. **MAF bid = 2,500** → expected +1,000 to +4,500 net of fee vs not bidding.

This keeps us on a simple, auditable, low-latency algorithm (≤ ~2 ms/tick, well under the 900 ms budget) and clears the 90k R1 mark by a realistic margin rather than chasing an implausible 3× number.
