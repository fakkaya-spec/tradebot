"""Backtest raporu.

Kullanım:
  python -m betbacktest.run --start 2023-07-01            # son 3 sezon
  python -m betbacktest.run --start 2023-07-01 --best-price
  python -m betbacktest.run --sports football,tennis
"""
import argparse
from collections import defaultdict
from datetime import date

from betbot import config
from . import data
from .engine import build_daily_candidates, metrics, simulate

WARMUP_SEASONS = 2  # bahis başlamadan önce modellerin gördüğü sezon sayısı


def _pct(x: float) -> str:
    return f"{x * 100:+.1f}%"


def yearly_breakdown(res) -> dict[str, float]:
    """Sezon-yılı bazında banka değişimi (Temmuz-Haziran)."""
    out = {}
    last_by_year: dict[str, float] = {}
    first_by_year: dict[str, float] = {}
    for d, bank in res.equity:
        y = f"{d.year}-{d.year + 1}" if d.month >= 7 else f"{d.year - 1}-{d.year}"
        first_by_year.setdefault(y, bank)
        last_by_year[y] = bank
    for y in first_by_year:
        out[y] = last_by_year[y] / first_by_year[y] - 1.0
    return out


def sport_breakdown(res) -> dict[str, tuple[int, float, float]]:
    agg = defaultdict(lambda: [0, 0.0, 0.0])
    for b in res.bets:
        a = agg[b.sport]
        a[0] += 1
        a[1] += b.stake
        a[2] += b.pnl
    return {k: (v[0], v[2], v[2] / v[1] if v[1] else 0.0) for k, v in agg.items()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2023-07-01")
    ap.add_argument("--end", default=str(date.today()))
    ap.add_argument("--sports", default="football,tennis,basketball")
    ap.add_argument("--fair", default="ps", choices=["ps", "avg"],
                    help="adil fiyat referansı: Pinnacle (ps) veya piyasa ortalaması")
    ap.add_argument("--b365", action="store_true",
                    help="en iyi piyasa oranı yerine sadece B365 ile oyna (temkinli)")
    ap.add_argument("--max-picks", type=int, default=config.MAX_DAILY_PICKS)
    ap.add_argument("--bank", type=float, default=1000.0)
    args = ap.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    sports = set(args.sports.split(","))
    first_year = start.year - WARMUP_SEASONS

    print(f"Veri indiriliyor/okunuyor ({first_year} -> {end.year}) ...")
    matches: list[dict] = []
    if "football" in sports:
        matches += data.load_football(first_year, end.year if end.month >= 8 else end.year - 1)
        print(f"  futbol: {sum(1 for m in matches if m['sport'] == 'football')} maç")
    if "tennis" in sports:
        matches += data.load_tennis(first_year, end.year)
        print(f"  tenis:  {sum(1 for m in matches if m['sport'] == 'tennis')} maç")
    if "basketball" in sports:
        matches += data.load_nba(first_year, end.year - 1)
        print(f"  NBA:    {sum(1 for m in matches if m['sport'] == 'basketball')} maç")
    matches.sort(key=lambda m: m["date"])
    if not matches:
        raise SystemExit("veri yok — ağ politikasında veri alan adlarına izin verildi mi?")

    print(f"\nWalk-forward adaylar üretiliyor (bahisler {start} itibarıyla) ...")
    daily = build_daily_candidates(matches, start, end,
                                   best_price=not args.b365, fair_src=args.fair)
    n_cand = sum(len(v) for v in daily.values())
    print(f"  {len(daily)} gün, {n_cand} aday fiyatlandı")

    for pname in ("orta", "agresif"):
        prof = config.PROFILES[pname]
        res = simulate(daily, prof, start_bank=args.bank, max_picks=args.max_picks)
        m = metrics(res)
        print(f"\n===== PROFİL: {pname.upper()}  "
              f"(kelly x{prof.kelly_mult}, bahis tavanı {prof.stake_cap:.0%}, "
              f"min edge {prof.min_edge:.0%}) =====")
        print(f"  Banka: {res.start_bank:.0f} -> {res.final_bank:.0f}  "
              f"({m['final_mult']:.2f}x, yıllık {_pct(m['cagr'])})")
        print(f"  Bahis: {m['bets']}  ROI: {_pct(m['roi'])}  "
              f"isabet: {m['hit']:.1%}  ort. oran: {m['avg_odds']:.2f}")
        print(f"  Max drawdown: {-m['maxdd'] * 100:.1f}%  "
              f"kill-switch: {m['kills']} ay {res.kill_months}")
        print("  Sezon bazında:")
        for y, r in yearly_breakdown(res).items():
            print(f"    {y}: {_pct(r)}")
        print("  Spor bazında (bahis / P&L / ROI):")
        for s, (n, pnl, roi) in sorted(sport_breakdown(res).items()):
            print(f"    {s:<10} {n:>5}  {pnl:>+9.0f}  {_pct(roi)}")


if __name__ == "__main__":
    main()
