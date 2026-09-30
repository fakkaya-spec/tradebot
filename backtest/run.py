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
from .engine import Backtester, metrics, timeframe_hours


def warmup_days(timeframe: str) -> int:
    """Isinma payi: 200 bar (EMA200 + vol penceresi) + guvenlik payi, takvim gunu olarak."""
    return int(200 * timeframe_hours(timeframe) / 24) + 12


def legacy_cfg(cfg):
    return dataclasses.replace(cfg, enable_regime=False, enable_meanrev=False,
                               enable_vol_target=False)


def slice_window(data, funding, start, end, wu_days):
    """Veriyi [start - warmup, end) araligina indirger; warmup payi motorun
    isinmasi icindir, islemler start civarinda baslar."""
    d, f = {}, {}
    for sym, df in data.items():
        lo = (start - pd.Timedelta(days=wu_days)) if start is not None else None
        d[sym] = df.loc[lo:end]
        f[sym] = funding[sym].loc[lo:end] if len(funding[sym]) else funding[sym]
    return d, f


def run_window(cfg, data, funding, start, end, equity, spot=False, long_only=False,
               slippage_map=None, stop_slip_extra=0.0, one_per_symbol=False):
    d, f = slice_window(data, funding, start, end, warmup_days(cfg.timeframe))
    if spot:
        cfg = dataclasses.replace(cfg, max_leverage=1.0)
        return Backtester(d, f, cfg, start_equity=equity,
                          taker_fee=0.001, long_only=True, apply_funding=False).run()
    bt = Backtester(d, f, cfg, start_equity=equity, long_only=long_only,
                    slippage_map=slippage_map, stop_slip_extra=stop_slip_extra)
    bt.one_per_symbol_side = one_per_symbol
    return bt.run()


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
    daily = result.equity_curve.resample("D").last().dropna()
    if len(daily) > 1:
        worst = daily.pct_change().min()
        print(f"  En kotu tek gun     : {worst*100:+.1f}%")
    # Funding'in kar-zarara etkisi (denetim 4.3/B6: once olc, sonra kural koy)
    ft = getattr(result, "funding_total", None)
    if ft is not None:
        gross_win = sum(t.pnl for t in result.trades if t.pnl > 0)
        pay = ft / gross_win * 100 if gross_win else 0.0
        print(f"  Funding toplami     : {-ft:+,.2f} USDT (brut karin %{abs(pay):.1f}'i)")
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
        wins = sum(t.pnl for t in ts_ if t.pnl > 0)
        losses = abs(sum(t.pnl for t in ts_ if t.pnl < 0))
        pf = wins / losses if losses else float("inf")
        print(f"    {sym:<9} islem={len(ts_):>3}  PnL={pnl:+10.2f} USDT  PF={pf:.2f}")
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
    ap.add_argument("--sweep-stops", action="store_true",
                    help="--holdout ile: basabas/iz suren/ilk stop kombinasyonlarini tara "
                         "(igne dayanikliligi deneyi)")
    ap.add_argument("--timeframe", default=None,
                    help="mum periyodu, orn. 1h (varsayilan: 4h). Ayni bar-parametreleriyle "
                         "daha kisa periyot = daha hizli/sik islem yapan varyant")
    ap.add_argument("--spot", action="store_true",
                    help="spot modu: long-only, kaldiracsiz, funding yok, %%0.1 komisyon")
    ap.add_argument("--long-only", action="store_true",
                    help="futures kosullari AYNEN korunur, sadece short girisleri atlanir")
    ap.add_argument("--slip", default=None,
                    help="sembol bazli kayma, orn. 'SOLUSDT=0.0008,ETHUSDT=0.0004' "
                         "(belirtilmeyenler varsayilan %%0.03)")
    ap.add_argument("--stop-slip", type=float, default=0.0,
                    help="stop fill'lerine EK kayma (flash-crash stresi), orn. 0.003")
    ap.add_argument("--vol-shift", action="store_true",
                    help="vol-hedefleme medyani mevcut bari dislar (denetim 2.2 duzeltmesi)")
    ap.add_argument("--one-per-symbol", action="store_true",
                    help="ayni sembol+yonde tek pozisyon (denetim 1.2 testi: "
                         "trend+breakout ust uste binmesin)")
    ap.add_argument("--quarterly", action="store_true",
                    help="ceyrek ceyrek performans dokumu (walk-forward tarzi denetim)")
    args = ap.parse_args()

    slippage_map = None
    if args.slip:
        slippage_map = {}
        for part in args.slip.split(","):
            sym, val = part.split("=")
            slippage_map[sym.strip()] = float(val)

    cfg = Config()
    if args.legacy:
        cfg = legacy_cfg(cfg)
    if args.timeframe:
        cfg = dataclasses.replace(cfg, timeframe=args.timeframe)
    if args.vol_shift:
        cfg = dataclasses.replace(cfg, vol_median_shift=True)
    symbols = [s.strip() for s in args.symbols.split(",")]

    data, funding = {}, {}
    for sym in symbols:
        print(f"{sym}: veri yukleniyor...")
        k, f = data_mod.load(sym, cfg.timeframe, args.days + warmup_days(cfg.timeframe),
                             use_cache=not args.no_cache)
        data[sym], funding[sym] = k, f
        print(f"  {len(k)} mum, {len(f)} funding kaydi")

    if args.holdout:
        holdout_ts = pd.Timestamp(args.holdout, tz="UTC")
        windows = [("EGITIM DONEMI (strateji bu veriyle tasarlandi)", None, holdout_ts),
                   ("GORULMEMIS DONEM (holdout - asil sinav)", holdout_ts, None)]
        if args.sweep_stops:
            # Igne dayanikliligi: be = basabas esigi (xATR, buyudukce stop girise
            # GEC cekilir), tr = iz suren mesafe (xATR, buyudukce genis nefes),
            # s = ilk stop (xATR). be=9.9 fiilen "basabas/iz surme kapali" demektir.
            print("\n  STOP TARAMASI  (MAR = CAGR / |MaxDD| - yuksek olan iyi)")
            for wtitle, ws, we in windows:
                print(f"\n  {wtitle}")
                base = run_window(cfg, data, funding, ws, we, args.equity)
                print(compact_line(f"MEVCUT be={cfg.breakeven_atr:.1f} tr={cfg.trail_atr:.1f} "
                                   f"s={cfg.stop_atr:.1f}", base))
                for be in (1.0, 2.0, 2.5, 9.9):
                    for tr in (3.0, 4.0, 5.0):
                        vcfg = dataclasses.replace(cfg, breakeven_atr=be, trail_atr=tr)
                        result = run_window(vcfg, data, funding, ws, we, args.equity)
                        print(compact_line(f"be={be:.1f} tr={tr:.1f} s={cfg.stop_atr:.1f}", result))
                for s in (2.5, 3.0):
                    vcfg = dataclasses.replace(cfg, stop_atr=s)
                    result = run_window(vcfg, data, funding, ws, we, args.equity)
                    print(compact_line(f"be={cfg.breakeven_atr:.1f} tr={cfg.trail_atr:.1f} "
                                       f"s={s:.1f}", result))
                # Ilk taramanin iki kazananinin (erken basabas + genis ilk stop)
                # birlesimi: etkilesim var mi?
                for be, tr, s in ((1.0, 3.0, 2.5), (1.0, 5.0, 2.5)):
                    vcfg = dataclasses.replace(cfg, breakeven_atr=be, trail_atr=tr, stop_atr=s)
                    result = run_window(vcfg, data, funding, ws, we, args.equity)
                    print(compact_line(f"be={be:.1f} tr={tr:.1f} s={s:.1f} (kombo)", result))
            print()
            return
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
            variants = [("v1", legacy_cfg(cfg)), ("v2", dataclasses.replace(cfg, enable_regime=True,
                        enable_meanrev=True, enable_vol_target=True))]
            print("\n  KARSILASTIRMA  (v1: eski hibrit | v2: rejim + meanrev + vol hedefleme)")
            for wtitle, ws, we in windows:
                print(f"\n  {wtitle}")
                for vname, vcfg in variants:
                    result = run_window(vcfg, data, funding, ws, we, args.equity)
                    print(compact_line(vname, result))
            print()
            return
        for wtitle, ws, we in windows:
            result = run_window(cfg, data, funding, ws, we, args.equity,
                                spot=args.spot, long_only=args.long_only,
                                slippage_map=slippage_map, stop_slip_extra=args.stop_slip,
                                one_per_symbol=args.one_per_symbol)
            print_report(metrics(result), result, symbols,
                         wtitle + (" [SPOT long-only]" if args.spot else "")
                         + (" [LONG-ONLY futures]" if args.long_only else ""))
        return

    if args.quarterly:
        # Walk-forward tarzi dokum: parametreler sabit oldugu icin yeniden
        # optimizasyon yok - her ceyrek bagimsiz pencere olarak kosulur.
        full = run_window(cfg, data, funding, None, None, args.equity,
                          slippage_map=slippage_map, stop_slip_extra=args.stop_slip,
                          one_per_symbol=args.one_per_symbol)
        idx = full.equity_curve.index
        print("\n  CEYREKLIK DOKUM (her ceyrek bagimsiz, 10k ile baslar)")
        q = pd.Timestamp(idx[0].year, ((idx[0].quarter - 1) * 3) + 1, 1, tz="UTC")
        while q < idx[-1]:
            q_end = q + pd.offsets.QuarterBegin(startingMonth=1)
            r = run_window(cfg, data, funding, q, q_end, args.equity,
                           slippage_map=slippage_map, stop_slip_extra=args.stop_slip,
                           one_per_symbol=args.one_per_symbol)
            if len(r.equity_curve) > 10:
                print(compact_line(f"{q.year}-Q{q.quarter}", r))
            q = q_end
        print()
        return

    result = run_window(cfg, data, funding, None, None, args.equity,
                        spot=args.spot, long_only=args.long_only,
                        slippage_map=slippage_map, stop_slip_extra=args.stop_slip,
                        one_per_symbol=args.one_per_symbol)
    variant = ("SPOT long-only" if args.spot
               else ("LONG-ONLY futures" if args.long_only
                     else ("v1 (legacy)" if args.legacy else "v2")))
    print_report(metrics(result), result, symbols,
                 f"HIBRIT STRATEJI [{variant}]  |  {args.days} gun  |  {', '.join(symbols)}")


if __name__ == "__main__":
    main()
