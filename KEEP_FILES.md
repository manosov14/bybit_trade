# Что оставлено и что удалено

Цель: оставить рабочие универсальные модули (биржа, данные, уровни, тренд, логи),
а старую логику анализа/входов убрать и начать писать новую.

## Оставлено (CORE)

### infra/
- `infra/exchange.py`, `infra/exchange_ccxt.py` — CCXT Bybit (linear USDT perps), ретраи.
- `infra/databroker.py` — кэш свечей/единый доступ к OHLCV.
- `infra/env.py` — загрузка .env + нормализация ключей.
- `infra/specs.py` — **новое**: единая резолвилка tick/step + округление.

### domain/
- `domain/levels.py`, `domain/levels_engine.py` — уровни D1.
- `domain/trend.py`, `domain/bias.py` — тренд/байас.
- `domain/sessions.py`, `domain/risk.py`, `domain/speed_filter.py` — универсальные фильтры/метрики.

### indicators/
- `indicators/ta.py`, `indicators/ta_utils.py` — индикаторы/утилиты.

### scanner/
- `scanner/market_feed.py` — снапшоты свечей по таймфреймам.
- `scanner/audit_logger.py` — событийный лог `logs/events.jsonl`.
- `scanner/state_store.py` — состояния/кулдауны.
- `scanner/level_watcher.py`, `scanner/contracts.py` — уровни/контракты.

### usecases/
- `usecases/run_tables.py`, `usecases/run_table_stable.py`, `usecases/day_events.py` — таблицы/просмотр логов.
- `usecases/levels_gateway.py`, `usecases/levels_resolver.py`, `usecases/scanner_levels_reader.py` — чтение уровней.
- `usecases/runtime.py` — **новое**: новый безопасный раннер (данные -> стратегия -> лог интентов).

### strategies/
- `strategies/base.py` — **новое**: интерфейсы Snapshot/Intent.
- `strategies/empty.py` — **новое**: шаблон стратегии.

### execution/
- `execution/executor.py` — **новое**: скелет исполнителя (сухой прогон, без live).

## Удалено (LEGACY анализ/входы)

- `scanner/runner.py`, `scanner/sweep_detector.py`, `scanner/filter_adapter.py`, `scanner/plan_emitter.py`, `scanner/premarket.py`
- `usecases/scanner.py`, `usecases/evaluate_setup.py`, `usecases/monitor_sweeps.py`, `usecases/order_manager.py`, `usecases/fills_hook.py`

Эти файлы были привязаны к старой логике входов/сигналов.
