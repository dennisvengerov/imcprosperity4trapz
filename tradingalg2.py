import json
import math
from typing import Any, Dict, List, Optional, Tuple

from datamodel import Order, OrderDepth, TradingState


class Trader:
    symbol = "TOMATOES"
    position_limit = 80

    maximum_mid_history_length = 40
    maximum_absolute_move_history_length = 20

    microprice_weight = 0.75
    short_reversion_weight = 0.55
    long_reversion_weight = 0.10
    inventory_skew_at_full = 6.0

    alpha_to_lean = 0.75
    alpha_to_turn_off_opposite_side = 1.75
    minimum_snipe_edge = 3.2
    emergency_inventory_fraction = 0.80

    base_passive_size = 12
    strong_passive_size = 24
    base_taker_size = 12
    strong_taker_size = 24

    def _initial_symbol_state(self) -> Dict[str, Any]:
        return {
            "mid_history": [],
            "absolute_move_history": [],
            "last_mid_price": None,
        }

    def _load_state(self, trader_data: str) -> Dict[str, Any]:
        if trader_data == "":
            return {self.symbol: self._initial_symbol_state()}
        try:
            loaded_state = json.loads(trader_data)
            if self.symbol not in loaded_state:
                loaded_state[self.symbol] = self._initial_symbol_state()
            return loaded_state
        except Exception:
            return {self.symbol: self._initial_symbol_state()}

    def _clip_price(self, raw_price: int, minimum_price: int, maximum_price: int) -> int:
        if minimum_price > maximum_price:
            return raw_price
        return max(minimum_price, min(maximum_price, raw_price))

    def _best_prices(
        self, order_depth: OrderDepth
    ) -> Tuple[Optional[int], Optional[int], int, int]:
        best_bid_price = max(order_depth.buy_orders.keys()) if order_depth.buy_orders else None
        best_ask_price = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else None

        best_bid_volume = order_depth.buy_orders[best_bid_price] if best_bid_price is not None else 0
        best_ask_volume = abs(order_depth.sell_orders[best_ask_price]) if best_ask_price is not None else 0

        return best_bid_price, best_ask_price, best_bid_volume, best_ask_volume

    def _mid_price(
        self,
        best_bid_price: Optional[int],
        best_ask_price: Optional[int],
        last_mid_price: Optional[float],
    ) -> float:
        if best_bid_price is not None and best_ask_price is not None:
            return (best_bid_price + best_ask_price) / 2.0
        if best_bid_price is not None:
            return float(best_bid_price)
        if best_ask_price is not None:
            return float(best_ask_price)
        if last_mid_price is not None:
            return float(last_mid_price)
        return 5000.0

    def _microprice(
        self,
        best_bid_price: Optional[int],
        best_ask_price: Optional[int],
        best_bid_volume: int,
        best_ask_volume: int,
        fallback_mid_price: float,
    ) -> float:
        if (
            best_bid_price is not None
            and best_ask_price is not None
            and (best_bid_volume + best_ask_volume) > 0
        ):
            return (
                (best_ask_price * best_bid_volume) + (best_bid_price * best_ask_volume)
            ) / (best_bid_volume + best_ask_volume)
        return fallback_mid_price

    def _mean_of_last_values(
        self, values: List[float], window_length: int, fallback_value: float
    ) -> float:
        if not values:
            return fallback_value
        relevant_values = values[-window_length:]
        return sum(relevant_values) / len(relevant_values)

    def _update_state(self, symbol_state: Dict[str, Any], current_mid_price: float) -> None:
        last_mid_price = symbol_state["last_mid_price"]
        if last_mid_price is not None:
            absolute_mid_move = abs(current_mid_price - last_mid_price)
            symbol_state["absolute_move_history"].append(absolute_mid_move)
            if len(symbol_state["absolute_move_history"]) > self.maximum_absolute_move_history_length:
                symbol_state["absolute_move_history"].pop(0)

        symbol_state["mid_history"].append(current_mid_price)
        if len(symbol_state["mid_history"]) > self.maximum_mid_history_length:
            symbol_state["mid_history"].pop(0)

        symbol_state["last_mid_price"] = current_mid_price

    def _fair_price(
        self,
        symbol_state: Dict[str, Any],
        current_mid_price: float,
        current_microprice: float,
    ) -> float:
        short_mean_price = self._mean_of_last_values(
            symbol_state["mid_history"], 5, current_mid_price
        )
        long_mean_price = self._mean_of_last_values(
            symbol_state["mid_history"], 20, current_mid_price
        )

        microprice_gap = current_microprice - current_mid_price
        short_reversion_gap = short_mean_price - current_mid_price
        long_reversion_gap = long_mean_price - current_mid_price

        predicted_mid_move = (
            (self.microprice_weight * microprice_gap)
            + (self.short_reversion_weight * short_reversion_gap)
            + (self.long_reversion_weight * long_reversion_gap)
        )

        return current_mid_price + predicted_mid_move

    def _passive_half_width(self, observed_spread: float, recent_absolute_move: float) -> float:
        return max(2.0, min(5.0, (0.22 * observed_spread) + (0.55 * recent_absolute_move)))

    def _take_threshold(self, observed_spread: float, recent_absolute_move: float) -> float:
        adaptive_threshold = (0.15 * observed_spread) + (1.50 * recent_absolute_move) + 0.50
        return max(self.minimum_snipe_edge, min(6.0, adaptive_threshold))

    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}
        conversions = 0

        stored_state = self._load_state(state.traderData)
        symbol_state = stored_state[self.symbol]

        if self.symbol not in state.order_depths:
            return result, conversions, json.dumps(stored_state)

        order_depth = state.order_depths[self.symbol]
        orders: List[Order] = []

        best_bid_price, best_ask_price, best_bid_volume, best_ask_volume = self._best_prices(order_depth)
        current_mid_price = self._mid_price(
            best_bid_price,
            best_ask_price,
            symbol_state["last_mid_price"],
        )
        current_microprice = self._microprice(
            best_bid_price,
            best_ask_price,
            best_bid_volume,
            best_ask_volume,
            current_mid_price,
        )

        self._update_state(symbol_state, current_mid_price)

        observed_spread = (
            float(best_ask_price - best_bid_price)
            if best_bid_price is not None and best_ask_price is not None
            else 14.0
        )
        recent_absolute_move = self._mean_of_last_values(
            symbol_state["absolute_move_history"], 10, 1.0
        )

        fair_price = self._fair_price(symbol_state, current_mid_price, current_microprice)

        current_position = state.position.get(self.symbol, 0)
        maximum_buy_quantity = self.position_limit - current_position
        maximum_sell_quantity = self.position_limit + current_position

        reserved_buy_quantity = 0
        reserved_sell_quantity = 0
        assumed_fill_position = current_position

        def add_buy_order(order_price: int, order_quantity: int) -> int:
            nonlocal reserved_buy_quantity
            if order_quantity <= 0:
                return 0
            remaining_buy_quantity = maximum_buy_quantity - reserved_buy_quantity
            if remaining_buy_quantity <= 0:
                return 0
            clipped_quantity = min(order_quantity, remaining_buy_quantity)
            if clipped_quantity <= 0:
                return 0
            orders.append(Order(self.symbol, int(order_price), int(clipped_quantity)))
            reserved_buy_quantity += clipped_quantity
            return clipped_quantity

        def add_sell_order(order_price: int, order_quantity: int) -> int:
            nonlocal reserved_sell_quantity
            if order_quantity <= 0:
                return 0
            remaining_sell_quantity = maximum_sell_quantity - reserved_sell_quantity
            if remaining_sell_quantity <= 0:
                return 0
            clipped_quantity = min(order_quantity, remaining_sell_quantity)
            if clipped_quantity <= 0:
                return 0
            orders.append(Order(self.symbol, int(order_price), -int(clipped_quantity)))
            reserved_sell_quantity += clipped_quantity
            return clipped_quantity

        take_threshold = self._take_threshold(observed_spread, recent_absolute_move)

        if order_depth.sell_orders:
            for ask_price in sorted(order_depth.sell_orders.keys()):
                ask_quantity = abs(order_depth.sell_orders[ask_price])
                inventory_fraction = assumed_fill_position / self.position_limit
                reservation_price = fair_price - (self.inventory_skew_at_full * inventory_fraction)
                buy_edge = reservation_price - ask_price

                if buy_edge < take_threshold:
                    break

                desired_quantity = (
                    self.strong_taker_size if buy_edge >= (take_threshold + 1.5) else self.base_taker_size
                )
                executed_quantity = add_buy_order(
                    ask_price, min(ask_quantity, desired_quantity)
                )
                assumed_fill_position += executed_quantity

        if order_depth.buy_orders:
            for bid_price in sorted(order_depth.buy_orders.keys(), reverse=True):
                bid_quantity = order_depth.buy_orders[bid_price]
                inventory_fraction = assumed_fill_position / self.position_limit
                reservation_price = fair_price - (self.inventory_skew_at_full * inventory_fraction)
                sell_edge = bid_price - reservation_price

                if sell_edge < take_threshold:
                    break

                desired_quantity = (
                    self.strong_taker_size if sell_edge >= (take_threshold + 1.5) else self.base_taker_size
                )
                executed_quantity = add_sell_order(
                    bid_price, min(bid_quantity, desired_quantity)
                )
                assumed_fill_position -= executed_quantity

        inventory_fraction = assumed_fill_position / self.position_limit
        reservation_price = fair_price - (self.inventory_skew_at_full * inventory_fraction)
        alpha_signal = reservation_price - current_mid_price
        passive_half_width = self._passive_half_width(observed_spread, recent_absolute_move)

        if (
            best_bid_price is not None
            and best_ask_price is not None
            and (best_ask_price - best_bid_price) >= 3
        ):
            inside_bid_floor = best_bid_price + 1
            inside_ask_ceiling = best_ask_price - 1

            near_bid_price = self._clip_price(
                int(math.floor(reservation_price - passive_half_width)),
                inside_bid_floor,
                inside_ask_ceiling,
            )
            far_bid_price = self._clip_price(
                near_bid_price - 2,
                inside_bid_floor,
                inside_ask_ceiling,
            )

            near_ask_price = self._clip_price(
                int(math.ceil(reservation_price + passive_half_width)),
                inside_bid_floor,
                inside_ask_ceiling,
            )
            far_ask_price = self._clip_price(
                near_ask_price + 2,
                inside_bid_floor,
                inside_ask_ceiling,
            )
        else:
            fallback_bid_price = best_bid_price if best_bid_price is not None else int(math.floor(current_mid_price - 1))
            fallback_ask_price = best_ask_price if best_ask_price is not None else int(math.ceil(current_mid_price + 1))

            near_bid_price = fallback_bid_price
            far_bid_price = fallback_bid_price
            near_ask_price = fallback_ask_price
            far_ask_price = fallback_ask_price

        balanced_primary_size = self.base_passive_size - (self.base_passive_size // 3)
        balanced_secondary_size = self.base_passive_size // 3
        strong_primary_size = self.strong_passive_size - (self.strong_passive_size // 3)
        strong_secondary_size = self.strong_passive_size // 3

        buy_near_size = 0
        buy_far_size = 0
        sell_near_size = 0
        sell_far_size = 0

        if abs(inventory_fraction) >= self.emergency_inventory_fraction:
            if assumed_fill_position > 0:
                sell_near_size = strong_primary_size
                sell_far_size = strong_secondary_size
            else:
                buy_near_size = strong_primary_size
                buy_far_size = strong_secondary_size
        elif alpha_signal >= self.alpha_to_turn_off_opposite_side:
            buy_near_size = strong_primary_size
            buy_far_size = strong_secondary_size
            if assumed_fill_position > 0:
                sell_near_size = balanced_secondary_size
        elif alpha_signal <= -self.alpha_to_turn_off_opposite_side:
            sell_near_size = strong_primary_size
            sell_far_size = strong_secondary_size
            if assumed_fill_position < 0:
                buy_near_size = balanced_secondary_size
        elif alpha_signal >= self.alpha_to_lean:
            buy_near_size = strong_primary_size
            buy_far_size = strong_secondary_size
            sell_near_size = balanced_secondary_size
        elif alpha_signal <= -self.alpha_to_lean:
            sell_near_size = strong_primary_size
            sell_far_size = strong_secondary_size
            buy_near_size = balanced_secondary_size
        else:
            buy_near_size = balanced_primary_size
            buy_far_size = balanced_secondary_size
            sell_near_size = balanced_primary_size
            sell_far_size = balanced_secondary_size

        if buy_near_size > 0:
            add_buy_order(near_bid_price, buy_near_size)

        if buy_far_size > 0 and far_bid_price < near_bid_price:
            add_buy_order(far_bid_price, buy_far_size)

        if sell_near_size > 0:
            add_sell_order(near_ask_price, sell_near_size)

        if sell_far_size > 0 and far_ask_price > near_ask_price:
            add_sell_order(far_ask_price, sell_far_size)

        result[self.symbol] = orders

        print(
            f"{self.symbol} | pos={current_position} | mid={current_mid_price:.2f} | "
            f"micro={current_microprice:.2f} | fair={fair_price:.2f} | "
            f"reservation={reservation_price:.2f} | alpha={alpha_signal:.2f} | "
            f"spread={observed_spread:.2f} | recent_move={recent_absolute_move:.2f} | orders={orders}"
        )

        return result, conversions, json.dumps(stored_state)