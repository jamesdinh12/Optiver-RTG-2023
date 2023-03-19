# Copyright 2021 Optiver Asia Pacific Pty. Ltd.
#
# This file is part of Ready Trader Go.
#
#     Ready Trader Go is free software: you can redistribute it and/or
#     modify it under the terms of the GNU Affero General Public License
#     as published by the Free Software Foundation, either version 3 of
#     the License, or (at your option) any later version.
#
#     Ready Trader Go is distributed in the hope that it will be useful,
#     but WITHOUT ANY WARRANTY; without even the implied warranty of
#     MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#     GNU Affero General Public License for more details.
#
#     You should have received a copy of the GNU Affero General Public
#     License along with Ready Trader Go.  If not, see
#     <https://www.gnu.org/licenses/>.
import asyncio
import itertools

from typing import List

from ready_trader_go import BaseAutoTrader, Instrument, Lifespan, MAXIMUM_ASK, MINIMUM_BID, Side


LOT_SIZE = 10
POSITION_LIMIT = 100
TICK_SIZE_IN_CENTS = 100
MIN_BID_NEAREST_TICK = (MINIMUM_BID + TICK_SIZE_IN_CENTS) // TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS
MAX_ASK_NEAREST_TICK = MAXIMUM_ASK // TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS


### User defined variables
# Deviation factor used to calculate upper and lower bounds of average future prices
DEVIATION_FACTOR = 0.002
HISTORY = 4
WISDOM = 1



class AutoTrader(BaseAutoTrader):
    """Example Auto-trader.

    When it starts this auto-trader places ten-lot bid and ask orders at the
    current best-bid and best-ask prices respectively. Thereafter, if it has
    a long position (it has bought more lots than it has sold) it reduces its
    bid and ask prices. Conversely, if it has a short position (it has sold
    more lots than it has bought) then it increases its bid and ask prices.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, team_name: str, secret: str):
        """Initialise a new instance of the AutoTrader class."""
        super().__init__(loop, team_name, secret)
        self.order_ids = itertools.count(1)
        self.bids = set()
        self.asks = set()
        self.ask_id = self.ask_price = self.bid_id = self.bid_price = self.position = 0

        # ETF data
        self.etf_ask_prices = [0]
        self.etf_bid_prices = [0]
        self.etf_ask_vol = [0]
        self.etf_bid_vol = [0]
        self.etf_behaviour = 0

        # Recent instrument price deviations
        self.instr_price_dev = [0] * HISTORY
        self.bid_deviation = [0] * HISTORY
        self.ask_deviation = [0] * HISTORY
        

        self.avg_bid_deviation = 0
        self.avg_ask_deviation = 0
        self.bid_dev_changes = [0] * (WISDOM + 1)
        self.ask_dev_changes = [0] * (WISDOM + 1)

        # Flags for buying/selling
        self.good_to_buy = False
        self.good_to_sell = False

    def on_error_message(self, client_order_id: int, error_message: bytes) -> None:
        """Called when the exchange detects an error.

        If the error pertains to a particular order, then the client_order_id
        will identify that order, otherwise the client_order_id will be zero.
        """
        self.logger.warning("error with order %d: %s", client_order_id, error_message.decode())
        if client_order_id != 0 and (client_order_id in self.bids or client_order_id in self.asks):
            self.on_order_status_message(client_order_id, 0, 0, 0)

    def on_hedge_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        """Called when one of your hedge orders is filled.

        The price is the average price at which the order was (partially) filled,
        which may be better than the order's limit price. The volume is
        the number of lots filled at that price.
        """
        self.logger.info("received hedge filled for order %d with average price %d and volume %d", client_order_id,
                         price, volume)

    def on_order_book_update_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                                     ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        """Called periodically to report the status of an order book.

        The sequence number can be used to detect missed or out-of-order
        messages. The five best available ask (i.e. sell) and bid (i.e. buy)
        prices are reported along with the volume available at each of those
        price levels.
        """
        self.logger.info("received order book for instrument %d with sequence number %d", instrument,
                         sequence_number)
        
        if instrument == Instrument.ETF:
            self.etf_ask_prices = ask_prices
            self.etf_ask_vol = ask_volumes
            self.etf_bid_prices = bid_prices
            self.etf_bid_vol = bid_volumes

        if instrument == Instrument.FUTURE:
            
            # We determine an average future price as the current market, and calculuate a buffer zone around it (0.2%)
            avg_future_price = (ask_prices[0] + bid_prices[0]) / 2
            future_upr_bound = avg_future_price * (1 + DEVIATION_FACTOR)
            future_lwr_bound = avg_future_price * (1 - DEVIATION_FACTOR)

            ### Wholistic Market Movement -- COME BACK TO THIS
            avg_etf_price = (mean(self.etf_ask_prices) + mean(self.etf_bid_prices)) / 2
            
            # Deviation of EFT /from/ future
            self.instr_price_dev.pop(-1)
            self.instr_price_dev.insert(0, int(avg_future_price) - int(avg_etf_price))
            
            if all(price < 0 for price in self.instr_price_dev):
                self.etf_behaviour = 1    # ETF above FUTURE

                new_ask_price = ask_prices[0] + TICK_SIZE_IN_CENTS if ask_prices[0] != 0 else 0
                    
                # We're only hitting, so no need to cancel orders
                if self.ask_id != 0 and new_ask_price not in (self.ask_price, 0):
                    # self.send_cancel_order(self.ask_id)
                    # self.ask_id = 0
                    new_ask_price = self.etf_ask_prices[0]

                self.ask_id = next(self.order_ids)
                self.ask_price = new_ask_price
            
                if ((self.position - LOT_SIZE) <= -POSITION_LIMIT):
                    lot = POSITION_LIMIT + self.position - 1
                else:
                    if self.etf_ask_vol[0] < LOT_SIZE:
                        lot = self.etf_ask_vol[0]
                    else:
                        lot = LOT_SIZE
                
                self.send_insert_order(self.ask_id, Side.SELL, new_ask_price, lot, Lifespan.FILL_AND_KILL)
                self.asks.add(self.ask_id)
                print("order sell (ETF above FUTURE)")

            elif all(price > 0 for price in self.instr_price_dev):
                self.etf_behaviour = -1   # ETF below FUTURE

                new_bid_price = bid_prices[0] - TICK_SIZE_IN_CENTS if bid_prices[0] != 0 else 0
                    
                # We're only hitting, so no need to cancel orders
                if self.bid_id != 0 and new_bid_price not in (self.bid_price, 0):
                    # self.send_cancel_order(self.bid_id)
                    # self.bid_id = 0
                    new_bid_price = self.etf_bid_prices[0]
                
                self.bid_id = next(self.order_ids)
                self.bid_price = new_bid_price
            
                if ((self.position + LOT_SIZE) >= POSITION_LIMIT):
                    lot = POSITION_LIMIT - self.position - 1
                else:
                    if self.etf_bid_vol[0] < LOT_SIZE:
                        lot = self.etf_bid_vol[0]
                    else:
                        lot = LOT_SIZE
                
                self.send_insert_order(self.bid_id, Side.BUY, new_bid_price, lot, Lifespan.FILL_AND_KILL)
                self.bids.add(self.bid_id)
                print("order buy (ETF below FUTURE)")

            else:
                self.etf_behaviour = 0    # third thing
            #
            # # I want to see if the market is moving up or down
            # # This is a (very) general trend
            # market_mvmt = self.etf_behaviour[-1] - self.etf_behaviour[0]


            self.bid_deviation.pop(-1)
            self.bid_deviation.insert(0, future_lwr_bound - self.etf_bid_prices[0])
            self.ask_deviation.pop(-1)
            self.ask_deviation.insert(0, self.etf_ask_prices[0] - future_upr_bound)
            
            self.avg_bid_deviation = sum(self.bid_deviation) / len(self.bid_deviation)
            self.avg_ask_deviation = sum(self.ask_deviation) / len(self.ask_deviation)

            for i in range(WISDOM):
                self.bid_dev_changes.pop(-1)
                self.bid_dev_changes.insert(0, self.bid_deviation[i] - self.bid_deviation[i + 1])
                self.ask_dev_changes.pop(-1)
                self.ask_dev_changes.insert(0, self.ask_deviation[i] - self.ask_deviation[i + 1])

            if (all(changes >= 0 for changes in self.bid_dev_changes)) or (sum(self.bid_dev_changes) > 0) or (self.bid_deviation[0] > self.avg_bid_deviation):
                self.good_to_buy = True
            else:
                self.good_to_buy = False

            if (all(changes >= 0 for changes in self.ask_dev_changes)) or (sum(self.ask_dev_changes)) > 0 or (self.ask_deviation[0] > self.avg_ask_deviation):
                self.good_to_sell = True
            else:
                self.good_to_sell = False

            # price_adjustment = - (self.position // LOT_SIZE) * TICK_SIZE_IN_CENTS
            # new_bid_price = self.etf_bid_prices[0] + price_adjustment if self.etf_bid_prices[0] != 0 else 0
            # new_ask_price = self.etf_ask_prices[0] + price_adjustment if self.etf_ask_prices[0] != 0 else 0
            # print(new_bid_price, new_ask_price)

            # print(self.good_to_buy, self.good_to_sell)

            for bid in self.etf_bid_prices:

                if bid < future_lwr_bound:
                    new_bid_price = bid_prices[0] - TICK_SIZE_IN_CENTS if bid_prices[0] != 0 else 0
                    
                    # We're only hitting, so no need to cancel orders
                    if self.bid_id != 0 and new_bid_price not in (self.bid_price, 0):
                        # self.send_cancel_order(self.bid_id)
                        # self.bid_id = 0
                        new_bid_price = self.etf_bid_prices[0]
                    
                    if self.bid_id == 0 and self.position < POSITION_LIMIT and new_bid_price != 0: 
                        
                        if self.good_to_buy:
                            self.bid_id = next(self.order_ids)
                            self.bid_price = new_bid_price
                        
                            if ((self.position + LOT_SIZE) >= POSITION_LIMIT):
                                lot = POSITION_LIMIT - self.position - 1
                            else:
                                if self.etf_bid_vol[0] < LOT_SIZE:
                                    lot = self.etf_bid_vol[0]
                                else:
                                    lot = LOT_SIZE
                            
                            self.send_insert_order(self.bid_id, Side.BUY, new_bid_price, lot, Lifespan.FILL_AND_KILL)
                            self.bids.add(self.bid_id)
                            print("order buy (good to buy)")
                        

            for ask in self.etf_ask_prices:

                if ask > future_upr_bound:
                    new_ask_price = ask_prices[0] + TICK_SIZE_IN_CENTS if ask_prices[0] != 0 else 0
                    
                    # We're only hitting, so no need to cancel orders
                    if self.ask_id != 0 and new_ask_price not in (self.ask_price, 0):
                        # self.send_cancel_order(self.ask_id)
                        # self.ask_id = 0
                        new_ask_price = self.etf_ask_prices[0]
                    
                    if self.ask_id == 0 and self.position > -POSITION_LIMIT and new_ask_price != 0: 
                        
                        if self.good_to_sell:
                            self.ask_id = next(self.order_ids)
                            self.ask_price = new_ask_price
                        
                            if ((self.position - LOT_SIZE) <= -POSITION_LIMIT):
                                lot = POSITION_LIMIT + self.position - 1
                            else:
                                if self.etf_ask_vol[0] < LOT_SIZE:
                                    lot = self.etf_ask_vol[0]
                                else:
                                    lot = LOT_SIZE
                            
                            self.send_insert_order(self.ask_id, Side.SELL, new_ask_price, lot, Lifespan.FILL_AND_KILL)
                            self.asks.add(self.ask_id)
                            print("order sell (good to sell)")
                        


    def on_order_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        """Called when one of your orders is filled, partially or fully.

        The price is the price at which the order was (partially) filled,
        which may be better than the order's limit price. The volume is
        the number of lots filled at that price.
        """
        self.logger.info("received order filled for order %d with price %d and volume %d", client_order_id,
                         price, volume)
        if client_order_id in self.bids:
            self.position += volume
            print("bought")
            self.send_hedge_order(next(self.order_ids), Side.ASK, MIN_BID_NEAREST_TICK, volume)
        elif client_order_id in self.asks:
            self.position -= volume
            print("sell")
            self.send_hedge_order(next(self.order_ids), Side.BID, MAX_ASK_NEAREST_TICK, volume)

    def on_order_status_message(self, client_order_id: int, fill_volume: int, remaining_volume: int,
                                fees: int) -> None:
        """Called when the status of one of your orders changes.

        The fill_volume is the number of lots already traded, remaining_volume
        is the number of lots yet to be traded and fees is the total fees for
        this order. Remember that you pay fees for being a market taker, but
        you receive fees for being a market maker, so fees can be negative.

        If an order is cancelled its remaining volume will be zero.
        """
        self.logger.info("received order status for order %d with fill volume %d remaining %d and fees %d",
                         client_order_id, fill_volume, remaining_volume, fees)
        if remaining_volume == 0:
            if client_order_id == self.bid_id:
                self.bid_id = 0
            elif client_order_id == self.ask_id:
                self.ask_id = 0

            # It could be either a bid or an ask
            self.bids.discard(client_order_id)
            self.asks.discard(client_order_id)

    def on_trade_ticks_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                               ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        """Called periodically when there is trading activity on the market.

        The five best ask (i.e. sell) and bid (i.e. buy) prices at which there
        has been trading activity are reported along with the aggregated volume
        traded at each of those price levels.

        If there are less than five prices on a side, then zeros will appear at
        the end of both the prices and volumes arrays.
        """
        self.logger.info("received trade ticks for instrument %d with sequence number %d", instrument,
                         sequence_number)



### User functions

def mean(lst):
    return sum(lst) / len(lst)
