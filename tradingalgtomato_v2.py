from datamodel import OrderDepth, TradingState, Order
from typing import List
import json


class Trader:

    def bid(self):
        return 15

    def run(self, state: TradingState):
        result = {}
        conversions = 0

        symbol = "TOMATOES"

        # ==========================================
        # 1. TRADER DATA RESTORATION
        # ==========================================
        if state.traderData == "":
            data = {}
        else:
            data = json.loads(state.traderData)

        slow_ema = data.get("slow_ema", None)
        fast_ema = data.get("fast_ema", None)
        micro_ema = data.get("micro_ema", None)
        vol_ema = data.get("vol_ema", 3.0)
        last_mid = data.get("last_mid", None)
        flow_ema = data.get("flow_ema", 0.0)

        # ==========================================
        # 2. TOMATOES STRATEGY
        # ==========================================
        if symbol in state.order_depths:
            order_depth: OrderDepth = state.order_depths[symbol]
            orders: List[Order] = []

            current_position = state.position.get(symbol, 0)
            position_limit = 80

            if len(order_depth.buy_orders) > 0 and len(order_depth.sell_orders) > 0:
                best_bid = max(order_depth.buy_orders.keys())
                best_ask = min(order_depth.sell_orders.keys())
                best_bid_vol = order_depth.buy_orders[best_bid]
                best_ask_vol = abs(order_depth.sell_orders[best_ask])

                mid_price = (best_bid + best_ask) / 2.0
                spread = best_ask - best_bid

                total_top_vol = best_bid_vol + best_ask_vol
                micro_price = (
                    (best_ask * best_bid_vol + best_bid * best_ask_vol) / total_top_vol
                    if total_top_vol > 0 else mid_price
                )

                imbalance = (best_bid_vol - best_ask_vol) / max(1, total_top_vol)

                # ------------------------------------------
                # Parameters
                # ------------------------------------------
                SLOW_ALPHA = 0.06
                FAST_ALPHA = 0.18
                MICRO_ALPHA = 0.30
                VOL_ALPHA = 0.12
                FLOW_ALPHA = 0.25

                INVENTORY_SKEW = 0.18

                MR_ENTRY = 1.5
                MR_FULL = 4.0
                MR_MAX_POS = 30

                TAKER_EDGE = 1
                TAKER_MR_EXTRA = 1

                MM_SKEW_THRESHOLD = 15

                SEC_DEPTH = 3
                SEC_SIZE = 10

                # ------------------------------------------
                # Update state estimates
                # ------------------------------------------
                if slow_ema is None:
                    slow_ema = mid_price
                else:
                    slow_ema = SLOW_ALPHA * mid_price + (1 - SLOW_ALPHA) * slow_ema

                if fast_ema is None:
                    fast_ema = mid_price
                else:
                    fast_ema = FAST_ALPHA * mid_price + (1 - FAST_ALPHA) * fast_ema

                if micro_ema is None:
                    micro_ema = micro_price
                else:
                    micro_ema = MICRO_ALPHA * micro_price + (1 - MICRO_ALPHA) * micro_ema

                if last_mid is not None:
                    vol_ema = VOL_ALPHA * abs(mid_price - last_mid) + (1 - VOL_ALPHA) * vol_ema

                last_mid = mid_price

                # ------------------------------------------
                # [APPROACH 3] Trade-flow signal from market_trades
                # ------------------------------------------
                net_flow = 0
                if symbol in state.market_trades:
                    for trade in state.market_trades[symbol]:
                        if trade.price >= best_ask:
                            net_flow += trade.quantity
                        elif trade.price <= best_bid:
                            net_flow -= trade.quantity
                        elif trade.price > mid_price:
                            net_flow += trade.quantity * 0.5
                        else:
                            net_flow -= trade.quantity * 0.5

                flow_ema = FLOW_ALPHA * net_flow + (1 - FLOW_ALPHA) * flow_ema

                # ------------------------------------------
                # Fair value estimation
                # ------------------------------------------
                volatility = max(1.0, vol_ema)

                flow_adjustment = 0.15 * flow_ema / max(1.0, volatility)
                model_fair = (
                    0.35 * fast_ema + 0.40 * micro_ema + 0.25 * slow_ema
                    + flow_adjustment
                )

                adjusted_fair = model_fair - INVENTORY_SKEW * current_position
                fair_price = int(round(adjusted_fair))

                # ------------------------------------------
                # [APPROACH 2] Volatility-adaptive MM parameters
                # ------------------------------------------
                vol_ratio = vol_ema / 3.0
                adaptive_offset = max(1, int(round(vol_ratio * 1.5)))
                adaptive_size = max(15, int(round(35 / max(0.5, vol_ratio))))

                # ------------------------------------------
                # Mean-reversion signal & target position
                # ------------------------------------------
                deviation = (mid_price - slow_ema) / volatility

                trend_strength = abs(fast_ema - slow_ema) / volatility
                trend_dampen = max(0.3, 1.0 - 0.3 * max(0.0, trend_strength - 1.0))

                # ------------------------------------------
                # [APPROACH 1] Imbalance-gated MR entry threshold
                # ------------------------------------------
                if deviation < 0 and imbalance > 0.1:
                    effective_entry = MR_ENTRY * 0.75
                elif deviation > 0 and imbalance < -0.1:
                    effective_entry = MR_ENTRY * 0.75
                elif (deviation < 0 and imbalance < -0.2) or (deviation > 0 and imbalance > 0.2):
                    effective_entry = MR_ENTRY * 1.3
                else:
                    effective_entry = MR_ENTRY

                if abs(deviation) < effective_entry:
                    mr_target = 0
                else:
                    excess = abs(deviation) - effective_entry
                    scale = min(1.0, excess / (MR_FULL - effective_entry))
                    mr_target = int(scale * MR_MAX_POS) * (-1 if deviation > 0 else 1)
                    mr_target = int(mr_target * trend_dampen)

                # ------------------------------------------
                # Taker: take mispriced orders & work toward target
                # ------------------------------------------
                position_gap = mr_target - current_position

                if position_gap > 0:
                    urgency = min(1.0, position_gap / MR_MAX_POS)
                    edge = TAKER_EDGE + int(urgency * TAKER_MR_EXTRA)
                    max_buy_price = fair_price + edge
                    remaining = min(position_gap, position_limit - current_position)

                    for ask_p, ask_v in sorted(order_depth.sell_orders.items()):
                        if remaining <= 0:
                            break
                        if ask_p <= max_buy_price:
                            qty = min(abs(ask_v), remaining)
                            if qty > 0:
                                orders.append(Order(symbol, ask_p, qty))
                                current_position += qty
                                remaining -= qty

                elif position_gap < 0:
                    urgency = min(1.0, -position_gap / MR_MAX_POS)
                    edge = TAKER_EDGE + int(urgency * TAKER_MR_EXTRA)
                    min_sell_price = fair_price - edge
                    remaining = min(-position_gap, position_limit + current_position)

                    for bid_p, bid_v in sorted(order_depth.buy_orders.items(), reverse=True):
                        if remaining <= 0:
                            break
                        if bid_p >= min_sell_price:
                            qty = min(bid_v, remaining)
                            if qty > 0:
                                orders.append(Order(symbol, bid_p, -qty))
                                current_position -= qty
                                remaining -= qty

                else:
                    for ask_p, ask_v in sorted(order_depth.sell_orders.items()):
                        cap = position_limit - current_position
                        if cap <= 0:
                            break
                        if ask_p <= fair_price - TAKER_EDGE:
                            qty = min(abs(ask_v), cap)
                            if qty > 0:
                                orders.append(Order(symbol, ask_p, qty))
                                current_position += qty

                    for bid_p, bid_v in sorted(order_depth.buy_orders.items(), reverse=True):
                        cap = position_limit + current_position
                        if cap <= 0:
                            break
                        if bid_p >= fair_price + TAKER_EDGE:
                            qty = min(bid_v, cap)
                            if qty > 0:
                                orders.append(Order(symbol, bid_p, -qty))
                                current_position -= qty

                # ------------------------------------------
                # Maker: two-sided liquidity inside the spread
                # ------------------------------------------
                buy_capacity = position_limit - current_position
                sell_capacity = position_limit + current_position

                maker_bid = min(best_bid + 1, fair_price - adaptive_offset)
                maker_ask = max(best_ask - 1, fair_price + adaptive_offset)

                if maker_bid >= maker_ask:
                    maker_bid = fair_price - 2
                    maker_ask = fair_price + 2

                buy_size = min(adaptive_size, buy_capacity)
                sell_size = min(adaptive_size, sell_capacity)

                if current_position > MM_SKEW_THRESHOLD:
                    over = current_position - MM_SKEW_THRESHOLD
                    buy_size = max(2, buy_size - over)
                    sell_size = min(sell_capacity, sell_size + over // 2)
                elif current_position < -MM_SKEW_THRESHOLD:
                    over = -current_position - MM_SKEW_THRESHOLD
                    sell_size = max(2, sell_size - over)
                    buy_size = min(buy_capacity, buy_size + over // 2)

                if mr_target > current_position:
                    buy_size = min(buy_capacity, buy_size + 5)
                elif mr_target < current_position:
                    sell_size = min(sell_capacity, sell_size + 5)

                # [APPROACH 3] Flow-based size bias
                if flow_ema > 5:
                    buy_size = min(buy_capacity, buy_size + 3)
                    sell_size = max(2, sell_size - 3)
                elif flow_ema < -5:
                    sell_size = min(sell_capacity, sell_size + 3)
                    buy_size = max(2, buy_size - 3)

                prim_buy = min(buy_size, buy_capacity)
                prim_sell = min(sell_size, sell_capacity)

                if prim_buy > 0:
                    orders.append(Order(symbol, maker_bid, prim_buy))
                if prim_sell > 0:
                    orders.append(Order(symbol, maker_ask, -prim_sell))

                rem_buy = buy_capacity - prim_buy
                rem_sell = sell_capacity - prim_sell

                sec_buy = min(SEC_SIZE, rem_buy)
                sec_sell = min(SEC_SIZE, rem_sell)

                if sec_buy > 0:
                    orders.append(Order(symbol, maker_bid - SEC_DEPTH, sec_buy))
                if sec_sell > 0:
                    orders.append(Order(symbol, maker_ask + SEC_DEPTH, -sec_sell))

            result[symbol] = orders

        # ==========================================
        # 3. SAVE STATE
        # ==========================================
        data["slow_ema"] = slow_ema
        data["fast_ema"] = fast_ema
        data["micro_ema"] = micro_ema
        data["vol_ema"] = vol_ema
        data["last_mid"] = last_mid
        data["flow_ema"] = flow_ema

        traderData = json.dumps(data)

        return result, conversions, traderData
