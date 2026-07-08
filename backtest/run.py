"""Backtest calistirici.

Kullanim:
    python -m backtest.run --days 365                # v2 (rejim + meanrev + vol hedefleme)
    python -m backtest.run --days 365 --legacy       # v1 (v2 ozellikleri kapali)
    python -m backtest.run --days 1825 --holdout 2025-01-01
        -> egitim donemi (< tarih) ve GORULMEMIS donem (>= tarih) ayri raporlanir
    python -m backtest.run --days 1825 --holdout 2025-01-01 --compare
        -> v1 ve v2, egitim + holdout pencerelerinde yan yana karsilastirilir
"""
import argparse
import dataclasses

import pandas as pd

from bot.config import Config
from bot.strategy import SLEEVES

from . import data as data_mod
from .engine import Backtester, metrics

WARMUP_DAYS = 45  # pencere basina isinma payi (EMA200 + vol penceresi ~34 gun)


def legacy_cfg(cfg):
    return dataclasses.replace(cfg, enable_regime=False, enable_meanrev=False,
                               enable_vol_target=False)


def slice_window(data, funding, start, end):
    """Veriyi [start - warmup, end) araligina indirger; warmup payi motorun
    isinmasi icindir, islemler start civarinda baslar."""
    d, f = {}, {}
    for sym, df in data.items():
        lo = (start - pd.Timedelta(days=WARMUP_DAYS)) if start is not None else None
        d[sym] = df.loc[lo:end]
        f[sym] = funding[sym].loc[lo:end] if len(funding[sym]) else funding[sym]
    return d, f


def run_window(cfg, data, funding, start, end, equity):
    d, f = slice_window(data, funding, start, end)
    return Backtester(d, f, cfg, start_equity=equity).run()


def print_report(m: dict, result, symbols, title):
    line = "=" * 62
    print(line)
    print(f"  {title}")
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
    for sleeve in SLEEVES:
        ts_ = [t for t in result.trades if t.sleeve == sleeve]
        if not ts_:
            continue
        pnl = sum(t.pnl for t in ts_)
        print(f"    {sleeve:<9} islem={len(ts_):>3}  PnL={pnl:+10.2f} USDT")
    print("  Sembol bazinda:")
    for sym in symbols:
        ts_ = [t for t in result.trades if t.symbol == sym]
        pnl = sum(t.pnl for t in ts_)
        print(f"    {sym:<9} islem={len(ts_):>3}  PnL={pnl:+10.2f} USDT")
    print(line)


def compact_line(label, result):
    m = metrics(result)
    mar = m["yillik_getiri_cagr"] / abs(m["max_drawdown"]) if m["max_drawdown"] else 0
    return (f"    {label:<12} getiri={m['toplam_getiri']*100:+7.1f}%  "
            f"CAGR={m['yillik_getiri_cagr']*100:+6.1f}%  DD={m['max_drawdown']*100:6.1f}%  "
            f"PF={m['profit_factor']:.2f}  MAR={mar:4.2f}  islem={m['islem_sayisi']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    ap.add_argument("--equity", type=float, default=10_000)
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--legacy", action="store_true", help="v2 ozelliklerini kapat (v1 davranisi)")
    ap.add_argument("--holdout", help="YYYY-MM-DD: bu tarihten oncesi egitim, sonrasi gorulmemis donem")
    ap.add_argument("--compare", action="store_true", help="--holdout ile: v1 ve v2'yi yan yana karsilastir")
    ap.add_argument("--sweep", action="store_true",
                    help="--holdout ile: risk/kill-switch kombinasyonlarini tara")
    args = ap.parse_args()

    cfg = Config()
    if args.legacy:
        cfg = legacy_cfg(cfg)
    symbols = [s.strip() for s in args.symbols.split(",")]

    data, funding = {}, {}
    for sym in symbols:
        print(f"{sym}: veri yukleniyor...")
        k, f = data_mod.load(sym, cfg.timeframe, args.days + WARMUP_DAYS, use_cache=not args.no_cache)
        data[sym], funding[sym] = k, f
        print(f"  {len(k)} mum, {len(f)} funding kaydi")

    if args.holdout:
        holdout_ts = pd.Timestamp(args.holdout, tz="UTC")
        windows = [("EGITIM DONEMI (strateji bu veriyle tasarlandi)", None, holdout_ts),
                   ("GORULMEMIS DONEM (holdout - asil sinav)", holdout_ts, None)]
        if args.sweep:
            risks = [0.0075, 0.01, 0.015, 0.02, 0.03]
            kills = [0.08, 0.12]
            print("\n  RISK TARAMASI  (MAR = CAGR / |MaxDD| - yuksek olan iyi)")
            for wtitle, ws, we in windows:
                print(f"\n  {wtitle}")
                for ks in kills:
                    for r in risks:
                        vcfg = dataclasses.replace(cfg, risk_per_trade=r, monthly_kill_switch=ks)
                        result = run_window(vcfg, data, funding, ws, we, args.equity)
                        print(compact_line(f"r={r*100:.2f}% ks={ks*100:.0f}%", result))
            print()
            return
        if args.compare:
            variants = [("v1", legacy_cfg(Config())), ("v2", Config())]
            print("\n  KARSILASTIRMA  (v1: eski hibrit | v2: rejim + meanrev + vol hedefleme)")
            for wtitle, ws, we in windows:
                print(f"\n  {wtitle}")
                for vname, vcfg in variants:
                    result = run_window(vcfg, data, funding, ws, we, args.equity)
                    print(compact_line(vname, result))
            print()
            return
        for wtitle, ws, we in windows:
            result = run_window(cfg, data, funding, ws, we, args.equity)
            print_report(metrics(result), result, symbols, wtitle)
        return

    result = run_window(cfg, data, funding, None, None, args.equity)
    variant = "v1 (legacy)" if args.legacy else "v2"
    print_report(metrics(result), result, symbols,
                 f"HIBRIT STRATEJI [{variant}]  |  {args.days} gun  |  {', '.join(symbols)}")


if __name__ == "__main__":
    main()
