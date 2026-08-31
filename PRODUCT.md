# PRODUCT.md

## Product purpose

`bybit_trade` is the reference trading solution for the project's trading domain. It contains the current market-analysis and trading-rule implementation that is used as the behavioral source for related tools, including `bybit-backtester`.

The main responsibility of this repository is to describe and implement the trading domain used for live or dry-run decision making:

- market structure and levels
- false breakout / sweep detection
- trend and bias logic where applicable
- filters used by the strategy
- risk calculation
- generation of trading intent
- preparation for order execution

The repository is not the backtesting product itself. Historical strategy validation is implemented separately in `bybit-backtester`.

## Core product flow

The intended high-level flow is:

`Bybit market data -> domain analysis -> strategy rules -> trading intent -> dry-run or execution layer`

The trading domain must remain reusable enough that the same strategy behavior can be reproduced in an offline historical backtest.

## Domain capabilities

The repository currently contains reusable trading logic around:

- levels and level detection
- sweeps / false breakouts
- trend and directional bias
- session-related logic where required by the strategy
- speed and other trade filters
- delta-related logic where required by the strategy
- risk parameters
- entry, stop-loss and take-profit calculation

These components are the behavioral reference for the strategy implementation in `bybit-backtester`.

## Reference strategy for MVP validation

The first strategy that must be reproducible in `bybit-backtester` is the false breakout strategy.

For a detected setup, the strategy domain must be able to determine at minimum:

- whether a valid signal exists
- signal direction: long or short
- signal timestamp
- entry price
- stop-loss price
- take-profit price
- conditions that invalidate the setup
- metadata required to explain why the signal was generated

The exact behavior should be documented separately in a strategy specification before migration or refactoring.

## Relationship with bybit-backtester

`bybit_trade` and `bybit-backtester` have different product responsibilities.

### bybit_trade

Responsible for:

- current trading-domain behavior
- live or dry-run market analysis
- creation of trading intent
- exchange-facing infrastructure where required
- future real-order execution

### bybit-backtester

Responsible for:

- loading historical candles from Bybit public API
- caching historical candles in PostgreSQL
- detecting missing candle ranges and downloading only missing data
- running the false breakout strategy over historical data
- simulating trade execution on future candles
- calculating backtest metrics
- returning backtest results through Telegram

The backtester must reuse or reproduce the behavior of the trading domain, but it must not depend on live execution infrastructure.

## Shared architectural rule

Trading rules must be isolated from infrastructure.

Strategy code must not directly:

- call Telegram
- access PostgreSQL
- perform HTTP requests to Bybit
- place orders
- depend on wall-clock time

The strategy should operate on normalized market data and return deterministic trading signals.

This separation is required so that the same trading behavior can be used both in live trading and in historical backtesting.

## MVP integration target

The immediate cross-repository MVP target is:

`Telegram /backtest BTCUSDT 1h 90d -> historical candles -> false breakout strategy -> simulated trades -> metrics -> Telegram report`

The source of strategy behavior for this flow is `bybit_trade`.

The execution environment and user-facing backtest workflow belong to `bybit-backtester`.

## Required backtest metrics

The MVP backtester must calculate:

- total number of trades
- winning trades
- losing trades
- win rate
- gross profit
- gross loss
- Profit Factor
- average reward-to-risk
- maximum drawdown
- final result

These metrics are not responsibilities of the live trading strategy itself, but the strategy must expose enough deterministic information to calculate them historically.

## Out of scope for the shared MVP

The following are explicitly outside the first MVP boundary:

- multiple strategies
- machine learning
- strategy optimization
- portfolio-level simulation
- leverage modelling
- liquidation modelling
- funding-rate modelling
- partial fills
- WebSocket-based historical replay
- Redis
- Kubernetes
- web interface
- charts
- authentication for backtesting
- real order execution from the backtester

## Migration principles for bybit-backtester

When extracting behavior from this repository into `bybit-backtester`:

1. Do not copy the repository wholesale.
2. Extract only domain behavior required by the false breakout strategy.
3. Do not migrate live execution code.
4. Do not migrate scanners or command infrastructure unless required by the strategy itself.
5. Keep historical data access outside the strategy package.
6. Keep persistence outside the strategy package.
7. Make strategy execution deterministic.
8. Add unit tests that reproduce expected long, short, rejection and no-signal cases.

## Backtesting implementation sequence

The recommended implementation sequence for `bybit-backtester` is:

1. Document the false breakout strategy contract.
2. Define common domain models: Candle, TradingSignal, Trade, BacktestRequest, BacktestResult and BacktestMetrics.
3. Implement PostgreSQL candle storage.
4. Implement a read-only Bybit historical data client.
5. Implement missing-range historical data caching.
6. Port or adapt the false breakout strategy from this repository.
7. Implement the candle-based backtest engine.
8. Implement metrics calculation.
9. Implement the backtest orchestration service.
10. Implement the Telegram interface.
11. Add Docker Compose and an end-to-end smoke test.

## Definition of success

The integration MVP is successful when:

1. `bybit_trade` remains the clear source of truth for the false breakout trading behavior.
2. `bybit-backtester` reproduces that strategy deterministically on historical candles.
3. A user can run `/backtest BTCUSDT 1h 90d` in Telegram.
4. Historical data is fetched through Bybit public API and cached locally.
5. The backtester simulates all generated trades without placing real orders.
6. The user receives reproducible backtest statistics.
7. Live-trading infrastructure and backtesting infrastructure remain separated.

## Next documentation artifact

Before strategy migration, create a detailed false breakout specification that explicitly documents:

- input candle requirements
- level selection rules
- sweep / false breakout rules
- long setup
- short setup
- entry calculation
- stop-loss calculation
- take-profit calculation
- filters
- invalidation rules
- edge cases
- mapping from the current `bybit_trade` functions to the new backtester strategy implementation

This specification should become the contract used to verify that `bybit-backtester` reproduces the same strategy behavior as `bybit_trade`.
