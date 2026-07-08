"""Anlik sinyal goruntuleyici: bot su an her paritede ne goruyor?

Kullanim: python -m backtest.signal
Canli botun bir sonraki dongude verecegi karari (son KAPANMIS 4h bara gore)
gosterir. Emir gondermez, sadece okur.
"""
import requests

import pandas as pd

from bot.config import Config
from bot.indicators import add_indicators
from bot.strategy import BREAKOUT, TREND, StrategyParams, entry_signal

LABELS = {1: "TREND-YUKARI", 0: "YATAY", -1: "TREND-ASAGI"}


def fetch(symbol: str, interval: str, limit: int = 500) -> pd.DataFrame:
    rows = requests.get(
        "https://fapi.binance.com/fapi/v1/klines",
        params={"symbol": symbol, "interval": interval, "limit": limit},
        timeout=30,
    ).json()
    df = pd.DataFrame(rows).iloc[:, :6]
    df.columns = ["open_time", "open", "high", "low", "close", "volume"]
    df = df.astype(float)
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df.set_index("ts")[["open", "high", "low", "close", "volume"]].iloc[:-1]  # son bar kapanmadi


def main():
    cfg = Config()
    params = StrategyParams(cfg.adx_threshold, cfg.stop_atr, cfg.breakeven_atr,
                            cfg.trail_atr, enable_regime=cfg.enable_regime,
                            rsi_oversold=cfg.rsi_oversold, rsi_overbought=cfg.rsi_overbought)
    print(f"{'PARITE':<10} {'FIYAT':>12} {'EMA200':>12} {'EMA20/50':>10} {'ADX':>6}  KARAR")
    print("-" * 72)
    for sym in [s.replace("/", "") for s in cfg.symbols]:
        df = add_indicators(fetch(sym, cfg.timeframe), cfg)
        row = df.iloc[-1]
        ema_dir = "yukari" if row.ema_fast > row.ema_slow else "asagi"
        macro = "ustunde" if row.close > row.ema_macro else "altinda"
        signals = []
        for sleeve in (TREND, BREAKOUT):
            side = entry_signal(sleeve, row, params)
            if side:
                signals.append(f"{sleeve}:{side.upper()}")
        karar = " + ".join(signals) if signals else "BEKLE (kosullar olusmadi)"
        print(f"{sym:<10} {row.close:>12,.2f} {row.ema_macro:>12,.2f} {ema_dir:>10} {row.adx:>6.1f}  {karar}")
        print(f"{'':10} fiyat EMA200'un {macro}; son bar: {df.index[-1].strftime('%Y-%m-%d %H:%M UTC')}")
    print("-" * 72)
    print("Not: bot ancak bar KAPANISINDA karar verir; cooldown, funding filtresi ve")
    print("acik pozisyon durumu bu tabloda yoktur - kesin karar canli botundur.")


if __name__ == "__main__":
    main()
