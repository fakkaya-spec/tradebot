"""Elo tabanlı modeller: tenis (yüzey harmanlı) ve NBA (MOV + ev avantajı).

Her iki model de "online" çalışır: maçlar kronolojik sırayla `update` edilir,
tahmin her zaman o ana kadarki bilgiyle yapılır — backtest doğal olarak
walk-forward olur, geleceği görme (look-ahead) mümkün değildir.
"""
import math


def _win_prob(elo_diff: float) -> float:
    return 1.0 / (1.0 + 10.0 ** (-elo_diff / 400.0))


class TennisElo:
    """FiveThirtyEight tarzı tenis Elo'su.

    Oyuncu başına bir genel + yüzey başına bir Elo tutulur; tahmin ikisinin
    harmanıdır. K faktörü oyuncunun maç sayısıyla azalır: yeni oyuncu hızlı,
    oturmuş oyuncu yavaş güncellenir.
    """

    def __init__(self, surface_weight: float = 0.5, start: float = 1500.0):
        self.sw = surface_weight
        self.start = start
        self.overall: dict[str, float] = {}
        self.surface: dict[tuple[str, str], float] = {}
        self.n: dict[str, int] = {}
        self.n_surface: dict[tuple[str, str], int] = {}

    @staticmethod
    def _k(n: int) -> float:
        return 250.0 / ((n + 5) ** 0.4)

    def matches(self, player: str) -> int:
        return self.n.get(player, 0)

    def prob(self, a: str, b: str, surface: str) -> float:
        """a oyuncusunun b'yi yenme olasılığı."""
        d_all = self.overall.get(a, self.start) - self.overall.get(b, self.start)
        d_srf = (self.surface.get((a, surface), self.start)
                 - self.surface.get((b, surface), self.start))
        return _win_prob((1 - self.sw) * d_all + self.sw * d_srf)

    def update(self, winner: str, loser: str, surface: str) -> None:
        for table, counts, key_w, key_l in (
            (self.overall, self.n, winner, loser),
            (self.surface, self.n_surface, (winner, surface), (loser, surface)),
        ):
            rw = table.get(key_w, self.start)
            rl = table.get(key_l, self.start)
            exp_w = _win_prob(rw - rl)
            kw = self._k(counts.get(key_w, 0))
            kl = self._k(counts.get(key_l, 0))
            table[key_w] = rw + kw * (1.0 - exp_w)
            table[key_l] = rl - kl * (1.0 - exp_w)
            counts[key_w] = counts.get(key_w, 0) + 1
            counts[key_l] = counts.get(key_l, 0) + 1


class NbaElo:
    """FiveThirtyEight NBA Elo'su: K=20, ev avantajı, galibiyet farkı çarpanı,
    sezonlar arası %25 ortalamaya dönüş."""

    def __init__(self, k: float = 20.0, home_adv: float = 92.0,
                 start: float = 1500.0, revert: float = 0.25):
        self.k = k
        self.home_adv = home_adv
        self.start = start
        self.revert = revert
        self.elo: dict[str, float] = {}
        self.n: dict[str, int] = {}

    def matches(self, team: str) -> int:
        return self.n.get(team, 0)

    def new_season(self) -> None:
        for t in self.elo:
            self.elo[t] += self.revert * (1505.0 - self.elo[t])

    def prob_home(self, home: str, away: str) -> float:
        d = (self.elo.get(home, self.start) + self.home_adv
             - self.elo.get(away, self.start))
        return _win_prob(d)

    def update(self, home: str, away: str, home_pts: int, away_pts: int) -> None:
        ph = self.prob_home(home, away)
        home_won = 1.0 if home_pts > away_pts else 0.0
        margin = abs(home_pts - away_pts)
        elo_diff = (self.elo.get(home, self.start) + self.home_adv
                    - self.elo.get(away, self.start))
        if home_won == 0.0:
            elo_diff = -elo_diff
        mov = ((margin + 3.0) ** 0.8) / (7.5 + 0.006 * elo_diff)
        shift = self.k * mov * (home_won - ph)
        self.elo[home] = self.elo.get(home, self.start) + shift
        self.elo[away] = self.elo.get(away, self.start) - shift
        self.n[home] = self.n.get(home, 0) + 1
        self.n[away] = self.n.get(away, 0) + 1
