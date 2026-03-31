from datamodel import OrderDepth, UserId, TradingState, Order
from typing import List
import string
import json

class Trader:

    def bid(self):
        return 15
    
    def run(self, state: TradingState):
        # Initialize the dictionary to hold our orders
        result = {}
        conversions = 0
        
        # ==========================================
        # 1. TRADER DATA RESTORATION (String Saving)
        # ==========================================
        # If this is the first iteration, initialize our EMA state
        if state.traderData == "":
            data = {"EMERALDS_EMA": 10000.0}
        else:
            # Decode the JSON string from the previous iteration
            data = json.loads(state.traderData)
            
        ema = data.get("EMERALDS_EMA", 10000.0)
        
        # ==========================================
        # 2. EMERALDS STRATEGY
        # ==========================================
        symbol = "EMERALDS"
        
        # Check if EMERALDS is actively trading in this iteration
        if symbol in state.order_depths:
            order_depth: OrderDepth = state.order_depths[symbol]
            orders: List[Order] =[]
            
            # --- A. Calculate Mid Price & Update EMA ---
            if len(order_depth.buy_orders) > 0 and len(order_depth.sell_orders) > 0:
                best_bid = max(order_depth.buy_orders.keys())
                best_ask = min(order_depth.sell_orders.keys())
                mid_price = (best_bid + best_ask) / 2.0
                
                # Update the EMA. 
                # alpha = 0.05 means the new price has 5% weight, historical data has 95% weight.
                # This makes it highly resistant to single-tick spoofing/outliers.
                alpha = 0.05 
                ema = (alpha * mid_price) + ((1 - alpha) * ema)
            
            # --- B. Determine the True Fair Price ---
            # If the EMA is within 5 units of 10,000, snap it to 10,000 to ignore noise.
            if abs(ema - 10000) <= 10:
                fair_price = 10000
            else:
                # Otherwise, adapt to the new statistically significant fair value
                fair_price = int(round(ema))
                
            # --- C. Position & Capacity Setup ---
            # Assume limit is 20 (Standard for Emeralds, adjust if the wiki rules change this)
            position_limit = 80 
            current_position = state.position.get(symbol, 0)
            
            # We want to buy at least 1 unit below fair price, and sell 1 unit above
            acceptable_buy_price = fair_price - 1
            acceptable_sell_price = fair_price + 1
            
            # --- D. "Taker" Strategy: Take immediately profitable existing orders ---
            
            # 1. Check if we can BUY cheap sell orders
            if len(order_depth.sell_orders) != 0:
                # Sort the asks ascending (we want to buy the cheapest first)
                for ask_price, ask_volume in sorted(order_depth.sell_orders.items()):
                    if ask_price <= acceptable_buy_price:
                        # In Prosperity, sell volumes are negative integers, so we use abs()
                        trade_volume = min(abs(ask_volume), position_limit - current_position)
                        if trade_volume > 0:
                            orders.append(Order(symbol, ask_price, trade_volume))
                            current_position += trade_volume
                            
            # 2. Check if we can SELL to overpaying buy orders
            if len(order_depth.buy_orders) != 0:
                # Sort the bids descending (we want to sell to the highest bidder first)
                for bid_price, bid_volume in sorted(order_depth.buy_orders.items(), reverse=True):
                    if bid_price >= acceptable_sell_price:
                        trade_volume = min(bid_volume, position_limit + current_position)
                        if trade_volume > 0:
                            # To sell, we must send negative volume
                            orders.append(Order(symbol, bid_price, -trade_volume))
                            current_position -= trade_volume
                            
            # --- E. "Maker" Strategy: Quote our remaining position capacity ---
            # After taking whatever we can, place standing orders in the order book
            # We quote slightly wider to earn the spread (e.g., +/- 2 from fair price)
            buy_capacity = position_limit - current_position
            sell_capacity = position_limit + current_position
            
            if buy_capacity > 0:
                maker_bid = fair_price - 6 
                orders.append(Order(symbol, maker_bid, buy_capacity))
                
            if sell_capacity > 0:
                maker_ask = fair_price + 6
                orders.append(Order(symbol, maker_ask, -sell_capacity))
                
            # Attach the orders to the results dictionary
            result[symbol] = orders
            
        # ==========================================
        # 3. SAVE STATE FOR NEXT ITERATION
        # ==========================================
        data["EMERALDS_EMA"] = ema
        traderData = json.dumps(data) # Converts our python dict into a compact string
        
        return result, conversions, traderData