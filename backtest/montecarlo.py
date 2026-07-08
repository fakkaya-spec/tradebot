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
import dataclasses

import numpy as np
import pandas as pd

from bot.config import Config

from . import data as data_mod
from .engine import Backtester
from .run import warmup_days

BLOCK = 3  # blok bootstrap: 3'er aylik seriler korunur


def load_all(cfg, days: int):
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    data, funding = {}, {}
    for sym in symbols:
        print(f"{sym}: veri yukleniyor...")
        k, f = data_mod.load(sym, cfg.timeframe, days + warmup_days(cfg.timeframe))
        data[sym], funding[sym] = k, f
    return data, funding


def monthly_returns(cfg, data, funding, equity: float, label: str):
    print(f"Backtest kosuyor ({label})...")
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


def floor_refill_path(rets, base: float):
    """Taban tamamlama modu: ay sonunda bakiye taban altina dustuyse cepten
    tabana tamamlanir; ustundeyse dokunulmaz (bilesik calisir).
    (equity_serisi, toplam_tamamlama, tamamlama_sayisi) dondurur."""
    equity, deposits, refills = base, 0.0, 0
    path = []
    for r in rets:
        equity *= 1.0 + r
        if equity < base:
            deposits += base - equity
            equity = base
            refills += 1
        path.append(equity)
    return path, deposits, refills


def floor_refill_report(rets: pd.Series, base: float):
    path, deposits, refills = floor_refill_path(rets.to_numpy(), base)
    final = path[-1]
    net = final - base - deposits
    line = "=" * 66
    print(line)
    print(f"  TABAN TAMAMLAMA MODU  ({base:,.0f} alti tamamlanir, ustu bilesik birakilir)")
    print(line)
    print(f"  Son bakiye           : {final:>12,.0f} USDT")
    print(f"  Cepten tamamlanan    : {deposits:>12,.0f} USDT  ({refills} ay tamamlama gerekti)")
    print(f"  NET SONUC            : {net:>+12,.0f} USDT")
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

    # Taban tamamlama modu: yil boyu ay ay simule edilir
    equity = np.full(sims, base)
    deposits = np.zeros(sims)
    for m in range(years.shape[1]):
        equity = equity * (1 + years[:, m])
        short = np.maximum(0.0, base - equity)
        deposits += short
        equity += short
    floor_net = equity - base - deposits
    print(f"  TABAN TAMAMLAMA MODU (yillik, {base:,.0f} taban):")
    print(f"    net kotu %5 / MEDYAN / iyi %95: {pct(floor_net, 5):+,.0f} / "
          f"{pct(floor_net, 50):+,.0f} / {pct(floor_net, 95):+,.0f} USDT")
    print(f"    Yil icinde tamamlama gerekme olasiligi: %{(deposits > 0).mean()*100:.0f}")
    print(f"    Tamamlama gerektiginde medyan tutar   : "
          f"{np.median(deposits[deposits > 0]) if (deposits > 0).any() else 0:,.0f} USDT")
    print(f"    Eksi yil olasiligi           : %{(floor_net < 0).mean()*100:.0f}")
    print(line)
    print("  Not: Gecmis aylik getiriler evren kabul edilir; gelecegin rejimi bu")
    print("  evrenden farkliysa (orn. trend'siz yillar) gercek dagilim daha kotu")
    print("  olabilir. Secim bias'i nedeniyle medyanlari ihtiyatla ~2/3 ile carpin.")


def mc_stats(rets: pd.Series, base: float, sims: int, seed: int):
    rng = np.random.default_rng(seed)
    years = block_bootstrap_years(rets.to_numpy(), sims, 12, rng)
    growth = np.cumprod(1 + years, axis=1)
    annual = growth[:, -1] - 1
    paths = np.concatenate([np.ones((sims, 1)), growth], axis=1)
    dd = (paths / np.maximum.accumulate(paths, axis=1) - 1).min(axis=1)
    equity = np.full(sims, base)
    deposits = np.zeros(sims)
    for m in range(years.shape[1]):
        equity = equity * (1 + years[:, m])
        short = np.maximum(0.0, base - equity)
        deposits += short
        equity += short
    floor_net = equity - base - deposits
    return {
        "medyan": np.percentile(annual, 50), "kotu5": np.percentile(annual, 5),
        "iyi95": np.percentile(annual, 95), "eksi_yil": (annual < 0).mean(),
        "dd_medyan": np.percentile(dd, 50), "dd30": (dd < -0.30).mean(),
        "floor_net_medyan": np.percentile(floor_net, 50),
        "floor_net_kotu5": np.percentile(floor_net, 5),
        "tamamlama_olasiligi": (deposits > 0).mean(),
    }


def ks_comparison(rets_ks, rets_no, base, sims, seed):
    line = "=" * 66
    a = mc_stats(rets_ks, base, sims, seed)
    b = mc_stats(rets_no, base, sims, seed)
    print(line)
    print("  KIYAS: KILL-SWITCH ACIK (dur-bekle)  vs  KAPALI (tamamla-devam)")
    print(line)
    print(f"  {'':34}{'KS ACIK':>12}{'KS KAPALI':>12}")
    print(f"  {'[Gercek 5 yil]':34}")
    fa_path, fa_dep, fa_n = floor_refill_path(rets_ks.to_numpy(), base)
    fb_path, fb_dep, fb_n = floor_refill_path(rets_no.to_numpy(), base)
    print(f"  {'Son bakiye (taban modu)':34}{fa_path[-1]:>12,.0f}{fb_path[-1]:>12,.0f}")
    print(f"  {'Cepten tamamlanan':34}{fa_dep:>12,.0f}{fb_dep:>12,.0f}")
    print(f"  {'Net sonuc':34}{fa_path[-1]-base-fa_dep:>+12,.0f}{fb_path[-1]-base-fb_dep:>+12,.0f}")
    print(f"  {'En kotu ay':34}{rets_ks.min()*100:>11.1f}%{rets_no.min()*100:>11.1f}%")
    print(f"  {'[Monte Carlo - 12 ay]':34}")
    print(f"  {'Medyan yillik getiri':34}{a['medyan']*100:>+11.1f}%{b['medyan']*100:>+11.1f}%")
    print(f"  {'Kotu %5 yil':34}{a['kotu5']*100:>+11.1f}%{b['kotu5']*100:>+11.1f}%")
    print(f"  {'Iyi %95 yil':34}{a['iyi95']*100:>+11.1f}%{b['iyi95']*100:>+11.1f}%")
    print(f"  {'Eksi yil olasiligi':34}{a['eksi_yil']*100:>11.0f}%{b['eksi_yil']*100:>11.0f}%")
    print(f"  {'DD < -%30 olasiligi':34}{a['dd30']*100:>11.0f}%{b['dd30']*100:>11.0f}%")
    print(f"  {'Taban modu net (medyan)':34}{a['floor_net_medyan']:>+12,.0f}{b['floor_net_medyan']:>+12,.0f}")
    print(f"  {'Taban modu net (kotu %5)':34}{a['floor_net_kotu5']:>+12,.0f}{b['floor_net_kotu5']:>+12,.0f}")
    print(f"  {'Tamamlama gerekme olasiligi':34}{a['tamamlama_olasiligi']*100:>11.0f}%{b['tamamlama_olasiligi']*100:>11.0f}%")
    print(line)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=1825)
    ap.add_argument("--equity", type=float, default=10_000)
    ap.add_argument("--sims", type=int, default=10_000)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    cfg = Config()
    data, funding = load_all(cfg, args.days)
    rets = monthly_returns(cfg, data, funding, args.equity, "kill-switch ACIK")
    fixed_capital_report(rets, args.equity)
    floor_refill_report(rets, args.equity)
    mc_report(rets, args.equity, args.sims, np.random.default_rng(args.seed))

    cfg_no_ks = dataclasses.replace(cfg, monthly_kill_switch=9.9)  # fiilen kapali
    rets_no = monthly_returns(cfg_no_ks, data, funding, args.equity, "kill-switch KAPALI")
    ks_comparison(rets, rets_no, args.equity, args.sims, args.seed)


if __name__ == "__main__":
    main()
