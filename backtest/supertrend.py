"""Supertrend + SMA200 long-only backtest (kullanici stratejisi).

Kurallar (4h kapanisinda):
- AL : fiyat SMA200 USTUNDE ve Supertrend(10,3) BULLISH iken (pozisyon yoksa)
- SAT: fiyat SMA200 ALTINDA ve Supertrend BEARISH iken (iki kosul birden)
Sadece long, kaldiracsiz. Sermaye sembollere esit bolunur, her biri bagimsiz
"ya hep ya hic" calisir. Komisyon %0.05 + slippage %0.03 her yonde.

Ek olarak ayni kurallarin "sadece Supertrend bearish -> SAT" varyanti da
kiyas icin raporlanir.

Kullanim: python -m backtest.supertrend --days 1825
"""
import argparse

import numpy as np
import pandas as pd

from . import data as data_mod

FEE = 0.0005
SLIP = 0.0003
COST = FEE + SLIP
WARMUP = 200  # SMA200 isinmasi (bar)


def supertrend_direction(df: pd.DataFrame, period: int = 10, mult: float = 3.0) -> np.ndarray:
    """+1 = bullish, -1 = bearish (klasik Supertrend, ATR Wilder)."""
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    hl2 = (h + l) / 2.0
    prev_c = np.roll(c, 1)
    prev_c[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev_c), np.abs(l - prev_c)))
    atr = pd.Series(tr).ewm(alpha=1.0 / period, adjust=False).mean().values
    ub, lb = hl2 + mult * atr, hl2 - mult * atr
    fub, flb = ub.copy(), lb.copy()
    direction = np.ones(len(c), dtype=int)
    for i in range(1, len(c)):
        fub[i] = ub[i] if (ub[i] < fub[i - 1] or c[i - 1] > fub[i - 1]) else fub[i - 1]
        flb[i] = lb[i] if (lb[i] > flb[i - 1] or c[i - 1] < flb[i - 1]) else flb[i - 1]
        if c[i] > fub[i - 1]:
            direction[i] = 1
        elif c[i] < flb[i - 1]:
            direction[i] = -1
        else:
            direction[i] = direction[i - 1]
    return direction


def sleeve_curve(df: pd.DataFrame, exit_and: bool = True):
    """Tek sembol icin strateji carpani (1.0'dan baslar) ve islem sayisi.

    exit_and=True : SAT = fiyat<SMA200 VE supertrend bearish (kullanici kurali)
    exit_and=False: SAT = supertrend bearish (klasik cikis)
    """
    c = df["close"].values
    sma = df["close"].rolling(WARMUP).mean().values
    bull = supertrend_direction(df) == 1
    eq, pos, trades = 1.0, False, 0
    curve = np.ones(len(c))
    for i in range(len(c)):
        if i < WARMUP:
            curve[i] = eq
            continue
        if pos:
            eq *= c[i] / c[i - 1]
        if not pos and c[i] > sma[i] and bull[i]:
            pos = True
            eq *= 1.0 - COST
            trades += 1
        elif pos and (not bull[i]) and (c[i] < sma[i] if exit_and else True):
            pos = False
            eq *= 1.0 - COST
        curve[i] = eq
    return pd.Series(curve, index=df.index), trades


def portfolio(curves: dict, base: float) -> pd.Series:
    """Her sembole esit pay, bagimsiz kasalar; toplam deger serisi."""
    per = base / len(curves)
    idx = sorted(set().union(*[s.index for s in curves.values()]))
    total = pd.Series(0.0, index=pd.DatetimeIndex(idx))
    for s in curves.values():
        total = total.add(s.reindex(idx).ffill().fillna(1.0) * per, fill_value=0.0)
    return total


def report(total: pd.Series, base: float, trades: dict, title: str):
    line = "=" * 62
    days = (total.index[-1] - total.index[0]).days or 1
    cagr = (total.iloc[-1] / base) ** (365 / days) - 1
    dd = (total / total.cummax() - 1).min()
    print(line)
    print(f"  {title}")
    print(line)
    print(f"  Baslangic : {base:,.0f} USDT   Son: {total.iloc[-1]:,.0f} USDT "
          f"({(total.iloc[-1]/base-1)*100:+.1f}%)")
    print(f"  CAGR      : {cagr*100:+.1f}%   MaxDD: {dd*100:.1f}%   "
          f"Islem: {sum(trades.values())} ({', '.join(f'{k}:{v}' for k, v in trades.items())})")
    print(line)
    monthly = total.resample("ME").last()
    rets = monthly.pct_change()
    rets.iloc[0] = monthly.iloc[0] / base - 1
    print("  Aylik getiriler:")
    for ts, r in rets.items():
        bar = "#" * int(abs(r) * 200)
        print(f"    {ts.strftime('%Y-%m')}  {r*100:+6.1f}%  {bar}")
    print(line)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=1825)
    ap.add_argument("--equity", type=float, default=10_000)
    ap.add_argument("--symbols", default="BTCUSDT,SOLUSDT")
    args = ap.parse_args()

    data = {}
    for sym in [s.strip() for s in args.symbols.split(",")]:
        print(f"{sym}: veri yukleniyor...")
        k, _ = data_mod.load(sym, "4h", args.days + 45)
        data[sym] = k

    curves_a, trades_a = {}, {}
    curves_b, trades_b = {}, {}
    for sym, df in data.items():
        curves_a[sym], trades_a[sym] = sleeve_curve(df, exit_and=True)
        curves_b[sym], trades_b[sym] = sleeve_curve(df, exit_and=False)

    total_a = portfolio(curves_a, args.equity)
    total_b = portfolio(curves_b, args.equity)
    report(total_a, args.equity,  trades_a,
           "SUPERTREND+SMA200 (SAT: SMA200 alti VE bearish - istenen kural)")
    days = (total_b.index[-1] - total_b.index[0]).days or 1
    cagr_b = (total_b.iloc[-1] / args.equity) ** (365 / days) - 1
    dd_b = (total_b / total_b.cummax() - 1).min()
    print(f"  KIYAS - klasik cikis (SAT: sadece Supertrend bearish):")
    print(f"    Son: {total_b.iloc[-1]:,.0f} USDT  CAGR: {cagr_b*100:+.1f}%  "
          f"MaxDD: {dd_b*100:.1f}%  Islem: {sum(trades_b.values())}")
    print("=" * 62)


if __name__ == "__main__":
    main()
