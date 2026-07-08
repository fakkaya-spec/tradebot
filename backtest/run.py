"""Backtest calistirici.

Kullanim:
    python -m backtest.run --days 365
    python -m backtest.run --days 365 --symbols BTCUSDT,ETHUSDT
"""
import argparse

from bot.config import Config

from . import data as data_mod
from .engine import Backtester, metrics


def print_report(m: dict, result, symbols, days):
    line = "=" * 62
    print(line)
    print(f"  HIBRIT STRATEJI BACKTEST  |  {days} gun  |  {', '.join(symbols)}")
    print(line)
    print(f"  Baslangic sermayesi : {result.start_equity:,.0f} USDT")
    print(f"  Son sermaye         : {result.equity_curve.iloc[-1]:,.0f} USDT")
    print(f"  Toplam getiri       : {m['toplam_getiri']*100:+.1f}%")
    print(f"  Yillik getiri (CAGR): {m['yillik_getiri_cagr']*100:+.1f}%")
    print(f"  Maksimum drawdown   : {m['max_drawdown']*100:.1f}%")
    print(f"  Islem sayisi        : {m['islem_sayisi']}")
    print(f"  Kazanma orani       : {m['kazanma_orani']*100:.1f}%")
    print(f"  Profit factor       : {m['profit_factor']:.2f}")
    if m["funding_tahmini_mi"]:
        print("  ! Funding verisi eksikti, sabit 0.01%/8h varsayildi.")
    print(line)
    print("  Aylik getiriler:")
    for ts, r in m["aylik_getiriler"].items():
        bar = "#" * int(abs(r) * 200)
        print(f"    {ts.strftime('%Y-%m')}  {r*100:+6.1f}%  {bar}")
    print(line)
    print("  Kol bazinda:")
    for sleeve in ("trend", "breakout"):
        ts_ = [t for t in result.trades if t.sleeve == sleeve]
        pnl = sum(t.pnl for t in ts_)
        print(f"    {sleeve:<9} islem={len(ts_):>3}  PnL={pnl:+10.2f} USDT")
    print("  Sembol bazinda:")
    for sym in symbols:
        ts_ = [t for t in result.trades if t.symbol == sym]
        pnl = sum(t.pnl for t in ts_)
        print(f"    {sym:<9} islem={len(ts_):>3}  PnL={pnl:+10.2f} USDT")
    print(line)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    ap.add_argument("--equity", type=float, default=10_000)
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    cfg = Config()
    symbols = [s.strip() for s in args.symbols.split(",")]

    data, funding = {}, {}
    for sym in symbols:
        print(f"{sym}: veri yukleniyor...")
        # warmup icin istenen aralige 45 gun eklenir (EMA200 ~33 gun + pay)
        k, f = data_mod.load(sym, cfg.timeframe, args.days + 45, use_cache=not args.no_cache)
        data[sym], funding[sym] = k, f
        print(f"  {len(k)} mum, {len(f)} funding kaydi")

    result = Backtester(data, funding, cfg, start_equity=args.equity).run()
    print_report(metrics(result), result, symbols, args.days)


if __name__ == "__main__":
    main()
