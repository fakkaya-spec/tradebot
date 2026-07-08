"""Günlük seçim çekirdeği: edge hesabı, Kelly boyutlama, top-N seçim.

Canlı bot ve backtest BU modülü ortak kullanır — ikisinin farklı davranması
mümkün değildir.
"""
from dataclasses import dataclass, field

from . import config


@dataclass
class Candidate:
    date: str            # YYYY-MM-DD
    sport: str           # football / tennis / basketball
    league: str
    event: str           # "Ev - Deplasman" ya da "OyuncuA - OyuncuB"
    market: str          # "1X2", "O/U 2.5", "ML"
    selection: str       # "H"/"D"/"A", "O2.5", oyuncu adı...
    price: float         # oynanabilir ondalık oran
    prob: float          # harmanlanmış olasılık tahmini
    edge: float = field(init=False)
    won: bool | None = None       # backtest'te sonuç
    p_model: float | None = None  # harman öncesi model olasılığı
    p_fair: float | None = None   # marjsız piyasa konsensüs olasılığı

    def __post_init__(self):
        self.edge = self.prob * self.price - 1.0

    def reblend(self, w: float) -> None:
        """Harman ağırlığını değiştirip olasılık ve edge'i yeniden hesaplar."""
        if self.p_model is None or self.p_fair is None:
            return
        self.prob = (1.0 - w) * self.p_model + w * self.p_fair
        self.edge = self.prob * self.price - 1.0


def demargin(prices: list[float]) -> list[float]:
    """Bir piyasanın tüm oranlarından marjı çıkarıp olasılığa çevirir."""
    inv = [1.0 / p for p in prices]
    s = sum(inv)
    return [v / s for v in inv]


def blend(p_model: float, p_market: float, w: float = None) -> float:
    """Model olasılığını piyasa konsensüsüne doğru çeker.

    Piyasa (kapanış konsensüsü) çoğu zaman modelden daha isabetlidir; harman
    hem kalibrasyonu düzeltir hem de yalnızca modelin GÜÇLÜ itirazlarında
    bahis açılmasını sağlar.
    """
    if w is None:
        w = config.MARKET_BLEND
    return (1.0 - w) * p_model + w * p_market


def kelly(prob: float, price: float) -> float:
    """Tam Kelly kesri (negatifse 0)."""
    b = price - 1.0
    if b <= 0:
        return 0.0
    return max(0.0, (prob * b - (1.0 - prob)) / b)


def daily_picks(candidates: list[Candidate], profile: config.Profile,
                max_picks: int = None) -> list[tuple[Candidate, float]]:
    """Adayları filtreler, edge'e göre sıralar, top-N seçer ve stake kesri atar.

    Dönen stake kesirleri o günkü bankoya çarpılır. Günlük toplam risk tavanı
    aşılırsa tüm stake'ler orantılı küçültülür.
    """
    if max_picks is None:
        max_picks = config.MAX_DAILY_PICKS
    ok = [c for c in candidates
          if c.edge >= profile.min_edge
          and config.MIN_ODDS <= c.price <= config.MAX_ODDS
          and 0.0 < c.prob < 1.0]
    ok.sort(key=lambda c: c.edge, reverse=True)
    # aynı maça iki farklı piyasadan girme — en yüksek edge'li olan kalır
    seen: set[tuple[str, str]] = set()
    picks: list[Candidate] = []
    for c in ok:
        key = (c.sport, c.event)
        if key in seen:
            continue
        seen.add(key)
        picks.append(c)
        if len(picks) >= max_picks:
            break
    stakes = [min(profile.kelly_mult * kelly(c.prob, c.price), profile.stake_cap)
              for c in picks]
    total = sum(stakes)
    if total > profile.daily_risk_cap and total > 0:
        stakes = [s * profile.daily_risk_cap / total for s in stakes]
    return [(c, s) for c, s in zip(picks, stakes) if s > 0]
