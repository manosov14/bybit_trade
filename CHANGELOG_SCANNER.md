# Changelog — scanner & order manager (false-break bot)

This patch aligns the **market scanner** and the **order opening/management** module with the updated ТЗ (см. файл ТЗ в проекте).

## What changed
- **usecases/scanner.py** — полностью переписан под требования:
  - Фокус на M1 при подходе цены к уровню на расстояние `ATR(5m)`.
  - Детект «прокола» по глубине в долях `ATR(H1, 14)` с порогами `PROBE_MIN_ATR`/`PROBE_MAX_ATR`.
  - «Вооружение» входа: стоп-заявка за уровнем на `ENTRY_TICKS` тиков после возврата.
  - Окно возврата — `RETURN_BARS_5M` пятиминутных свечей; по истечении — снятие заявки.
  - Повторный вход: до 2 попыток на один сигнал (переменная состояния `attempts`). 
  - Лимиты: не более `MAX_OPEN_TRADES`, остановка после `STOP_SERIES_LIMIT` стопов (заготовка; подсчёт производится менеджером позиций).
  - Конфиг загружается из `.env` (см. ключи ниже).

- **usecases/order_manager.py** — новый лёгкий менеджер ордеров:
  - Расчёт **entry/SL/TP/qty** по риск‑параметрам: `ACCOUNT_EQUITY`, `RISK_PCT`, `RR`.
  - Варианты стопа: за экстремум прокола (`STOP_BY_SWEEP=true`) или на `STOP_TICKS` за уровнем.
  - Поддержка шага цены и лота через карты `.env`: `TICK_SIZE_MAP`, `QTY_STEP_MAP`.
  - Режимы: `MODE=test` (печать и фейковые ID) и `MODE=live` (через `ExchangeService/ccxt`).

## New/used .env keys
```
MODE=test|live
SYMBOLS=BTC/USDT:USDT,ETH/USDT:USDT

# Прокол/ATR
H1_ATR_LEN=14
PROBE_MIN_ATR=0.10
PROBE_MAX_ATR=0.35
ATR_M5_LEN=14

# Вход/выход
ENTRY_TICKS=2
STOP_TICKS=2
STOP_BY_SWEEP=true
RR=3
RETURN_BARS_5M=2

# Фильтры (заготовки)
SPEED_ENABLED=true
SPEED_HALF_ATR=0.5
SPEED_MAX_MINUTES=15
SESSIONS=EU,US
DELTA_ENABLED=false

# Риск/лимиты
ACCOUNT_EQUITY=10000
RISK_PCT=1
MAX_OPEN_TRADES=2
STOP_SERIES_LIMIT=3

# Маркет-специфика (для тестов без REST-метаданных)
TICK_SIZE_MAP=BTC/USDT:USDT:0.5,ETH/USDT:USDT:0.05
QTY_STEP_MAP=BTC/USDT:USDT:0.001,ETH/USDT:USDT:0.001
DEFAULT_TICK_SIZE=0.1
DEFAULT_QTY_STEP=0.001
PLACE_STOP_MARKET=true
```
