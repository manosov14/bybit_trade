# Bybit Bot Core (cleaned) + New Strategy Skeleton

## Что внутри

Этот архив — "ядро" (биржа/данные/уровни/логи) + новый скелет для написания анализа с нуля.

### Основные папки
- `app/` — CLI (`/param`, `/trend`, `/days`, `/levels`, `/run`)
- `infra/` — Bybit (ccxt), кэш свечей, загрузка `.env`, резолв тик/лот (`specs.py`)
- `domain/` + `indicators/` — чистая логика трендов/уровней/фильтров
- `scanner/` — универсальные компоненты (`market_feed`, `audit_logger`, `state_store`)
- `strategies/` — **новая** логика анализа/входов (пишите тут)
- `usecases/runtime.py` — **новый** безопасный раннер: данные -> стратегия -> лог интентов
- `execution/` — скелет исполнения (пока dry-run)

Список оставленных/удалённых файлов: `KEEP_FILES.md`.

## Быстрый старт
```bash
cp .env.example .env  # fill keys
python -m app.cli /param
python -m app.cli /trend
python -m app.cli /days --days 5
python -m app.cli /levels
python -m app.cli /run
```

## Важно
- `/run` запускает новый раннер (`usecases/runtime.py`). По умолчанию он только логирует "интенты" и не торгует.
- Чтобы включить вашу стратегию — реализуйте `strategies/<name>.py` и поставьте `STRATEGY_MODULE=strategies.<name>`.
- Реальные ордера подключим после того, как новая логика анализа будет готова.
