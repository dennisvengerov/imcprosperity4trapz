from datamodel import OrderDepth, TradingState, Order
from typing import List, Dict, Tuple, Optional
import json


PEPPER = "INTARIAN_PEPPER_ROOT"
OSMIUM = "ASH_COATED_OSMIUM"

POSITION_LIMIT = 80

# ---------- PEPPER_ROOT hyperparameters ----------
# Empirical drift observed across training days -2, -1, 0:
#   mid rose by ~1,000 per 10,000 ticks, i.e. ~+0.1 per tick of timestamp 100.
# Tick in this context = one run() call (timestamp step = 100).
PEPPER_HISTORY_CAP = 200          # rolling window for OLS slope
PEPPER_SLOPE_PRIOR = 0.10         # fallback slope if history is too short
PEPPER_HORIZON = 5                # how many ticks ahead we price to
PEPPER_MIN_SAMPLES_FOR_OLS = 20   # below this, use the prior
PEPPER_LONG_BIAS = 2              # structural quote-skew toward long side
PEPPER_TAKE_EDGE = 1              # cross the book when price <= fair - edge
PEPPER_MAKER_BID_OFFSET = 1
PEPPER_MAKER_ASK_OFFSET = 3       # asymmetric: drift punishes passive shorts


# ---------- OSMIUM hyperparameters ----------
OSMIUM_ANCHOR = 10000
OSMIUM_SNAP_TOL = 10              # snap fair to anchor when |ema-anchor| <= tol
OSMIUM_EMA_ALPHA = 0.05
OSMIUM_TAKE_EDGE = 2              # cross when price beats fair by >= 2
OSMIUM_MAKER_OFFSET = 4           # quote at fair +/- 4 (well inside 16-wide market)
OSMIUM_MR_THRESHOLD = 5           # deviation from fair that triggers MR overlay
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

        # --- Taker pass: free edge vs our forward-looking fair value ---
        for ask_price, ask_volume in sorted(order_depth.sell_orders.items()):
            if buy_cap <= 0:
                break
            if ask_price <= fair_int - PEPPER_TAKE_EDGE:
                qty = min(abs(ask_volume), buy_cap)
                if qty > 0:
                    orders.append(Order(PEPPER, ask_price, qty))
                    position += qty
                    buy_cap -= qty

        for bid_price, bid_volume in sorted(
            order_depth.buy_orders.items(), reverse=True
        ):
            if sell_cap <= 0:
                break
            # Hitting bids is adverse to +drift; require a wider edge on sells.
            if bid_price >= fair_int + PEPPER_TAKE_EDGE + 1:
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

        ema = float(data["osmium_ema"])
        ema = OSMIUM_EMA_ALPHA * mid + (1.0 - OSMIUM_EMA_ALPHA) * ema
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
