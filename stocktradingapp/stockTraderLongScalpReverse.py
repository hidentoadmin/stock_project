import logging
import queue
import threading
from queue import PriorityQueue, Queue
from time import sleep

import requests
import schedule
from django.utils.timezone import now
from kiteconnect import KiteConnect

from stock_project import settings
from stocktradingapp.models import Stock, ZerodhaAccount, Controls, LiveMonitor

logging.basicConfig(filename=settings.LOG_FILE_PATH, level=logging.DEBUG)

# CONSTANTS
ENTER = 1
EXIT = 0
EXIT_NOW = 6
HIGH_PRIORITY = 4
LOW_PRIORITY = 5
STATUS_COMPLETE = 'COMPLETE'
STATUS_CANCELLED = 'CANCELLED'
STATUS_REJECTED = 'REJECTED'
ORDER_VARIETY = 'regular'
TICK_SIZE = 0.05

# PARAMETERS
ENTRY_TRIGGER_TIMES = (100.0 - settings.ENTRY_TRIGGER_PERCENT) / 100.0

MAX_RISK_PERCENT_PER_TRADE = settings.MAX_RISK_PERCENT_PER_TRADE
MAX_INVESTMENT_PER_POSITION = settings.MAX_INVESTMENT_PER_POSITION
MIN_INVESTMENT_PER_POSITION = settings.MIN_INVESTMENT_PER_POSITION

POSITION_STOPLOSS_PERCENT = settings.POSITION_STOPLOSS_PERCENT
POSITION_TARGET_PERCENT = settings.POSITION_TARGET_PERCENT

USER_STOPLOSS_PERCENT = settings.USER_STOPLOSS_PERCENT
USER_TARGET_STOPLOSS = settings.USER_TARGET_STOPLOSS
USER_TARGET_PERCENT = settings.USER_TARGET_PERCENT
USER_STOPLOSS_TARGET_RATIO = USER_TARGET_PERCENT / USER_STOPLOSS_PERCENT

ENTRY_TIME_START = now().time().replace(hour=settings.ENTRY_TIME_START[0], minute=settings.ENTRY_TIME_START[1],
                                        second=settings.ENTRY_TIME_START[2])

# FOR EACH TOKEN
token_symbols = {}
token_trigger_prices = {}
token_mis_margins = {}
current_positions = {}

# FOR EACH USER
user_kites = {}
pending_orders = {} # {userid1:None, userid2:None, userid3:32435}
user_initial_value = {}
live_funds_available = {}
user_net_value = {}
user_commission = {}
user_target_value = {}
user_stoploss = {}
user_target_stoploss = {}
user_amount_at_risk = {}
signal_queues = {}
live_monitor = {}

# OTHER GLOBALS
postback_queue = Queue(maxsize=500)
entry_allowed = True

class SignalClass(object):
    def __init__(self, priority, signal):
        self.priority = priority
        self.signal = signal

    def __lt__(self, other):
        return self.priority < other.priority

def analyzeTicks(tick_queue):
    setupParameters()
    if not setupUserAccounts():
        return
    updateTriggerRangesInDB()
    setupTokenMaps()
    startPostbackProcessingThread()
    logging.debug('long scalp stock trader thread started')
    scheduleExit()
    while True:
        try:
            tick = tick_queue.get(True)
            for instrument in tick:
                checkEntryTrigger(instrument['instrument_token'], instrument['last_price'])
            schedule.run_pending()
        except Exception as e:
            pass

def setupUserAccounts():
    user_zerodhas = ZerodhaAccount.objects.filter(is_active=True)
    user_present = False
    for user_zerodha in user_zerodhas:
        user_present = True
        if not validateAccessToken(user_zerodha.access_token_time):
            continue
        setupUserMaps(user_zerodha)
        updateLiveMonitor(user_zerodha.user_id)
        trading_thread = threading.Thread(target=tradeExecutor, daemon=True, args=(user_zerodha.user_id,),
                                          name=user_zerodha.user_id + '_trader_thread')
        trading_thread.start()
    return user_present

def setupUserMaps(user_zerodha):
    kite = KiteConnect(user_zerodha.api_key)
    kite.set_access_token(user_zerodha.access_token)
    user_kites[user_zerodha.user_id] = kite

    updateFundAvailable(user_zerodha.user_id)
    user_zerodha.fund_available = live_funds_available[user_zerodha.user_id]
    user_zerodha.save()

    user_initial_value[user_zerodha.user_id] = live_funds_available[user_zerodha.user_id]
    user_target_value[user_zerodha.user_id] = live_funds_available[user_zerodha.user_id] * (100.0 + USER_TARGET_PERCENT) / 100.0
    user_net_value[user_zerodha.user_id] = live_funds_available[user_zerodha.user_id]
    user_commission[user_zerodha.user_id] = 0.0
    user_stoploss[user_zerodha.user_id] = (100.0 - USER_STOPLOSS_PERCENT) * live_funds_available[user_zerodha.user_id] / 100.0
    user_target_stoploss[user_zerodha.user_id] = USER_TARGET_STOPLOSS * user_net_value[user_zerodha.user_id] / 100.0
    user_amount_at_risk[user_zerodha.user_id] = 0.0
    signal_queues[user_zerodha.user_id] = PriorityQueue(maxsize=100)
    pending_orders[user_zerodha.user_id] = None
    live_monitor[user_zerodha.user_id] = LiveMonitor(hstock_user=user_zerodha.hstock_user, user_id=user_zerodha.user_id,
                                                     initial_value=user_initial_value[user_zerodha.user_id])

def updateLiveMonitor(user_id):
    user_live_monitor = live_monitor[user_id]
    user_live_monitor.current_value = user_net_value[user_id]
    user_live_monitor.stoploss = user_stoploss[user_id]
    user_live_monitor.net_profit_percent = (user_live_monitor.current_value - user_live_monitor.initial_value) * 100.0 / user_live_monitor.initial_value
    user_live_monitor.value_at_risk = user_amount_at_risk[user_id]
    user_live_monitor.commission = user_commission[user_id]
    user_live_monitor.profit = user_live_monitor.current_value - user_live_monitor.initial_value + user_live_monitor.commission
    user_live_monitor.save()

def validateAccessToken(access_token_time):
    expiry_time = now().replace(hour=8, minute=30, second=0, microsecond=0)
    if now() > expiry_time and access_token_time < expiry_time:
        return False
    return True

def updateFundAvailable(zerodha_user_id):
    margin = user_kites[zerodha_user_id].margins()
    live_funds_available[zerodha_user_id] = margin['equity']['available']['live_balance']

def updateTriggerRangesInDB():
    trigger_ranges_response = requests.get(url=settings.TRIGGER_RANGE_URL)
    trigger_ranges = trigger_ranges_response.json()
    stocks = Stock.objects.filter(active=True)
    symbol_stock = {}
    for stock in stocks:
        symbol_stock[stock.trading_symbol] = stock
    for instrument in trigger_ranges:
        stock = symbol_stock.get(instrument['tradingsymbol'])
        if stock is not None:
            stock.co_trigger_percent_lower = instrument['co_lower']
            stock.co_trigger_percent_upper = instrument['co_upper']
            stock.mis_margin = instrument['mis_multiplier']
            stock.save()

def setupTokenMaps():
    stocks = Stock.objects.filter(active=True)
    for stock in stocks:
        current_positions[stock.instrument_token] = []
        token_symbols[stock.instrument_token] = stock.trading_symbol
        token_trigger_prices[stock.instrument_token] = 0.0 # initial trigger price
        token_mis_margins[stock.instrument_token] = stock.mis_margin

def setupParameters():
    global ENTRY_TRIGGER_TIMES, MAX_RISK_PERCENT_PER_TRADE, MAX_INVESTMENT_PER_POSITION, MIN_INVESTMENT_PER_POSITION, \
        POSITION_STOPLOSS_PERCENT, POSITION_TARGET_PERCENT, USER_STOPLOSS_PERCENT,\
        USER_TARGET_STOPLOSS, USER_STOPLOSS_TARGET_RATIO, USER_TARGET_PERCENT, ENTRY_TIME_START

    try:
        controls = Controls.objects.get(control_id=settings.CONTROLS_RECORD_ID)

        ENTRY_TRIGGER_TIMES = (100.0 - controls.entry_trigger_percent) / 100.0

        MAX_RISK_PERCENT_PER_TRADE = controls.max_risk_percent_per_trade
        MAX_INVESTMENT_PER_POSITION = controls.max_investment_per_position
        MIN_INVESTMENT_PER_POSITION = controls.min_investment_per_position

        POSITION_STOPLOSS_PERCENT = controls.position_stoploss_percent
        POSITION_TARGET_PERCENT = controls.position_target_percent

        USER_STOPLOSS_PERCENT = controls.user_stoploss_percent
        USER_TARGET_STOPLOSS = controls.user_target_stoploss
        USER_TARGET_PERCENT = controls.user_target_percent
        USER_STOPLOSS_TARGET_RATIO = USER_TARGET_PERCENT / USER_STOPLOSS_PERCENT

        ENTRY_TIME_START = controls.entry_time_start.time()
    except Exception as e:
        pass

def startPostbackProcessingThread():
    postback_processing_thread = threading.Thread(target=updateOrderFromPostback, daemon=True, name='postback_processing_thread')
    postback_processing_thread.start()

def checkEntryTrigger(instrument_token, current_price):
    if current_price <= token_trigger_prices[instrument_token]: # entry trigger breached
        token_trigger_prices[instrument_token] = current_price * ENTRY_TRIGGER_TIMES
        sendSignal(ENTER, instrument_token, current_price)
    else: # update entry trigger
        token_trigger_prices[instrument_token] = max(token_trigger_prices[instrument_token], current_price * ENTRY_TRIGGER_TIMES)

def sendSignal(enter_or_exit, instrument_token, currentPrice_or_currentPosition): # 0 for exit, 1 for enter
    if enter_or_exit == ENTER:
        for signal_queue in signal_queues.values():
            try:
                signal = {'enter_or_exit': enter_or_exit, 'instrument_token': instrument_token, 'current_price': currentPrice_or_currentPosition}
                signal_object = SignalClass(LOW_PRIORITY, signal)
                signal_queue.put_nowait(signal_object)
            except queue.Full:
                pass
    else:
        signal = {'enter_or_exit': enter_or_exit, 'instrument_token': instrument_token, 'current_position': currentPrice_or_currentPosition}
        signal_object = SignalClass(HIGH_PRIORITY, signal)
        signal_queue = signal_queues[currentPrice_or_currentPosition['user_id']]
        signal_queue.put(signal_object, block=True)

def tradeExecutor(zerodha_user_id):
    signal_queue = signal_queues[zerodha_user_id]
    kite = user_kites[zerodha_user_id]
    place_order = True
    while True:
        signal_object = signal_queue.get(True)
        signal = signal_object.signal
        try:
            if signal['enter_or_exit'] == ENTER and verifyEntryCondition(zerodha_user_id, signal['instrument_token']) and place_order:
                placeEntryOrder(zerodha_user_id, kite, signal)
                place_order = False
            elif signal['enter_or_exit'] == EXIT and not signal['current_position']['exit_pending']:
                placeExitOrder(kite, signal['instrument_token'], signal['current_position'])
            elif signal['enter_or_exit'] == EXIT_NOW and signal['current_position']['exit_pending']:
                placeMarketExitOrder(kite, signal['position'])
        except Exception as e:
            logging.debug('Exception while placing order for user - {}\n'
                          'Instrument Token - {}\n\n{}'.format(zerodha_user_id, signal['instrument_token'], e))

def verifyEntryCondition(zerodha_user_id, instrument_token):
    for position in current_positions[instrument_token]:
        if position['user_id'] == zerodha_user_id:
            return False
    current_time = now().time()
    if (not entry_allowed) or current_time < ENTRY_TIME_START or \
            user_net_value[zerodha_user_id] <= user_stoploss[zerodha_user_id] or pending_orders[zerodha_user_id]:
        return False
    return True

def placeEntryOrder(zerodha_user_id, kite, signal):
    quantity= calculateNumberOfStocksToTrade(zerodha_user_id, signal['instrument_token'], signal['current_price'])
    if quantity == 0:
        return
    quantity = 1
    order_id = kite.place_order(variety=ORDER_VARIETY, exchange='NSE',
                                tradingsymbol=token_symbols[signal['instrument_token']],
                                transaction_type='BUY', quantity=quantity, product='MIS',
                                order_type='MARKET', validity='DAY', disclosed_quantity=quantity)
    pending_orders[zerodha_user_id] = order_id

def placeExitOrder(kite, instrument_token, position):
    order_id = kite.place_order(variety=ORDER_VARIETY, exchange='NSE', tradingsymbol=token_symbols[instrument_token], transaction_type='SELL',
                                quantity=position['number_of_stocks'], order_type='LIMIT', price=getTargetPrice(position['entry_price']),
                                product='MIS', validity='DAY', disclosed_quantity=position['number_of_stocks'])
    position['exit_pending'] = True
    position['exit_order_id'] = order_id

def getTargetPrice(entry_price):
    target_price = (100.0 + POSITION_TARGET_PERCENT) * entry_price / 100.0
    limit_price = float('{:.1f}'.format(target_price))
    limit_price -= 0.1
    while limit_price < target_price:
        limit_price += TICK_SIZE
    return float('{:.2f}'.format(limit_price))

def placeMarketExitOrder(kite, position):
    dump = kite.modify_order(variety=ORDER_VARIETY, order_id=position['exit_order_id'], order_type='MARKET')

def calculateNumberOfStocksToTrade(zerodha_user_id, instrument_token, current_price):
    riskable_amount = min(MAX_RISK_PERCENT_PER_TRADE * user_net_value[zerodha_user_id] / 100.0,
                          user_net_value[zerodha_user_id] - user_amount_at_risk[zerodha_user_id] - user_stoploss[zerodha_user_id])
    if riskable_amount <= 0:
        return 0
    investment_for_riskable_amount = riskable_amount * 100.0 / POSITION_STOPLOSS_PERCENT # riskable_amount = 0.5% then ? = 100%...
    amount_to_invest = min(investment_for_riskable_amount, live_funds_available[zerodha_user_id] * token_mis_margins[instrument_token],
                           MAX_INVESTMENT_PER_POSITION)
    quantity = amount_to_invest // (current_price + 1) # 1 added to match the anticipated price increase in the time gap
    quantity = quantity if (quantity * current_price) >= MIN_INVESTMENT_PER_POSITION else 0
    return int(quantity)

def updateOrderFromPostback():
    while True:
        order_details = postback_queue.get(block=True)
        sleep(0.3)  # postback maybe received instantly after placing order. so wait till order id is added to pending orders list
        try:
            if pending_orders[order_details['user_id']] and pending_orders[order_details['user_id']] == order_details['order_id']:
                if order_details['status'] == STATUS_CANCELLED or order_details['status'] == STATUS_REJECTED:
                    pending_orders[order_details['user_id']] = None
                elif order_details['status'] == STATUS_COMPLETE:
                    updateEntryOrderComplete(order_details)
            elif order_details['status'] == STATUS_COMPLETE:
                updateExitOrderComplete(order_details)
        except Exception as e:
            logging.debug('exception while processing postback: \n{}'.format(e))

def updateEntryOrderComplete(order_details):
    updateFundAvailable(order_details['user_id'])
    new_position = constructNewPosition(order_details)
    updateAmountAtRisk(ENTER, order_details['user_id'], order_details['average_price'], order_details['filled_quantity'])
    current_positions[order_details['instrument_token']].append(new_position)
    pending_orders[order_details['user_id']] = None
    updateLiveMonitor(order_details['user_id'])
    sendSignal(EXIT, order_details['instrument_token'], new_position)

def updateAmountAtRisk(enter_or_exit, zerodha_user_id, price, number_of_stocks):
    if enter_or_exit == ENTER:
        user_amount_at_risk[zerodha_user_id] = user_amount_at_risk[zerodha_user_id] + (price * number_of_stocks * POSITION_STOPLOSS_PERCENT / 100.0)
    else:
        user_amount_at_risk[zerodha_user_id] = user_amount_at_risk[zerodha_user_id] - (price * number_of_stocks * POSITION_STOPLOSS_PERCENT / 100.0)

def updateExitOrderComplete(order_details):
    current_positions_for_instrument = current_positions[order_details['instrument_token']]
    for position in current_positions_for_instrument:
        if position['exit_pending'] and position['exit_order_id'] == order_details['order_id']:
            updateFundAvailable(order_details['user_id'])
            updateUserNetValue(position['user_id'], position, order_details['average_price'])
            updateAmountAtRisk(EXIT, position['user_id'], position['entry_price'], position['number_of_stocks'])
            current_positions_for_instrument.remove(position)
            updateLiveMonitor(order_details['user_id'])
            return

def updateUserNetValue(user_id, position, exit_price):
    trade_profit = (exit_price - position['entry_price']) * position['number_of_stocks']
    commission = calculateCommission(position['entry_price'] * position['number_of_stocks'], exit_price * position['number_of_stocks'])
    user_commission[user_id] += commission
    user_net_value[user_id] += (trade_profit - commission)
    user_stoploss[user_id] = max(user_stoploss[user_id], updateUserStoploss(user_id))

def calculateCommission(buy_value, sell_value):
    trade_value = buy_value + sell_value
    broker_commission_buy = min(20.0, buy_value * 0.03 / 100.0)
    broker_commission_sell = min(20.0, sell_value * 0.03 / 100.0)
    transaction_charge_trade = trade_value * 0.00325 / 100.0
    gst_trade = (broker_commission_buy + broker_commission_sell + transaction_charge_trade) * 18.0 / 100.0
    stt_sell = sell_value * 0.025 / 100.0
    sebi_trade = trade_value * 0.0001 / 100.0
    stamp_duty_trade = trade_value * 0.006 / 100.0
    return broker_commission_buy + broker_commission_sell + transaction_charge_trade + gst_trade + stt_sell + sebi_trade + stamp_duty_trade

def updateUserStoploss(user_id):
    return user_net_value[user_id] - \
           max((user_target_value[user_id] - user_net_value[user_id]) / USER_STOPLOSS_TARGET_RATIO, user_target_stoploss[user_id])

def constructNewPosition(order_details):
    new_position = {}
    new_position['user_id'] = order_details['user_id']
    new_position['number_of_stocks'] = order_details['filled_quantity']
    new_position['entry_price'] = order_details['average_price']
    new_position['exit_pending'] = False
    return new_position

def scheduleExit():
    try:
        controls = Controls.objects.get(control_id=settings.CONTROLS_RECORD_ID)
        entry_time_end = controls.entry_time_end.time()
        exit_time = controls.exit_time.time()
    except Exception as e:
        entry_time_end = now().time().replace(hour=settings.ENTRY_TIME_END[0], minute=settings.ENTRY_TIME_END[1],
                                              second=settings.ENTRY_TIME_END[2])
        exit_time =  now().time().replace(hour=settings.EXIT_TIME[0], minute=settings.EXIT_TIME[1])
    entry_time_end_str = str(entry_time_end.hour) + ':' + str(entry_time_end.minute)
    exit_time_str = str(exit_time.hour) + ':' + str(exit_time.minute)
    schedule.every().day.at(entry_time_end_str).do(blockEntry)
    schedule.every().day.at(exit_time_str).do(exitAllPositions)

def blockEntry():
    global entry_allowed
    entry_allowed = False

def exitAllPositions():
    for instrument_token in current_positions.keys():
        for position in current_positions[instrument_token]:
            sendSignal(EXIT_NOW, instrument_token, position)
