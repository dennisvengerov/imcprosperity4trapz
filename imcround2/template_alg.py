from datamodel import OrderDepth, TradingState, Order
from typing import List, Dict, Tuple, Optional
import json


PEPPER = "INTARIAN_PEPPER_ROOT"
OSMIUM = "ASH_COATED_OSMIUM"

POSITION_LIMIT = 80


# ==================================================================
# PEPPER_ROOT (v3) — deterministic-drift accumulator
# ==================================================================
# Drift is locked at +0.1 mid per timestamp unit (verified identical on all
# three training days). We do NOT estimate slope from data. We only track the
# intercept c in the model:   fair(ts) = c + 0.1 * ts
#
# Welford-style running mean of c, saturating at PEPPER_C_WINDOW samples so
# late-session ticks don't get drowned out and early-session ticks converge
# fast (intercept noise after N samples ~ 2.5 / sqrt(N)).
PEPPER_SLOPE = 0.1
PEPPER_C_WINDOW = 500

# Forward look for pricing decisions (in timestamp units, 100 = 1 tick).
PEPPER_TAKE_HORIZON = 200            # 2 ticks ahead for takers (tiny forward edge)
PEPPER_QUOTE_HORIZON = 0             # current fair for maker quote anchor

# Saturate the long position. 75 (not 80) leaves a safety cushion for the
# passive bid to keep filling without tripping the 80-cap aggregate rule.
PEPPER_LOAD_TARGET = 75

# Tiered overpay while loading. Unit bought at fair + K recoups in K/0.1
# = 10K timestamp-units (= K/10 ticks), tiny vs the remaining session.
# Earlier == more valuable because drift hasn't accrued yet.
PEPPER_OVERPAY_TIERS: List[Tuple[int, int]] = [
    (20, 5),    # pos <  20 -> pay up to fair + 5
    (50, 3),    # pos <  50 -> pay up to fair + 3
    (70, 2),    # pos <  70 -> pay up to fair + 2
    (75, 1),    # pos <  75 -> pay up to fair + 1
]

# Opportunistic sell: only cross to sell when the bid is substantially rich
# vs forward fair. We plan to re-buy within a few ticks at fair, so required
# edge >> drift-loss-per-tick.
PEPPER_OPP_SELL_EDGE = 5
PEPPER_OPP_SELL_MAX = 10             # cap per tick so one spike doesn't unwind us

# Maker bid: sit above best_bid, below best_ask, biased upward toward future
# drift. Clamp below best_ask - 1 so it never accidentally crosses.
PEPPER_LONG_BIAS = 4                 # shift the maker bid up by this many units
PEPPER_MAKER_POS_SKEW_MAX = 4        # |skew| cap when position near ±80

# "At-limit" ask harvesting: when pos == 80 and best bid is rich, drop a tiny
# ask at fair_fwd + 4 to catch any bot that will lift it. Very small size to
# avoid giving up long exposure en masse.
PEPPER_FULL_ASK_EDGE = 4
PEPPER_FULL_ASK_SIZE = 6


# ==================================================================
# OSMIUM (v3) — tight MM inside the 16-wide book, fast MR
# ==================================================================
OSMIUM_ANCHOR = 10000
OSMIUM_SNAP_TOL = 3                  # v2=5 -> v3=3: reanchor EMA faster
OSMIUM_EMA_ALPHA = 0.05
OSMIUM_TAKE_EDGE = 2                 # take any ask <= fair-2 / bid >= fair+2
OSMIUM_MAKER_OFFSET = 2              # v2=3 -> v3=2: one tick tighter inside 16-wide
OSMIUM_MR_THRESHOLD = 4              # maker one-unit skew when |dev| > 4
OSMIUM_MR_SWEEP = 8                  # v2=15 -> v3=8: sweep earlier (1.6σ)
OSMIUM_MR_SWEEP_PER_LEVEL = 15       # cap per price level during a sweep
OSMIUM_HEAVY_INV = 60                # |pos| > 60 -> widen aggressive side by 1


# ==================================================================
# MAF bid for Round 2 (see round2strat.md §4)
# Point estimate of expected uplift ~4k; bid strictly below that with margin.
# ==================================================================
MAF_BID_AMOUNT = 2500


class Trader:

    def bid(self) -> int:
        """Round-2 Market Access Fee auction bid (XIRECs)."""
        return MAF_BID_AMOUNT

    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}
        conversions = 0

        data = self._load_state(state.traderData)

        if PEPPER in state.order_depths:
            result[PEPPER] = self._handle_pepper(
                state.order_depths[PEPPER],
                state.position.get(PEPPER, 0),
                state.timestamp,
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
        defaults = {
            "pep_c": None,        # running intercept estimate (float)
            "pep_n": 0,           # samples seen
            "osm_ema": float(OSMIUM_ANCHOR),
        }
        if not raw:
            return defaults
        try:
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                return defaults
        except (ValueError, TypeError):
            return defaults
        for k, v in defaults.items():
            parsed.setdefault(k, v)
        return parsed

    @staticmethod
    def _best_bid_ask(order_depth: OrderDepth) -> Optional[Tuple[int, int]]:
        if not order_depth.buy_orders or not order_depth.sell_orders:
            return None
        return max(order_depth.buy_orders.keys()), min(order_depth.sell_orders.keys())

    @staticmethod
    def _overpay_for_pos(position: int) -> int:
        """Returns the overpay tolerance (XIRECs above fair) while loading."""
        for threshold, overpay in PEPPER_OVERPAY_TIERS:
            if position < threshold:
                return overpay
        return 0

    # ==================================================================
    # PEPPER_ROOT
    # ==================================================================
    def _handle_pepper(
        self,
        order_depth: OrderDepth,
        position: int,
        timestamp: int,
        data: dict,
    ) -> List[Order]:
        orders: List[Order] = []

        bba = self._best_bid_ask(order_depth)
        if bba is None:
            # One-sided book (e.g. thinned MAF feed) — skip this tick safely.
            return orders
        best_bid, best_ask = bba
        mid = (best_bid + best_ask) / 2.0

        # --- Update the locked-slope intercept estimate via Welford mean ---
        c_t = mid - PEPPER_SLOPE * timestamp
        c_hat = data.get("pep_c")
        n = int(data.get("pep_n", 0) or 0)
        if c_hat is None:
            c_hat = float(c_t)
            n = 1
        else:
            n = min(n + 1, PEPPER_C_WINDOW)
            c_hat = c_hat + (c_t - c_hat) / n
        data["pep_c"] = float(c_hat)
        data["pep_n"] = n

        fair_now = int(round(c_hat + PEPPER_SLOPE * timestamp))
        fair_fwd = int(round(c_hat + PEPPER_SLOPE * (timestamp + PEPPER_TAKE_HORIZON)))

        buy_cap = POSITION_LIMIT - position
        sell_cap = POSITION_LIMIT + position

        # ---- Phase A: aggressive load toward LOAD_TARGET ----
        if position < PEPPER_LOAD_TARGET and buy_cap > 0:
            overpay = self._overpay_for_pos(position)
            need = PEPPER_LOAD_TARGET - position
            max_price = fair_fwd + overpay
            for ask_price, ask_volume in sorted(order_depth.sell_orders.items()):
                if need <= 0 or buy_cap <= 0:
                    break
                if ask_price <= max_price:
                    qty = min(abs(ask_volume), need, buy_cap)
                    if qty > 0:
                        orders.append(Order(PEPPER, ask_price, qty))
                        position += qty
                        buy_cap -= qty
                        need -= qty

        # ---- Phase B: free-edge taker (grab any ask at or below fair) ----
        # Any ask at/below fair_now is positive-EV (drift will push mid above it).
        for ask_price, ask_volume in sorted(order_depth.sell_orders.items()):
            if buy_cap <= 0:
                break
            if ask_price <= fair_now:
                qty = min(abs(ask_volume), buy_cap)
                if qty > 0:
                    orders.append(Order(PEPPER, ask_price, qty))
                    position += qty
                    buy_cap -= qty

        # ---- Phase C: opportunistic taker-sell (only on rich bids) ----
        # Sell only if bid >= fair_fwd + PEPPER_OPP_SELL_EDGE, capped in size.
        opp_sold = 0
        for bid_price, bid_volume in sorted(
            order_depth.buy_orders.items(), reverse=True
        ):
            if sell_cap <= 0 or opp_sold >= PEPPER_OPP_SELL_MAX:
                break
            if bid_price >= fair_fwd + PEPPER_OPP_SELL_EDGE:
                qty = min(bid_volume, sell_cap, PEPPER_OPP_SELL_MAX - opp_sold)
                if qty > 0:
                    orders.append(Order(PEPPER, bid_price, -qty))
                    position -= qty
                    sell_cap -= qty
                    opp_sold += qty

        # ---- Phase D: maker bid (above best_bid, below best_ask) ----
        pos_skew = max(
            -PEPPER_MAKER_POS_SKEW_MAX,
            min(PEPPER_MAKER_POS_SKEW_MAX, int(round(4.0 * position / POSITION_LIMIT))),
        )
        raw_bid = fair_now + PEPPER_LONG_BIAS - pos_skew
        maker_bid = min(raw_bid, best_ask - 1)
        if buy_cap > 0 and maker_bid > 0:
            orders.append(Order(PEPPER, maker_bid, buy_cap))

        # ---- Phase E: "at-limit" ask harvest (only when fully long) ----
        # Post a small ask only when we can't buy any more anyway AND the
        # current top-of-book bid is rich (signals more rich bids likely).
        if position >= POSITION_LIMIT - 1 and sell_cap > 0:
            if best_bid >= fair_fwd + PEPPER_OPP_SELL_EDGE - 2:
                harvest_ask = fair_fwd + PEPPER_FULL_ASK_EDGE
                qty = min(PEPPER_FULL_ASK_SIZE, sell_cap)
                if qty > 0:
                    orders.append(Order(PEPPER, harvest_ask, -qty))
                    sell_cap -= qty

        return orders

    # ==================================================================
    # OSMIUM
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

        # Microprice blend: weight each side by the opposite-side size so that
        # heavy-sided books pull fair toward the thin side.
        bb_vol = order_depth.buy_orders[best_bid]
        ba_vol = abs(order_depth.sell_orders[best_ask])
        total_top = bb_vol + ba_vol
        if total_top > 0:
            micro = (best_ask * bb_vol + best_bid * ba_vol) / total_top
        else:
            micro = mid
        price_signal = 0.5 * mid + 0.5 * micro

        ema = float(data.get("osm_ema", OSMIUM_ANCHOR))
        ema = OSMIUM_EMA_ALPHA * price_signal + (1.0 - OSMIUM_EMA_ALPHA) * ema
        data["osm_ema"] = ema

        # Snap to the 10,000 anchor inside the (tightened) tolerance.
        if abs(ema - OSMIUM_ANCHOR) <= OSMIUM_SNAP_TOL:
            fair = OSMIUM_ANCHOR
        else:
            fair = int(round(ema))

        deviation = mid - fair
        if deviation > OSMIUM_MR_THRESHOLD:
            mr_shift = -1
        elif deviation < -OSMIUM_MR_THRESHOLD:
            mr_shift = +1
        else:
            mr_shift = 0

        buy_cap = POSITION_LIMIT - position
        sell_cap = POSITION_LIMIT + position

        # ---- Phase A: free-edge taker ----
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

        # ---- Phase B: MR sweep on large dislocations ----
        if deviation >= OSMIUM_MR_SWEEP and sell_cap > 0:
            for bid_price, bid_volume in sorted(
                order_depth.buy_orders.items(), reverse=True
            ):
                if sell_cap <= 0:
                    break
                if bid_price >= fair:
                    qty = min(bid_volume, sell_cap, OSMIUM_MR_SWEEP_PER_LEVEL)
                    if qty > 0:
                        orders.append(Order(OSMIUM, bid_price, -qty))
                        position -= qty
                        sell_cap -= qty
        elif -deviation >= OSMIUM_MR_SWEEP and buy_cap > 0:
            for ask_price, ask_volume in sorted(order_depth.sell_orders.items()):
                if buy_cap <= 0:
                    break
                if ask_price <= fair:
                    qty = min(abs(ask_volume), buy_cap, OSMIUM_MR_SWEEP_PER_LEVEL)
                    if qty > 0:
                        orders.append(Order(OSMIUM, ask_price, qty))
                        position += qty
                        buy_cap -= qty

        # ---- Phase C: maker quotes, skewed by position and deviation ----
        pos_skew = int(round(4.0 * position / POSITION_LIMIT))
        base = OSMIUM_MAKER_OFFSET
        # Widen the aggressive side when heavily inventoried to slow further loads.
        bid_offset = base + (1 if position > OSMIUM_HEAVY_INV else 0)
        ask_offset = base + (1 if position < -OSMIUM_HEAVY_INV else 0)

        maker_bid = fair - bid_offset - pos_skew + mr_shift
        maker_ask = fair + ask_offset - pos_skew + mr_shift

        # Ensure quotes don't cross each other or the visible book.
        if maker_bid >= maker_ask:
            maker_bid, maker_ask = fair - 2, fair + 2
        maker_bid = min(maker_bid, best_ask - 1)
        maker_ask = max(maker_ask, best_bid + 1)

        if buy_cap > 0 and maker_bid > 0:
            orders.append(Order(OSMIUM, maker_bid, buy_cap))
        if sell_cap > 0:
            orders.append(Order(OSMIUM, maker_ask, -sell_cap))

        return orders
