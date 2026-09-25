"""SuperTrended Moving Averages Strategy (TradingView Pine v5 cevirisi).

Kaynak: kullanicinin kuzeninin 'SuperTrended Moving Averages Strategy' scripti.
Kurallar (kapanmis barda, repaint yok):
- Taban cizgi: secilen hareketli ortalama (varsayilan EMA100)
- Bantlar: MA -/+ mult*ATR(period)  (varsayilan 0.5 x ATR(10), Wilder)
- Supertrend kilitlemesi: up ancak yukari, dn ancak asagi tasinir
- trend -1 -> +1 : close onceki dn bandinin ustune kapanirsa  -> AL
- trend +1 -> -1 : close onceki up bandinin altina kapanirsa  -> SAT (pozisyon kapat)
Long-only, stop yok, kaldiracsiz. Sermaye sembollere esit bolunur, her biri
bagimsiz "ya hep ya hic" calisir. Komisyon %0.05 + kayma %0.03 her yonde.

Kullanim: python -m backtest.stma --days 1825
         (varsayilan: BTC/ETH/SOL, 4h + 1d, EMA100, ATR10 x 0.5, holdout 2025-01-01)
"""
import argparse

import numpy as np
import pandas as pd

from . import data as data_mod

FEE = 0.0005
SLIP = 0.0003
COST = FEE + SLIP


def _wma(s: pd.Series, n: int) -> pd.Series:
    w = np.arange(1, n + 1, dtype=float)
    return s.rolling(n).apply(lambda x: np.dot(x, w) / w.sum(), raw=True)


def moving_average(src: pd.Series, length: int, mav: str) -> pd.Series:
    mav = mav.upper()
    if mav == "EMA":
        return src.ewm(span=length, adjust=False).mean()
    if mav == "SMA":
        return src.rolling(length).mean()
    if mav == "WMA":
        return _wma(src, length)
    if mav == "HULL":
        half = _wma(src, length // 2)
        full = _wma(src, length)
        return _wma(2 * half - full, int(round(np.sqrt(length))))
    if mav == "TILL":  # Tillson T3, volume factor 0.7 (Pine varsayilani)
        a = 0.7
        e = src
        es = []
        for _ in range(6):
            e = e.ewm(span=length, adjust=False).mean()
            es.append(e)
        c1 = -a ** 3
        c2 = 3 * a ** 2 + 3 * a ** 3
        c3 = -6 * a ** 2 - 3 * a - 3 * a ** 3
        c4 = 1 + 3 * a + a ** 3 + 3 * a ** 2
        return c1 * es[5] + c2 * es[4] + c3 * es[3] + c4 * es[2]
    raise SystemExit(f"desteklenmeyen MA tipi: {mav} (EMA/SMA/WMA/HULL/TILL)")


def stma_trend(df: pd.DataFrame, mav: str, length: int, atr_period: int,
               mult: float) -> np.ndarray:
    """Pine trend dizisinin birebir cevirisi: +1 long rejimi, -1 dis rejim."""
    c = df["close"].values
    h, l = df["high"].values, df["low"].values
    prev_c = np.roll(c, 1)
    prev_c[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev_c), np.abs(l - prev_c)))
    atr = pd.Series(tr).ewm(alpha=1.0 / atr_period, adjust=False).mean().values
    ma = moving_average(df["close"], length, mav).values

    up_raw = ma - mult * atr
    dn_raw = ma + mult * atr
    up = up_raw.copy()
    dn = dn_raw.copy()
    trend = np.ones(len(c), dtype=int)
    for i in range(1, len(c)):
        up[i] = max(up_raw[i], up[i - 1]) if c[i - 1] > up[i - 1] else up_raw[i]
        dn[i] = min(dn_raw[i], dn[i - 1]) if c[i - 1] < dn[i - 1] else dn_raw[i]
        if trend[i - 1] == -1 and c[i] > dn[i - 1]:
            trend[i] = 1
        elif trend[i - 1] == 1 and c[i] < up[i - 1]:
            trend[i] = -1
        else:
            trend[i] = trend[i - 1]
    return trend


def run_symbol(df: pd.DataFrame, mav: str, length: int, atr_period: int,
               mult: float, warmup: int):
    """Tek sembol: strateji carpani egrisi (1.0'dan baslar) + islem kayitlari."""
    c = df["close"].values
    trend = stma_trend(df, mav, length, atr_period, mult)
    eq, pos, entry_eq, entry_ts = 1.0, False, 1.0, None
    curve = np.ones(len(c))
    trades = []
    for i in range(len(c)):
        if i < warmup:
            curve[i] = eq
            continue
        if pos:
            eq *= c[i] / c[i - 1]
        flip_up = trend[i] == 1 and trend[i - 1] == -1
        flip_dn = trend[i] == -1 and trend[i - 1] == 1
        if not pos and flip_up:
            eq *= 1.0 - COST
            pos, entry_eq, entry_ts, entry_px = True, eq, df.index[i], c[i]
        elif pos and flip_dn:
            eq *= 1.0 - COST
            trades.append({"entry": entry_ts, "exit": df.index[i],
                           "entry_px": entry_px, "exit_px": c[i],
                           "ret": eq / entry_eq - 1})
            pos = False
        curve[i] = eq
    if pos:  # acik pozisyonu son barda kapat (rapor icin)
        trades.append({"entry": entry_ts, "exit": df.index[-1],
                       "entry_px": entry_px, "exit_px": c[-1],
                       "ret": eq / entry_eq - 1, "open": True})
    return pd.Series(curve, index=df.index), trades


def window_stats(total: pd.Series, trades: list, start, end, base: float):
    w = total[(total.index >= start) & (total.index < end)]
    if len(w) < 2:
        return None
    ret = w.iloc[-1] / w.iloc[0] - 1
    days = (w.index[-1] - w.index[0]).days or 1
    cagr = (1 + ret) ** (365 / days) - 1
    dd = (w / w.cummax() - 1).min()
    tw = [t for t in trades if start <= t["exit"] < end]
    wins = [t["ret"] for t in tw if t["ret"] > 0]
    losses = [t["ret"] for t in tw if t["ret"] <= 0]
    pf = (sum(wins) / abs(sum(losses))) if losses and wins else float("inf") if wins else 0.0
    mar = cagr / abs(dd) if dd else 0.0
    return (f"getiri={ret*100:+7.1f}%  CAGR={cagr*100:+6.1f}%  DD={dd*100:6.1f}%  "
            f"PF={pf:4.2f}  MAR={mar:4.2f}  islem={len(tw)} (kazanan %{100*len(wins)/len(tw):.0f})"
            if tw else f"getiri={ret*100:+7.1f}%  (islem yok)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=1825)
    ap.add_argument("--equity", type=float, default=10_000)
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    ap.add_argument("--timeframes", default="4h,1d")
    ap.add_argument("--mav", default="EMA", help="EMA/SMA/WMA/HULL/TILL")
    ap.add_argument("--length", type=int, default=100)
    ap.add_argument("--atr-period", type=int, default=10)
    ap.add_argument("--mult", type=float, default=0.5)
    ap.add_argument("--holdout", default="2025-01-01")
    ap.add_argument("--monthly", action="store_true", help="ay ay getiri tablosu da bas")
    ap.add_argument("--trades", action="store_true", help="islemleri tek tek listele")
    args = ap.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",")]
    holdout_ts = pd.Timestamp(args.holdout, tz="UTC")

    for tf in [t.strip() for t in args.timeframes.split(",")]:
        hours = 24 if tf == "1d" else int(tf.rstrip("h"))
        warmup = args.length * (6 if args.mav.upper() == "TILL" else 3)
        extra_days = int(warmup * hours / 24) + 5

        curves, all_trades, per_sym = {}, [], {}
        for sym in symbols:
            k, _ = data_mod.load(sym, tf, args.days + extra_days)
            curve, trades = run_symbol(k, args.mav, args.length,
                                       args.atr_period, args.mult, warmup)
            for t in trades:
                t["symbol"] = sym
            curves[sym], per_sym[sym] = curve, len(trades)
            all_trades.extend(trades)

        per = args.equity / len(curves)
        idx = sorted(set().union(*[s.index for s in curves.values()]))
        total = pd.Series(0.0, index=pd.DatetimeIndex(idx))
        for s in curves.values():
            total = total.add(s.reindex(idx).ffill().fillna(1.0) * per, fill_value=0.0)
        report_start = total.index[-1] - pd.Timedelta(days=args.days)
        total = total[total.index >= report_start]
        total = total / total.iloc[0] * args.equity

        line = "=" * 74
        print(line)
        print(f"  STMA [{args.mav}{args.length} +/- {args.mult}xATR({args.atr_period})]  "
              f"{tf}  long-only  {', '.join(symbols)}")
        print(line)
        far = pd.Timestamp.max.tz_localize("UTC")
        print("  TUM DONEM :", window_stats(total, all_trades, total.index[0], far, args.equity))
        print("  EGITIM    :", window_stats(total, all_trades, total.index[0], holdout_ts, args.equity))
        print("  HOLDOUT   :", window_stats(total, all_trades, holdout_ts, far, args.equity))
        print(f"  Son deger : {total.iloc[-1]:,.0f} USDT (baslangic {args.equity:,.0f}) | "
              f"islem/sembol: {', '.join(f'{k}:{v}' for k, v in per_sym.items())}")
        if args.trades:
            print("  Islemler (giris -> cikis, maliyetler dahil):")
            for t in sorted(all_trades, key=lambda x: x["entry"]):
                gun = (t["exit"] - t["entry"]).days
                tag = "  [HALA ACIK]" if t.get("open") else ""
                print(f"    {t['symbol']:<8} {t['entry'].strftime('%Y-%m-%d')} @ "
                      f"{t['entry_px']:>10,.2f} -> {t['exit'].strftime('%Y-%m-%d')} @ "
                      f"{t['exit_px']:>10,.2f}  ({gun:>4} gun)  {t['ret']*100:+7.1f}%{tag}")
        if args.monthly:
            monthly = total.resample("ME").last()
            rets = monthly.pct_change()
            rets.iloc[0] = monthly.iloc[0] / args.equity - 1
            print("  Aylik getiriler:")
            for ts, r in rets.items():
                print(f"    {ts.strftime('%Y-%m')}  {r*100:+6.1f}%  {'#' * int(abs(r) * 100)}")
        print(line)
        print()


if __name__ == "__main__":
    main()
