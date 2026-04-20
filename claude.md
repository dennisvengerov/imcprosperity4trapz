MY GOAL IS TO WIN IMC PROSPERITY CHALLENE BY IMPELEMENTING THE BEST ALGORITHM. 

As an agent, you will always reference the rules and visualizations. 

rules wiki link: https://imc-prosperity.notion.site/prosperity-4-wiki

LOOK AT STOCK VISUALIZATIONS inside round 2 folder (the .png) TO SEE THE PRICE AND VOLUME TRENDS VISUALLY
Summarized:

Overview: What Prosperity 4 Is
Prosperity 4 is a 15-day algorithmic + manual trading competition set in a sci-fi universe. You're trading on behalf of the fictional planet Intara, earning an in-game currency called XIRECs, on an exchange run by XIREN (eXtended Interplanetary Resource Exchange Network). The competition is structured as a Tutorial Round followed by 5 scoring rounds. Round 1's explicit goal is to net ≥ 200,000 XIRECs before the third trading day.

Each round has two components:

Algorithmic Challenge — a Python Trader class that auto-trades against bots on a simulated exchange.
Manual Challenge — one-shot human decisions (auctions, puzzles) submitted via the GUI.
The Algorithmic Trading Model
The Trader class
You submit a Python file that defines a Trader class with a single required method:

def run(self, state: TradingState) -> tuple[dict[Symbol, list[Order]], int, str]:
    ...
    return result, conversions, traderData
result: dict mapping product → list of Order objects to send this tick.
conversions: int for conversion requests (island-to-mainland arbitrage, used in later rounds).
traderData: a string you can use to persist state across ticks (AWS Lambda is stateless, so class/global variables are NOT guaranteed to persist; serialize with jsonpickle if needed).
For Round 2 only, you must also define a bid() method; it's ignored in all other rounds but it's safe to always include it.
Simulation mechanics
Historical testing: 1,000 iterations. Final scoring: 10,000 iterations.
Each tick, run() receives a fresh TradingState containing market data since the last tick.
Hard 900 ms per-tick time limit (average target ≤ 100 ms). Go over → your call times out.
traderData string is capped at 50,000 characters.
Only the standard Python 3.12 library plus the libraries listed in Appendix C are supported. No external packages.
Submissions get a UUID + runID — keep these handy when reporting issues on Discord.
The TradingState object
Key fields:

timestamp: current tick time.
listings: static product metadata (symbol, denomination in XIRECs).
order_depths[Symbol] -> OrderDepth: the visible bot-placed quotes.
buy_orders: Dict[int, int] — price → positive quantity.
sell_orders: Dict[int, int] — price → negative quantity.
Invariant: max buy price < min sell price (otherwise they'd have matched).
own_trades[Symbol] -> List[Trade]: your trades since last tick.
market_trades[Symbol] -> List[Trade]: bot-vs-bot trades since last tick. Counterparties are blank unless SUBMISSION is involved.
position[Product] -> int: signed net position (negative = short).
observations: includes plainValueObservations and conversionObservations (with bidPrice, askPrice, transportFees, exportTariff, importTariff, sunlight, humidity, etc.) — relevant in later rounds.
The Order class
Order(symbol, price, quantity). Positive qty = buy, negative = sell. Price is max buy / min sell.

How matching works
Execution is instantaneous and deterministic — the player's order always arrives before any bot could front-run them at that tick.
If your order crosses an existing bot quote, it matches immediately (partially or fully).
Any unfilled remainder sits as a visible resting quote. Bots may trade against it during the interval to the next tick, or it's auto-cancelled at the end of the tick.
Between your cancellation and the next TradingState, bots can also trade among themselves.
Position limits
Enforced per product, as the absolute (long OR short) max.
Enforcement is on the aggregated quantity of orders you submit. If the sum of buy (or sell) orders, if fully matched, would breach the limit, ALL those orders on that side are rejected — not just the overflow.
Example: limit 30, current position −5 → max legal aggregate buy volume is 30 − (−5) = 35. Volume 35 is OK; 36 → all buys rejected.
Conversions (later rounds)
You can only convert up to your absolute position, you must pay transport + import/export tariffs, and returning 0 / None skips conversion. Over-requesting → entirely ignored.

Rounds Summary
Tutorial Round — "Simulator Practice"
Products: EMERALDS (stable value) and TOMATOES (fluctuating).
Position limits: 80 each.
Not scored; purpose is to learn the GUI + submission flow.
No manual challenge.
Round 1 — "Trading Groundwork" (Intara landing)
Goal: ≥ 200,000 XIRECs PnL before day 3.
Algorithmic products:
INTARIAN_PEPPER_ROOT — stable ("hardy, slow-growing"), analogous to EMERALDS. Position limit: 80.
ASH_COATED_OSMIUM — volatile "but maybe follows a hidden pattern" (hint: mean-reversion / seasonal / Fourier-style structure, typical IMC setup). Position limit: 80.
Manual challenge: "An Intarian Welcome" — sealed-bid auctions:
You submit a single (price, quantity) limit order per product. You are guaranteed to be last to submit, after all bids/asks are locked.
Clearing rules: the exchange picks the clearing price that maximizes total traded volume, breaking ties by the higher price.
All bids ≥ clearing price and asks ≤ clearing price execute at the clearing price. Matching: price priority, then time priority (you're last in line at any given price).
Guaranteed buyback right after the auction — you don't hold these into continuous trading:
DRYLAND_FLAX: Merchant Guild buys back at 30 per unit, no fees.
EMBER_MUSHROOM: Merchant Guild buys back at 20 per unit, with a 0.10 per unit fee.
Strategy: your payoff per unit is (buyback_price − your_bid_price − fee), but higher bids → more volume allocated to you. It's a classic "bid high enough to win allocation but not so high you erode margin" problem. Since you're last at any price level, you need to set a price that is above the level of any competitors you expect to outrank for the quantity you want.
Rounds 2–5: not yet posted on the wiki (typical for IMC — each is unlocked at round start).
Practical Implications for Your Code
From looking at the model and your repo (tradingalgemerald_v1.py, Round 1 data under imcround1/ROUND1/):

PEPPER_ROOT: likely a market-making / fair-value strategy — quote around the stable mid, fade when price deviates, manage position near 0. This is the same template as EMERALDS in the tutorial.
ASH_COATED_OSMIUM: the wiki hint says "apparent unpredictability may follow a hidden pattern." That typically means: mean reversion to a moving average, a hidden cyclic component (Fourier / seasonal), or a signal derivable from trade flow. Your visualizations (stock_visualizations_strategy.png) are the right first step — inspect autocorrelation, trade-flow imbalance, and periodicity in the sample-day CSVs.
Respect the 900 ms tick budget — avoid heavy numpy/statsmodels fits inside run(). Precompute or keep rolling windows.
Persist any rolling state via traderData (jsonpickle-serialized), not globals.
Remember the position-limit rejection rule is per-side-aggregate — if you're at position +78/80 and submit two buys of size 5 each, both get rejected, not just one.
What's NOT Yet on the Wiki (but expected from past Prosperity years)

Round 2 Summary

## Overview
Round 2 introduces a competitive bidding mechanism for market access and a strategic investment challenge for your trading outpost. You will continue trading `ASH_COATED_OSMIUM` and `INTARIAN_PEPPER_ROOT` with the same position limits (80 for both).

## Algorithmic Challenge: "Limited Market Access"
The core addition to the algorithmic challenge is the **Market Access Fee (MAF)**.

### The MAF Mechanism
*   **What it is:** A blind auction where you bid a one-time fee (in XIRECs) to gain access to an additional 25% of market quotes.
*   **How to bid:** Implement a `bid(self)` method in your `Trader` class that returns an integer representing your bid amount.
    ```python
    class Trader:
        def bid(self) -> int:
            return 1000 # Example bid
    ```
*   **Winning the auction:** The top 50% of bids across all participants win the extra market access. The threshold is determined by the median bid of all submitted `trader.py` files (missing files or those without a `bid()` function are treated as bidding 0; negative bids are treated as 0).
*   **Cost:** If you are in the top 50%, the amount you bid is subtracted from your Round 2 profits. If you are in the bottom 50%, you pay nothing but only see the standard 80% of quotes.
    *   **Winners:** `Final PnL = Round 2 Trading Profit - Bid Amount`
    *   **Losers:** `Final PnL = Round 2 Trading Profit`
*   **Testing:** During local/sandbox testing, the MAF is ignored, and you will only interact with the default 80% of quotes (which is slightly randomized to prevent overfitting). The actual auction only happens during the final evaluation of the round.
*   **Strategy:** The goal is game-theoretical: bid just enough to be in the top 50% without overpaying, as every XIREC over the threshold directly reduces your final PnL.



## Visualizations Summary (Round 2)
Based on the `round2_visualizations.png` charts for `INTARIAN_PEPPER_ROOT` and `ASH_COATED_OSMIUM`:

### INTARIAN_PEPPER_ROOT
*   **Trend & Scale:** Displays a perfectly linear, deterministic upward trend. The mid-price rises constantly from 11000 up to 14000 over the 30,000 continuous time steps.
*   **Spread Size Distribution:** Bimodal spread distribution. The vast majority of spreads are tightly clustered between 12 and 19 (peaking at 14-15). There is a tiny secondary cluster at very low spreads (2-4).
*   **Cumulative Volume Imbalance:** Generally trends upwards, ending highly positive (~1250 net buying pressure), indicating sustained buying momentum that aligns with the price increase.

### ASH_COATED_OSMIUM
*   **Trend & Scale:** Highly volatile and mean-reverting. The price oscillates rapidly in a tight band, mostly fluctuating between roughly 9980 and 10020. The fast and slow SMAs track this volatility closely, suggesting no long-term directional drift.
*   **Spread Size Distribution:** Extremely tight and consistent spread. The overwhelming majority of spreads are exactly 16. Other spread sizes (17, 18, 19) exist but are a fraction of the dominant 16-tick spread.
*   **Cumulative Volume Imbalance:** Highly oscillatory with wide swings from roughly -400 to +1000. It does not show a clear long-term direction, reinforcing the mean-reverting nature of the product's price.
