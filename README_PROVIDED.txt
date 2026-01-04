bybit/filters/bias.py — фильтр тренда (D1/H4 SMA, combined_bias_flags).
Зависимости: только indicators/ta_utils.sma.

bybit/filters/delta_absorption.py — дельта/поглощение (датаклассы DeltaParams, DeltaResult, evaluate_delta_absorption).
Зависимости: pandas, numpy. Вход: M1 OHLCV + уровень + тип (PDH|PDL).

bybit/filters/sessions.py — фильтр торговых сессий (ЕС/США/Азия).
Самодостаточен, не зависит от внешних факторов.

bybit/core/inside_days.py — подсчёт «внутренних» дней подряд.
Самодостаточен.

bybit/core/levels_engine.py — PDH/PDL (уровни предыдущего дня), класс данных Level.
Самодостаточен.

bybit/indicators/ta_utils.py — брать только используемые функции (SMA/ATR).
Остальные можно не указывать.

bybit/services/exchange.py — обёртка над ccxt.bybit (fetch_ohlcv, fetch_trades, market_conditions_ok).
Рекомендуется оставить как адаптер IExchange, тогда код домена не будет зависеть от биржи.