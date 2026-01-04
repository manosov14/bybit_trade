"""Quick offline sanity checks.

Run:
  python selftest.py

This does not hit Bybit. It only checks imports and helper logic.
"""

from __future__ import annotations

from infra.specs import SpecsResolver, round_down


def main() -> None:
    # tick/step resolution: full symbol takes precedence, then BASE
    env = {
        "TICK_SIZE_MAP": "BTC:0.5,BTC/USDT:USDT:0.25,ETH:0.05",
        "QTY_STEP_MAP": "BTC:0.001,ETH:0.01",
        "DEFAULT_TICK_SIZE": 0.01,
        "DEFAULT_QTY_STEP": 0.001,
    }
    specs = SpecsResolver(
        tick_map_raw=env.get("TICK_SIZE_MAP"),
        qty_map_raw=env.get("QTY_STEP_MAP"),
        default_tick=float(env.get("DEFAULT_TICK_SIZE")),
        default_step=float(env.get("DEFAULT_QTY_STEP")),
    )

    assert specs.tick_size("BTC/USDT:USDT") == 0.25
    assert specs.tick_size("BTC/USDT") == 0.5  # falls back to BASE
    assert specs.tick_size("XRP/USDT:USDT") == 0.01

    assert specs.qty_step("ETH/USDT:USDT") == 0.01
    assert round_down(68001.23, 0.5) == 68001.0

    print("OK: imports + specs resolver")


if __name__ == "__main__":
    main()
