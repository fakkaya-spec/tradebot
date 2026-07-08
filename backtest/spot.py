"""Cekirdek spot stratejisi backtest'i: EMA200 ustunde coin'de otur, altinda USDT'ye gec.

Dort varyanti karsilastirir (hepsi ayni 10k baslangic, komisyon dahil):
  1. HODL          : BTC+ETH 50/50 al-tut
  2. SPOT CEKIRDEK : EMA200(4h) ustunde coin, altinda nakit (BTC+ETH 50/50)
  3. BOT           : mevcut futures sistemi (trend+breakout+vol hedefleme)
  4. 50/50         : yarisi spot cekirdek, yarisi bot (surekli rebalans varsayimi)

Kullanim: python -m backtest.spot --days 1825
"""
import argparse

import pandas as pd

from bot.config import Config

from . import data as data_mod
from .engine import Backtester

SPOT_FEE = 0.001    # %0.1 spot taker
SLIPPAGE = 0.0003
WARMUP = 200        # EMA200 isinmasi (bar)


def spot_curve(df: pd.DataFrame, ema_span: int = 200) -> pd.Series:
    """Tek sembol icin strateji carpani: EMA ustunde coin getirisi, altinda 0.
    Karar bar kapanisinda verilir, bir sonraki bardan itibaren uygulanir."""
    close = df["close"]
    ema = close.ewm(span=ema_span, adjust=False).mean()
    pos = (close > ema).astype(float).shift(1).fillna(0.0)
    ret = close.pct_change().fillna(0.0)
    switch_cost = pos.diff().abs().fillna(0.0) * (SPOT_FEE + SLIPPAGE)
    equity = (1.0 + ret * pos - switch_cost).cumprod()
    return equity.iloc[WARMUP:] / equity.iloc[WARMUP]


def hodl_curve(df: pd.DataFrame) -> pd.Series:
    close = df["close"].iloc[WARMUP:]
    return close / close.iloc[0]


def blend(curves: list) -> pd.Series:
    """Esit agirlikli, surekli rebalansli portfoy (bar getirilerinin ortalamasi)."""
    rets = pd.concat([c.pct_change() for c in curves], axis=1).fillna(0.0)
    return (1.0 + rets.mean(axis=1)).cumprod()


def stats(curve: pd.Series, start_equity: float) -> str:
    eq = curve * start_equity
    days = (eq.index[-1] - eq.index[0]).days or 1
    cagr = (eq.iloc[-1] / start_equity) ** (365 / days) - 1
    dd = (eq / eq.cummax() - 1).min()
    yearly = eq.resample("YE").last().pct_change()
    first = eq.resample("YE").last()
    yearly.iloc[0] = first.iloc[0] / start_equity - 1
    ytxt = "  ".join(f"{ts.year}:{r*100:+.0f}%" for ts, r in yearly.items())
    return (f"son={eq.iloc[-1]:>10,.0f} USDT  CAGR={cagr*100:+6.1f}%  MaxDD={dd*100:6.1f}%\n"
            f"      yillik: {ytxt}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=1825)
    ap.add_argument("--equity", type=float, default=10_000)
    args = ap.parse_args()

    cfg = Config()
    spot_syms = ["BTCUSDT", "ETHUSDT"]
    bot_syms = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

    data, funding = {}, {}
    for sym in sorted(set(spot_syms + bot_syms)):
        print(f"{sym}: veri yukleniyor...")
        k, f = data_mod.load(sym, cfg.timeframe, args.days + 45)
        data[sym], funding[sym] = k, f

    print("Bot backtest'i kosuyor...")
    bot_result = Backtester({s: data[s] for s in bot_syms},
                            {s: funding[s] for s in bot_syms}, cfg,
                            start_equity=args.equity).run()
    bot_curve = bot_result.equity_curve / bot_result.start_equity

    hodl = blend([hodl_curve(data[s]) for s in spot_syms])
    core = blend([spot_curve(data[s]) for s in spot_syms])
    idx = bot_curve.index.intersection(core.index)
    combined = blend([core.loc[idx], bot_curve.loc[idx]])

    line = "=" * 78
    print(line)
    print(f"  CEKIRDEK + UYDU KARSILASTIRMASI  |  {args.days} gun  |  baslangic {args.equity:,.0f} USDT")
    print(line)
    print(f"  1. HODL (BTC+ETH)      {stats(hodl, args.equity)}")
    print(f"  2. SPOT CEKIRDEK       {stats(core, args.equity)}")
    print(f"  3. BOT (futures)       {stats(bot_curve, args.equity)}")
    print(f"  4. 50/50 CEKIRDEK+BOT  {stats(combined.loc[idx], args.equity)}")
    print(line)
    print("  Spot komisyonu %0.1 + slippage dahil; cekirdek karari EMA200(4h) kapanisiyla.")


if __name__ == "__main__":
    main()
