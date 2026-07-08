"""Monte Carlo + sabit sermaye analizi: "backtest tek bir tarih, baskalari nasil olurdu?"

1) SABIT SERMAYE MODU: her ay basinda kasa 10k'ya esitlenir - kar cekilir,
   zarar cepten tamamlanir. Gercek aylik getirilerle birebir hesaplanir
   (boyutlandirma yuzde bazli oldugu icin kesin sonuctur, yaklasiklama degil).

2) MONTE CARLO: gercek aylik getiriler 3'er aylik bloklar halinde (seri
   korunarak) yeniden dizilir; binlerce alternatif 12 aylik yil uretilir.
   Cikti: eksi yil olasiligi, drawdown dagilimi, medyan/kotu/iyi senaryolar.

Kullanim: python -m backtest.montecarlo --days 1825 --sims 10000
"""
import argparse

import numpy as np
import pandas as pd

from bot.config import Config

from . import data as data_mod
from .engine import Backtester
from .run import warmup_days

BLOCK = 3  # blok bootstrap: 3'er aylik seriler korunur


def monthly_returns(days: int, equity: float):
    cfg = Config()
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    data, funding = {}, {}
    for sym in symbols:
        print(f"{sym}: veri yukleniyor...")
        k, f = data_mod.load(sym, cfg.timeframe, days + warmup_days(cfg.timeframe))
        data[sym], funding[sym] = k, f
    print("Backtest kosuyor...")
    result = Backtester(data, funding, cfg, start_equity=equity).run()
    eq = result.equity_curve
    m = eq.resample("ME").last()
    rets = m.pct_change()
    rets.iloc[0] = m.iloc[0] / equity - 1
    return rets.dropna()


def fixed_capital_report(rets: pd.Series, base: float):
    pnl = base * rets
    withdrawals = pnl[pnl > 0].sum()
    refills = -pnl[pnl < 0].sum()
    net = pnl.sum()
    running = pnl.cumsum()
    worst_hole = running.min()
    compound_final = base * (1 + rets).prod()
    line = "=" * 66
    print(line)
    print(f"  SABIT SERMAYE MODU  (her ay {base:,.0f} USDT ile baslanir)")
    print(line)
    print(f"  Ay sayisi            : {len(rets)}  (arti: {(rets > 0).sum()}, eksi: {(rets <= 0).sum()})")
    print(f"  Toplam cekilen kar   : {withdrawals:>12,.0f} USDT")
    print(f"  Cepten tamamlanan    : {refills:>12,.0f} USDT")
    print(f"  NET SONUC            : {net:>+12,.0f} USDT")
    print(f"  En derin cukur       : {worst_hole:>+12,.0f} USDT  (kumulatif cepten koyma dibi)")
    print(f"  Kiyas - bilesik mod  : {base:,.0f} -> {compound_final:,.0f} USDT "
          f"(net {compound_final - base:+,.0f})")
    print(line)


def block_bootstrap_years(rets: np.ndarray, sims: int, horizon: int, rng) -> np.ndarray:
    """(sims, horizon) aylik getiri matrisi - BLOCK'lu diziler korunarak."""
    n = len(rets)
    starts_per_year = int(np.ceil(horizon / BLOCK))
    idx = rng.integers(0, n - BLOCK + 1, size=(sims, starts_per_year))
    blocks = np.stack([rets[i:i + BLOCK] for i in range(n - BLOCK + 1)])
    return blocks[idx].reshape(sims, -1)[:, :horizon]


def mc_report(rets: pd.Series, base: float, sims: int, rng):
    r = rets.to_numpy()
    years = block_bootstrap_years(r, sims, 12, rng)

    growth = np.cumprod(1 + years, axis=1)
    annual = growth[:, -1] - 1
    paths = np.concatenate([np.ones((sims, 1)), growth], axis=1)
    dd = (paths / np.maximum.accumulate(paths, axis=1) - 1).min(axis=1)
    fixed_net = base * years.sum(axis=1)

    pct = lambda a, q: np.percentile(a, q)
    line = "=" * 66
    print(f"  MONTE CARLO  ({sims:,} simulasyon, 12 aylik ufuk, {BLOCK} aylik blok bootstrap)")
    print(line)
    print("  BILESIK MOD (yillik getiri):")
    print(f"    kotu %5     : {pct(annual, 5)*100:+7.1f}%")
    print(f"    kotu %25    : {pct(annual, 25)*100:+7.1f}%")
    print(f"    MEDYAN      : {pct(annual, 50)*100:+7.1f}%")
    print(f"    iyi %75     : {pct(annual, 75)*100:+7.1f}%")
    print(f"    iyi %95     : {pct(annual, 95)*100:+7.1f}%")
    print(f"    Eksi yil olasiligi           : %{(annual < 0).mean()*100:.0f}")
    print(f"    Yil sonu > +%50 olasiligi    : %{(annual > 0.5).mean()*100:.0f}")
    print("  YIL ICI MAX DRAWDOWN (ay sonu bazli - gun ici gercekte daha derindir):")
    print(f"    medyan      : {pct(dd, 50)*100:6.1f}%")
    print(f"    kotu %10    : {pct(dd, 10)*100:6.1f}%")
    print(f"    DD < -%20 olasiligi          : %{(dd < -0.20).mean()*100:.0f}")
    print(f"    DD < -%30 olasiligi          : %{(dd < -0.30).mean()*100:.0f}")
    print(f"    DD < -%40 olasiligi          : %{(dd < -0.40).mean()*100:.0f}")
    print(f"  SABIT SERMAYE MODU (yillik net, {base:,.0f} USDT taban):")
    print(f"    kotu %5 / MEDYAN / iyi %95   : {pct(fixed_net, 5):+,.0f} / "
          f"{pct(fixed_net, 50):+,.0f} / {pct(fixed_net, 95):+,.0f} USDT")
    print(f"    Eksi yil olasiligi           : %{(fixed_net < 0).mean()*100:.0f}")
    print(line)
    print("  Not: Gecmis aylik getiriler evren kabul edilir; gelecegin rejimi bu")
    print("  evrenden farkliysa (orn. trend'siz yillar) gercek dagilim daha kotu")
    print("  olabilir. Secim bias'i nedeniyle medyanlari ihtiyatla ~2/3 ile carpin.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=1825)
    ap.add_argument("--equity", type=float, default=10_000)
    ap.add_argument("--sims", type=int, default=10_000)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    rets = monthly_returns(args.days, args.equity)
    fixed_capital_report(rets, args.equity)
    mc_report(rets, args.equity, args.sims, np.random.default_rng(args.seed))


if __name__ == "__main__":
    main()
