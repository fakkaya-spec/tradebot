"""Futbol: online güncellenen atak/defans Poisson modeli (Dixon-Coles ruhu).

Takım başına log-ölçekte atak ve defans derecesi tutulur:

    lambda_ev  = lig_ort * exp(atak_ev  - defans_dep + ev_avantajı)
    lambda_dep = lig_ort * exp(atak_dep - defans_ev)

Skor matrisi iki bağımsız Poisson'dan kurulur; düşük skorlar (0-0, 1-0, 0-1,
1-1) Dixon-Coles tau düzeltmesiyle ayarlanır. Maç sonrası dereceler Poisson
log-olabilirlik gradyanıyla küçük adımlarla güncellenir — model her tahminde
yalnızca o güne kadarki maçları bilir.
"""
import math

MAX_GOALS = 10


def _poisson_vec(lam: float, n: int = MAX_GOALS) -> list[float]:
    out = [math.exp(-lam)]
    for k in range(1, n + 1):
        out.append(out[-1] * lam / k)
    return out


class FootballPoisson:
    def __init__(self, lr: float = 0.05, home_adv: float = 0.25,
                 rho: float = -0.13, goal_cap: int = 5):
        self.lr = lr
        self.home_adv = home_adv
        self.rho = rho
        self.goal_cap = goal_cap          # farklı skorların güncellemeyi şişirmemesi için
        self.att: dict[str, float] = {}
        self.dfc: dict[str, float] = {}
        self.n: dict[str, int] = {}
        # lig başına ortalama gol (takım başı, maç başı) — yavaş EW ortalama
        self.league_mu: dict[str, float] = {}

    def matches(self, team: str) -> int:
        return self.n.get(team, 0)

    def _mu(self, league: str) -> float:
        return self.league_mu.get(league, 1.35)

    def _lambdas(self, league: str, home: str, away: str) -> tuple[float, float]:
        mu = self._mu(league)
        lh = mu * math.exp(self.att.get(home, 0.0) - self.dfc.get(away, 0.0)
                           + self.home_adv)
        la = mu * math.exp(self.att.get(away, 0.0) - self.dfc.get(home, 0.0))
        return min(lh, 6.0), min(la, 6.0)

    def _score_matrix(self, lh: float, la: float) -> list[list[float]]:
        ph = _poisson_vec(lh)
        pa = _poisson_vec(la)
        m = [[ph[i] * pa[j] for j in range(MAX_GOALS + 1)] for i in range(MAX_GOALS + 1)]
        # Dixon-Coles düşük skor düzeltmesi
        r = self.rho
        adj = {(0, 0): 1 - lh * la * r, (0, 1): 1 + lh * r,
               (1, 0): 1 + la * r, (1, 1): 1 - r}
        for (i, j), f in adj.items():
            m[i][j] *= max(f, 0.0)
        s = sum(sum(row) for row in m)
        return [[v / s for v in row] for row in m]

    def probs(self, league: str, home: str, away: str) -> dict[str, float]:
        """1X2 ve Üst 2.5 olasılıkları."""
        lh, la = self._lambdas(league, home, away)
        m = self._score_matrix(lh, la)
        p_home = sum(m[i][j] for i in range(MAX_GOALS + 1) for j in range(MAX_GOALS + 1) if i > j)
        p_draw = sum(m[i][i] for i in range(MAX_GOALS + 1))
        p_away = 1.0 - p_home - p_draw
        p_over = sum(m[i][j] for i in range(MAX_GOALS + 1) for j in range(MAX_GOALS + 1) if i + j >= 3)
        return {"H": p_home, "D": p_draw, "A": p_away,
                "O2.5": p_over, "U2.5": 1.0 - p_over}

    def update(self, league: str, home: str, away: str, gh: int, ga: int) -> None:
        lh, la = self._lambdas(league, home, away)
        gh_c = min(gh, self.goal_cap)
        ga_c = min(ga, self.goal_cap)
        # yeni takımlar (az maç görmüş) daha hızlı öğrenilir
        def rate(team: str) -> float:
            return self.lr * (1.0 + 2.0 / (1.0 + self.n.get(team, 0)))
        self.att[home] = self.att.get(home, 0.0) + rate(home) * (gh_c - lh)
        self.dfc[away] = self.dfc.get(away, 0.0) + rate(away) * (lh - gh_c)
        self.att[away] = self.att.get(away, 0.0) + rate(away) * (ga_c - la)
        self.dfc[home] = self.dfc.get(home, 0.0) + rate(home) * (la - ga_c)
        for t in (home, away):
            self.n[t] = self.n.get(t, 0) + 1
        mu = self._mu(league)
        self.league_mu[league] = mu + 0.01 * ((gh_c + ga_c) / 2.0 - mu)
