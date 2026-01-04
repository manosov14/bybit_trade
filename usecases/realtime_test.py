import time
from infra.exchange_ccxt import ExchangeService
from infra.databroker import DataBroker
from domain.trend import d1_trend
from domain.levels import working_levels_d1, LevelsParams
from domain.sweep import check_sweep, SweepParams

def run_once(symbols, d1_sma:int, days_window:int=5):
    ex = ExchangeService(); broker = DataBroker(ex)
    events = []
    for sym in symbols:
        d1 = broker.get_ohlcv(sym, '1d', need=90)
        side = d1_trend(d1, d1_sma)
        lvls = working_levels_d1(d1, side, LevelsParams(days_window=days_window))
        h1 = broker.get_ohlcv(sym, '1h', need=200, warm=True)
        last = h1.iloc[-1]
        for lvl in lvls:
            hi, lo = float(last['high']), float(last['low'])
            pierced = (side=='LONG' and lo<lvl.price) or (side=='SHORT' and hi>lvl.price)
            if pierced:
                window = h1.iloc[-6:][['ts','open','high','low','close']].reset_index(drop=True)
                sw = check_sweep(window, lvl.price, side, SweepParams())
                events.append({'symbol': sym, 'level': float(lvl.price), 'trend': side, 'sweep': sw.is_sweep})
    return events
