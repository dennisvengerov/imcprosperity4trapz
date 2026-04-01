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

                # ------------------------------------------
                # Multi-level order book micro-price & imbalance
                # ------------------------------------------
                sorted_bids = sorted(order_depth.buy_orders.items(), reverse=True)
                sorted_asks = sorted(order_depth.sell_orders.items())

                weighted_bid_pv = 0.0
                weighted_ask_pv = 0.0
                total_bid_weight = 0.0
                total_ask_weight = 0.0

                for level, (bp, bv) in enumerate(sorted_bids):
                    w = 1.0 / (level + 1)
                    weighted_bid_pv += bp * bv * w
                    total_bid_weight += bv * w

                for level, (ap, av) in enumerate(sorted_asks):
                    w = 1.0 / (level + 1)
                    weighted_ask_pv += ap * abs(av) * w
                    total_ask_weight += abs(av) * w

                if total_bid_weight > 0 and total_ask_weight > 0:
                    vw_bid = weighted_bid_pv / total_bid_weight
                    vw_ask = weighted_ask_pv / total_ask_weight
                    micro_price = (vw_ask * total_bid_weight + vw_bid * total_ask_weight) / (total_bid_weight + total_ask_weight)
                else:
                    micro_price = mid_price

                imbalance = (total_bid_weight - total_ask_weight) / max(1.0, total_bid_weight + total_ask_weight)

                # ------------------------------------------
                # Parameters
                # ------------------------------------------
                SLOW_ALPHA = 0.10
                FAST_ALPHA = 0.18
                MICRO_ALPHA = 0.30
                VOL_ALPHA = 0.12
                FLOW_ALPHA = 0.25

                INV_SKEW_LINEAR = 0.10
                INV_SKEW_QUAD = 0.003

                MR_ENTRY = 1.5
                MR_FULL = 4.0
                MR_MAX_POS = 55

                MM_SIZE = 40
                MM_SKEW_THRESHOLD = 10

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
                # Trade-flow signal from market_trades
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
                # Fair value (float, quadratic inventory penalty)
                # ------------------------------------------
                volatility = max(1.0, vol_ema)

                flow_adjustment = 0.15 * flow_ema / max(1.0, volatility)
                model_fair = (
                    0.35 * fast_ema + 0.40 * micro_ema + 0.25 * slow_ema
                    + flow_adjustment
                )

                inv_penalty = INV_SKEW_LINEAR * current_position + INV_SKEW_QUAD * current_position * abs(current_position)
                fair_value = model_fair - inv_penalty
                fair_int = int(round(fair_value))

                # ------------------------------------------
                # Mean-reversion signal & target position
                # ------------------------------------------
                deviation = (mid_price - slow_ema) / volatility

                trend_strength = abs(fast_ema - slow_ema) / volatility
                trend_dampen = max(0.3, 1.0 - 0.3 * max(0.0, trend_strength - 1.0))

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
                # Taker: sweep mispriced book levels
                # ------------------------------------------
                for ask_p, ask_v in sorted(order_depth.sell_orders.items()):
                    cap = position_limit - current_position
                    if cap <= 0:
                        break
                    if ask_p < fair_int:
                        qty = min(abs(ask_v), cap)
                        if qty > 0:
                            orders.append(Order(symbol, ask_p, qty))
                            current_position += qty

                for bid_p, bid_v in sorted(order_depth.buy_orders.items(), reverse=True):
                    cap = position_limit + current_position
                    if cap <= 0:
                        break
                    if bid_p > fair_int:
                        qty = min(bid_v, cap)
                        if qty > 0:
                            orders.append(Order(symbol, bid_p, -qty))
                            current_position -= qty

                # ------------------------------------------
                # BBO-anchored maker quotes with imbalance skew
                # ------------------------------------------
                buy_capacity = position_limit - current_position
                sell_capacity = position_limit + current_position

                maker_bid = min(best_bid + 1, fair_int - 1)
                maker_ask = max(best_ask - 1, fair_int + 1)

                if imbalance > 0.15:
                    maker_bid = min(maker_bid + 1, best_ask - 2)
                elif imbalance < -0.15:
                    maker_ask = max(maker_ask - 1, best_bid + 2)

                if maker_bid >= maker_ask:
                    maker_bid = fair_int - 2
                    maker_ask = fair_int + 2

                buy_size = min(MM_SIZE, buy_capacity)
                sell_size = min(MM_SIZE, sell_capacity)

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

                if flow_ema > 5:
                    buy_size = min(buy_capacity, buy_size + 3)
                    sell_size = max(2, sell_size - 3)
                elif flow_ema < -5:
                    sell_size = min(sell_capacity, sell_size + 3)
                    buy_size = max(2, buy_size - 3)

                # Tier 1: primary quotes at BBO+1 / BBO-1
                t1_buy = min(buy_size, buy_capacity)
                t1_sell = min(sell_size, sell_capacity)

                if t1_buy > 0:
                    orders.append(Order(symbol, maker_bid, t1_buy))
                if t1_sell > 0:
                    orders.append(Order(symbol, maker_ask, -t1_sell))

                rem_buy = buy_capacity - t1_buy
                rem_sell = sell_capacity - t1_sell

                # Tier 2: deeper inside spread when spread is wide
                if spread > 8 and rem_buy > 0 and rem_sell > 0:
                    t2_bid = maker_bid + 1
                    t2_ask = maker_ask - 1
                    if t2_bid < t2_ask:
                        t2_size = min(15, rem_buy, rem_sell)
                        if t2_size > 0:
                            orders.append(Order(symbol, t2_bid, t2_size))
                            orders.append(Order(symbol, t2_ask, -t2_size))
                            rem_buy -= t2_size
                            rem_sell -= t2_size

                # Tier 3: safety net behind BBO
                t3_buy = min(10, rem_buy)
                t3_sell = min(10, rem_sell)

                if t3_buy > 0:
                    orders.append(Order(symbol, best_bid - 1, t3_buy))
                if t3_sell > 0:
                    orders.append(Order(symbol, best_ask + 1, -t3_sell))

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
