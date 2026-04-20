MY GOAL IS TO WIN IMC PROSPERITY CHALLENE BY IMPELEMENTING THE BEST ALGORITHM. 

As an agent, you will always reference the rules and visualizations. 

rules wiki link: https://imc-prosperity.notion.site/prosperity-4-wiki

LOOK AT STOCK VISUALIZATIONS inside round 1 folder (the .png) TO SEE THE PRICE AND VOLUME TRENDS VISUALLY
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
Rounds 2–5 pages exist as links on the main wiki but are gated until each round opens. Based on Prosperity 3, you should expect later rounds to introduce:

Options / derivatives pricing (Black-Scholes-ish products).
Pair-tradable basket products (e.g., "picnic basket" = weighted sum of other products).
Cross-exchange arbitrage via the conversion mechanism (hence the ConversionObservation fields for tariffs/transport).
Weather/observation-driven products (the sunlight/humidity fields in ConversionObservation are the giveaway).
If you'd like, I can now:

Pull the code in tradingalgemerald_v1.py and sanity-check it against the wiki's API spec.
Analyze the Round 1 CSVs to find the "hidden pattern" in ASH_COATED_OSMIUM (equivalent, for your files, to checking the historical Round 1 data's second product).
Draft a starter Trader class tuned to the position-limit and time-budget constraints above.

