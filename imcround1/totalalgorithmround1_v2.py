from datamodel import OrderDepth, TradingState, Order
from typing import List, Dict, Tuple, Optional
import json


PEPPER = "INTARIAN_PEPPER_ROOT"
OSMIUM = "ASH_COATED_OSMIUM"

POSITION_LIMIT = 80

# ---------- PEPPER_ROOT hyperparameters (v2 tuned) ----------
# Empirical drift observed across training days -2, -1, 0:
#   mid rose by ~1,000 per 10,000 ticks, i.e. ~+0.1 per tick of timestamp 100.
# Tick in this context = one run() call (timestamp step = 100).
PEPPER_HISTORY_CAP = 200          # rolling window for OLS slope
PEPPER_SLOPE_PRIOR = 0.10         # fallback slope if history is too short
PEPPER_HORIZON = 10               # v1=5 -> v2=10: price further ahead, reliable drift
PEPPER_MIN_SAMPLES_FOR_OLS = 20   # below this, use the prior
PEPPER_LONG_BIAS = 4              # v1=2 -> v2=4: stronger upward skew on both quotes
PEPPER_TAKE_EDGE = 0              # v1=1 -> v2=0: lift any ask at or below fair
PEPPER_SELL_EDGE = 4              # v2 new: require bid >= fair + 4 before selling
PEPPER_MAKER_BID_OFFSET = 0       # v1=1 -> v2=0: quote bid right at fair + long_bias
PEPPER_MAKER_ASK_OFFSET = 7       # v1=3 -> v2=7: effectively never sell passively

# v2 new: aggressive loading phase -- saturate long quickly to capture drift sooner
PEPPER_LOAD_TARGET = 60           # load up to this position before normal logic
PEPPER_LOAD_PRICE_TOL = 1         # willing to pay up to fair + this while loading


# ---------- OSMIUM hyperparameters (v2 tuned) ----------
OSMIUM_ANCHOR = 10000
OSMIUM_SNAP_TOL = 5               # v1=10 -> v2=5: let EMA reposition fair sooner
OSMIUM_EMA_ALPHA = 0.05
OSMIUM_TAKE_EDGE = 2              # cross when price beats fair by >= 2
OSMIUM_MAKER_OFFSET = 3           # v1=4 -> v2=3: tighter quotes, more fills
OSMIUM_MR_THRESHOLD = 4           # v1=5 -> v2=4: trigger MR lean sooner
OSMIUM_MR_SWEEP = 15              # v2 new: if |mid - fair| >= this, sweep the book
OSMIUM_HISTORY_CAP = 100


class Trader:

    def bid(self):
        # Only used in Round 2. Harmless no-op for Round 1.
        return 15

    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}
        conversions = 0

        data = self._load_state(state.traderData)

        if PEPPER in state.order_depths:
            result[PEPPER] = self._handle_pepper_root(
                state.order_depths[PEPPER],
                state.position.get(PEPPER, 0),
                data,
            )

        if OSMIUM in state.order_depths:
            result[OSMIUM] = self._handle_osmium(
                state.order_depths[OSMIUM],
                state.position.get(OSMIUM, 0),
                data,
            )

        trader_data = json.dumps(data)
        return result, conversions, trader_data

    # ==================================================================
    # Shared helpers
    # ==================================================================
    @staticmethod
    def _load_state(raw: str) -> dict:
        if not raw:
            return {
                "pepper_hist": [],
                "osmium_ema": float(OSMIUM_ANCHOR),
                "osmium_hist": [],
            }
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return {
                "pepper_hist": [],
                "osmium_ema": float(OSMIUM_ANCHOR),
                "osmium_hist": [],
            }
        parsed.setdefault("pepper_hist", [])
        parsed.setdefault("osmium_ema", float(OSMIUM_ANCHOR))
        parsed.setdefault("osmium_hist", [])
        return parsed

    @staticmethod
    def _best_bid_ask(order_depth: OrderDepth) -> Optional[Tuple[int, int]]:
        if not order_depth.buy_orders or not order_depth.sell_orders:
            return None
        best_bid = max(order_depth.buy_orders.keys())
        best_ask = min(order_depth.sell_orders.keys())
        return best_bid, best_ask

    @staticmethod
    def _ols_slope(series: List[float]) -> Optional[float]:
        """Closed-form OLS slope of `series` vs integer time index. O(n)."""
        n = len(series)
        if n < 2:
            return None
        sum_t = n * (n - 1) / 2.0
        sum_t2 = (n - 1) * n * (2 * n - 1) / 6.0
        sum_y = 0.0
        sum_ty = 0.0
        for i, y in enumerate(series):
            sum_y += y
            sum_ty += i * y
        denom = n * sum_t2 - sum_t * sum_t
        if denom <= 0:
            return None
        return (n * sum_ty - sum_t * sum_y) / denom

    # ==================================================================
    # PEPPER_ROOT: drift-aware market making
    # ==================================================================
    def _handle_pepper_root(
        self,
        order_depth: OrderDepth,
        position: int,
        data: dict,
    ) -> List[Order]:
        orders: List[Order] = []

        bba = self._best_bid_ask(order_depth)
        if bba is None:
            return orders
        best_bid, best_ask = bba
        mid = (best_bid + best_ask) / 2.0

        hist: List[float] = data["pepper_hist"]
        hist.append(mid)
        if len(hist) > PEPPER_HISTORY_CAP:
            del hist[: len(hist) - PEPPER_HISTORY_CAP]

        # --- Estimate live drift, with fallback to empirical prior ---
        slope = None
        if len(hist) >= PEPPER_MIN_SAMPLES_FOR_OLS:
            slope = self._ols_slope(hist)
        if slope is None or abs(slope) > 1.0:  # guard against crazy fits early on
            slope = PEPPER_SLOPE_PRIOR

        fair = mid + slope * PEPPER_HORIZON
        fair_int = int(round(fair))

        buy_cap = POSITION_LIMIT - position
        sell_cap = POSITION_LIMIT + position

        # --- v2: Loading boot phase -- saturate long fast while position is low ---
        # Drift is +~0.1/tick, so every tick below max long loses (80 - pos) * 0.1.
        # Pay up to fair + PEPPER_LOAD_PRICE_TOL to reach PEPPER_LOAD_TARGET ASAP.
        if position < PEPPER_LOAD_TARGET:
            need = PEPPER_LOAD_TARGET - position
            load_max_price = fair_int + PEPPER_LOAD_PRICE_TOL
            for ask_price, ask_volume in sorted(order_depth.sell_orders.items()):
                if need <= 0 or buy_cap <= 0:
                    break
                if ask_price <= load_max_price:
                    qty = min(abs(ask_volume), need, buy_cap)
                    if qty > 0:
                        orders.append(Order(PEPPER, ask_price, qty))
                        position += qty
                        buy_cap -= qty
                        need -= qty

        # --- Normal taker pass: free edge vs our forward-looking fair value ---
        for ask_price, ask_volume in sorted(order_depth.sell_orders.items()):
            if buy_cap <= 0:
                break
            if ask_price <= fair_int - PEPPER_TAKE_EDGE:
                qty = min(abs(ask_volume), buy_cap)
                if qty > 0:
                    orders.append(Order(PEPPER, ask_price, qty))
                    position += qty
                    buy_cap -= qty

        # v2: only cross to sell when the bid is very rich vs fair; drift punishes
        # passive shorts, so we want to almost never sell.
        for bid_price, bid_volume in sorted(
            order_depth.buy_orders.items(), reverse=True
        ):
            if sell_cap <= 0:
                break
            if bid_price >= fair_int + PEPPER_SELL_EDGE:
                qty = min(bid_volume, sell_cap)
                if qty > 0:
                    orders.append(Order(PEPPER, bid_price, -qty))
                    position -= qty
                    sell_cap -= qty

        # --- Maker pass: asymmetric quotes skewed long ---
        pos_skew = int(round(4.0 * position / POSITION_LIMIT))

        maker_bid = fair_int - PEPPER_MAKER_BID_OFFSET - pos_skew + PEPPER_LONG_BIAS
        maker_ask = fair_int + PEPPER_MAKER_ASK_OFFSET - pos_skew + PEPPER_LONG_BIAS

        # Never quote through the opposite side of our own book.
        if maker_bid >= maker_ask:
            maker_bid = fair_int - 1
            maker_ask = fair_int + 3

        if buy_cap > 0:
            orders.append(Order(PEPPER, maker_bid, buy_cap))
        if sell_cap > 0:
            orders.append(Order(PEPPER, maker_ask, -sell_cap))

        data["pepper_hist"] = hist
        return orders

    # ==================================================================
    # OSMIUM: stationary mean-reversion MM around ~10,000
    # ==================================================================
    def _handle_osmium(
        self,
        order_depth: OrderDepth,
        position: int,
        data: dict,
    ) -> List[Order]:
        orders: List[Order] = []

        bba = self._best_bid_ask(order_depth)
        if bba is None:
            return orders
        best_bid, best_ask = bba
        mid = (best_bid + best_ask) / 2.0

        # v2: Microprice (volume-weighted) captures order-book imbalance; blend it
        # into the EMA so fair reacts to sided bot flow, not just midpoint.
        bb_vol = order_depth.buy_orders[best_bid]
        ba_vol = abs(order_depth.sell_orders[best_ask])
        total_top = bb_vol + ba_vol
        if total_top > 0:
            micro = (best_ask * bb_vol + best_bid * ba_vol) / total_top
        else:
            micro = mid
        price_signal = 0.5 * mid + 0.5 * micro

        ema = float(data["osmium_ema"])
        ema = OSMIUM_EMA_ALPHA * price_signal + (1.0 - OSMIUM_EMA_ALPHA) * ema
        data["osmium_ema"] = ema

        hist: List[float] = data["osmium_hist"]
        hist.append(mid)
        if len(hist) > OSMIUM_HISTORY_CAP:
            del hist[: len(hist) - OSMIUM_HISTORY_CAP]
        data["osmium_hist"] = hist

        # Snap to anchor inside tolerance; otherwise follow the EMA.
        if abs(ema - OSMIUM_ANCHOR) <= OSMIUM_SNAP_TOL:
            fair = OSMIUM_ANCHOR
        else:
            fair = int(round(ema))

        # Mean-reversion overlay on the maker quotes.
        mr_shift = 0
        deviation = mid - fair
        if deviation > OSMIUM_MR_THRESHOLD:
            # Price is rich -> lean sell (lower quotes).
            mr_shift = -1
        elif deviation < -OSMIUM_MR_THRESHOLD:
            mr_shift = +1

        buy_cap = POSITION_LIMIT - position
        sell_cap = POSITION_LIMIT + position

        # --- Taker pass: grab anything well past fair ---
        for ask_price, ask_volume in sorted(order_depth.sell_orders.items()):
            if buy_cap <= 0:
                break
            if ask_price <= fair - OSMIUM_TAKE_EDGE:
                qty = min(abs(ask_volume), buy_cap)
                if qty > 0:
                    orders.append(Order(OSMIUM, ask_price, qty))
                    position += qty
                    buy_cap -= qty

        for bid_price, bid_volume in sorted(
            order_depth.buy_orders.items(), reverse=True
        ):
            if sell_cap <= 0:
                break
            if bid_price >= fair + OSMIUM_TAKE_EDGE:
                qty = min(bid_volume, sell_cap)
                if qty > 0:
                    orders.append(Order(OSMIUM, bid_price, -qty))
                    position -= qty
                    sell_cap -= qty

        # --- v2: Aggressive mean-reversion sweep on large dislocations ---
        # When mid diverges from fair by >= OSMIUM_MR_SWEEP, walk every level of
        # the offending side up to remaining capacity (not just the top).
        deviation_for_sweep = mid - fair
        if deviation_for_sweep >= OSMIUM_MR_SWEEP and sell_cap > 0:
            for bid_price, bid_volume in sorted(
                order_depth.buy_orders.items(), reverse=True
            ):
                if sell_cap <= 0:
                    break
                if bid_price >= fair:  # anything at or above fair while price is rich
                    qty = min(bid_volume, sell_cap)
                    if qty > 0:
                        orders.append(Order(OSMIUM, bid_price, -qty))
                        position -= qty
                        sell_cap -= qty
        elif -deviation_for_sweep >= OSMIUM_MR_SWEEP and buy_cap > 0:
            for ask_price, ask_volume in sorted(order_depth.sell_orders.items()):
                if buy_cap <= 0:
                    break
                if ask_price <= fair:
                    qty = min(abs(ask_volume), buy_cap)
                    if qty > 0:
                        orders.append(Order(OSMIUM, ask_price, qty))
                        position += qty
                        buy_cap -= qty

        # --- Maker pass: inventory-skewed quotes inside the 16-wide market ---
        pos_skew = int(round(4.0 * position / POSITION_LIMIT))

        maker_bid = fair - OSMIUM_MAKER_OFFSET - pos_skew + mr_shift
        maker_ask = fair + OSMIUM_MAKER_OFFSET - pos_skew + mr_shift

        if maker_bid >= maker_ask:
            maker_bid = fair - 2
            maker_ask = fair + 2

        if buy_cap > 0:
            orders.append(Order(OSMIUM, maker_bid, buy_cap))
        if sell_cap > 0:
            orders.append(Order(OSMIUM, maker_ask, -sell_cap))

        return orders
