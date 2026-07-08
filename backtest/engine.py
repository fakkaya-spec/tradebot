"""Portfoy seviyesinde backtest motoru.

Canli botla AYNI strateji ve risk modullerini kullanir (bot.strategy,
bot.risk) - backtest'te gorulen davranis canli davranistir.

Gerceklik varsayimlari:
- Girisler sinyal barinin kapanisinda, taker komisyonu + slippage ile.
- Stop'lar bar icinde (low/high) kontrol edilir, stop fiyati + slippage ile dolar.
- Ayni barda once stop, sonra sinyal cikisi degerlendirilir (kotumser sira).
- Funding her 8 saatte (00/08/16 UTC) acik pozisyonun nominali uzerinden
  gercek oranlarla islenir; oran yoksa 0.01%/8h varsayilir (long oder).
- Lookahead yok: tum sinyaller kapanmis bar degerlerinden uretilir,
  Donchian kanallari mevcut bari dislar.
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from bot.indicators import add_indicators
from bot.risk import MonthlyKillSwitch, RiskParams, position_size
from bot.strategy import (LONG, MEANREV, SHORT, StrategyParams, active_sleeves,
                          entry_signal, exit_signal, funding_blocks_entry,
                          initial_stop, update_trailing_stop)

TAKER_FEE = 0.0005
SLIPPAGE = 0.0003
DEFAULT_FUNDING = 0.0001  # oran verisi yoksa: 0.01% / 8h, long oder


def timeframe_hours(tf: str) -> int:
    """'4h' -> 4, '1h' -> 1, '1d' -> 24"""
    tf = tf.strip().lower()
    if tf.endswith("d"):
        return int(tf[:-1]) * 24
    if tf.endswith("h"):
        return int(tf[:-1])
    raise ValueError(f"desteklenmeyen timeframe: {tf}")


@dataclass
class Position:
    symbol: str
    sleeve: str
    side: str
    qty: float
    entry: float
    stop: float
    best: float
    entry_atr: float
    entry_time: pd.Timestamp
    funding_paid: float = 0.0


@dataclass
class Trade:
    symbol: str
    sleeve: str
    side: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry: float
    exit: float
    qty: float
    pnl: float
    reason: str


@dataclass
class Result:
    equity_curve: pd.Series
    trades: list = field(default_factory=list)
    start_equity: float = 0.0
    funding_estimated: bool = False


class Backtester:
    def __init__(self, data: dict, funding: dict, cfg, start_equity: float = 10_000.0):
        """data: {symbol: OHLCV DataFrame}, funding: {symbol: Series veya bos}."""
        self.cfg = cfg
        self.params = StrategyParams(cfg.adx_threshold, cfg.stop_atr, cfg.breakeven_atr,
                                     cfg.trail_atr, enable_regime=cfg.enable_regime,
                                     rsi_oversold=cfg.rsi_oversold,
                                     rsi_overbought=cfg.rsi_overbought)
        self.risk = RiskParams(cfg.risk_per_trade, cfg.max_leverage,
                               cfg.max_position_notional_pct, cfg.monthly_kill_switch,
                               max_same_direction=cfg.max_same_direction)
        self.sleeves = active_sleeves(cfg)
        self.funding = funding
        self.funding_estimated = any(f is None or len(f) == 0 for f in funding.values())
        self.data = {sym: add_indicators(df, cfg) for sym, df in data.items()}
        # stop sonrasi ayni yone yeniden giris yasagi: (symbol, sleeve) -> (side, yasak bitis ts)
        self.cooldowns = {}
        self.bar_hours = timeframe_hours(cfg.timeframe)
        self.cooldown_delta = pd.Timedelta(hours=self.bar_hours * cfg.cooldown_bars)
        self.start_equity = start_equity
        self.cash = start_equity
        self.positions = {}  # (symbol, sleeve) -> Position
        self.trades = []
        self.kill_switch = MonthlyKillSwitch(cfg.monthly_kill_switch)

    # --- yardimcilar ---------------------------------------------------
    def _funding_rate(self, symbol, ts):
        s = self.funding.get(symbol)
        if s is None or len(s) == 0:
            return DEFAULT_FUNDING
        exact = s.get(ts)
        if exact is not None and exact == exact:
            return float(exact)
        prior = s.loc[:ts]
        return float(prior.iloc[-1]) if len(prior) else DEFAULT_FUNDING

    def _open_notional(self, marks):
        return sum(p.qty * marks.get(p.symbol, p.entry) for p in self.positions.values())

    def _unrealized(self, marks):
        return sum(
            p.qty * (marks.get(p.symbol, p.entry) - p.entry) * (1 if p.side == LONG else -1)
            for p in self.positions.values()
        )

    def _close(self, key, pos, ts, raw_price, reason):
        direction = 1 if pos.side == LONG else -1
        fill = raw_price * (1 - direction * SLIPPAGE)
        gross = pos.qty * (fill - pos.entry) * direction
        fees = pos.qty * (pos.entry + fill) * TAKER_FEE
        pnl = gross - fees - pos.funding_paid
        self.cash += gross - pos.qty * fill * TAKER_FEE  # giris komisyonu aciliste dusuldu
        self.trades.append(Trade(pos.symbol, pos.sleeve, pos.side, pos.entry_time, ts,
                                 pos.entry, fill, pos.qty, pnl, reason))
        del self.positions[key]
        if reason == "stop":
            self.cooldowns[key] = (pos.side, ts + self.cooldown_delta)

    def _try_open(self, symbol, sleeve, side, row, ts, equity, marks):
        cd = self.cooldowns.get((symbol, sleeve))
        if cd and cd[0] == side and ts < cd[1]:
            return
        same_dir = sum(1 for p in self.positions.values() if p.side == side)
        if same_dir >= self.risk.max_same_direction:
            return
        rate = self._funding_rate(symbol, ts)
        if funding_blocks_entry(side, rate, self.cfg.funding_limit):
            return
        atr = float(row.atr)
        if not np.isfinite(atr) or atr <= 0:
            return
        stop_dist = self.params.stop_atr * atr
        risk_scale = self.cfg.meanrev_risk_mult if sleeve == MEANREV else 1.0
        if self.cfg.enable_vol_target:
            risk_scale *= float(row.vol_scale)
        qty = position_size(equity, float(row.close), stop_dist, self._open_notional(marks),
                            self.risk, risk_scale)
        if qty <= 0:
            return
        direction = 1 if side == LONG else -1
        fill = float(row.close) * (1 + direction * SLIPPAGE)
        self.cash -= qty * fill * TAKER_FEE
        self.positions[(symbol, sleeve)] = Position(
            symbol, sleeve, side, qty, fill,
            initial_stop(side, fill, atr, self.params), fill, atr, ts,
        )

    # --- ana dongu ------------------------------------------------------
    def run(self) -> Result:
        timeline = sorted(set().union(*[df.index for df in self.data.values()]))
        warmup = max(self.cfg.donchian_entry + 1, self.cfg.ema_macro, self.cfg.vol_window)
        equity_curve = {}
        marks = {}

        for ts in timeline:
            rows = {}
            for sym, df in self.data.items():
                if ts in df.index:
                    loc = df.index.get_loc(ts)
                    if loc >= warmup:
                        rows[sym] = df.iloc[loc]
                        marks[sym] = float(df.iloc[loc].close)

            # 1) funding tahsilati (8 saatlik grid; gunluk barlarda gunun 3 periyodu toplanir)
            if self.bar_hours >= 24:
                for pos in self.positions.values():
                    s = self.funding.get(pos.symbol)
                    window = s.loc[ts:ts + pd.Timedelta(hours=self.bar_hours)] if s is not None and len(s) else None
                    rate = float(window.sum()) if window is not None and len(window) else \
                        DEFAULT_FUNDING * 3 * (self.bar_hours / 24)
                    mark = marks.get(pos.symbol, pos.entry)
                    cost = rate * pos.qty * mark * (1 if pos.side == LONG else -1)
                    pos.funding_paid += cost
                    self.cash -= cost
            elif ts.hour in (0, 8, 16) and ts.minute == 0:
                for pos in self.positions.values():
                    rate = self._funding_rate(pos.symbol, ts)
                    mark = marks.get(pos.symbol, pos.entry)
                    cost = rate * pos.qty * mark * (1 if pos.side == LONG else -1)
                    pos.funding_paid += cost
                    self.cash -= cost

            # 2) acik pozisyonlar: stop -> sinyal cikisi -> trailing guncelle
            for key in list(self.positions):
                sym, sleeve = key
                if sym not in rows:
                    continue
                pos, row = self.positions[key], rows[sym]
                pos.best = max(pos.best, float(row.high)) if pos.side == LONG else min(pos.best, float(row.low))
                stop_hit = (pos.side == LONG and float(row.low) <= pos.stop) or (
                    pos.side == SHORT and float(row.high) >= pos.stop)
                if stop_hit:
                    self._close(key, pos, ts, pos.stop, "stop")
                    continue
                if exit_signal(sleeve, row, pos.side):
                    self._close(key, pos, ts, float(row.close), "sinyal")
                    continue
                pos.stop = update_trailing_stop(sleeve, pos.side, pos.entry, pos.entry_atr,
                                                pos.best, float(row.atr), pos.stop, self.params)

            equity = self.cash + self._unrealized(marks)

            # 3) aylik kill-switch
            if self.kill_switch.update(ts, equity):
                for key in list(self.positions):
                    pos = self.positions[key]
                    self._close(key, pos, ts, marks.get(pos.symbol, pos.entry), "kill-switch")
                equity = self.cash

            # 4) yeni girisler
            if not self.kill_switch.tripped:
                for sym, row in rows.items():
                    for sleeve in self.sleeves:
                        if (sym, sleeve) in self.positions:
                            continue
                        side = entry_signal(sleeve, row, self.params)
                        if side:
                            self._try_open(sym, sleeve, side, row, ts, equity, marks)

            equity_curve[ts] = self.cash + self._unrealized(marks)

        return Result(pd.Series(equity_curve), self.trades, self.start_equity, self.funding_estimated)


def metrics(result: Result) -> dict:
    eq = result.equity_curve
    ret = eq.iloc[-1] / result.start_equity - 1
    days = (eq.index[-1] - eq.index[0]).days or 1
    cagr = (eq.iloc[-1] / result.start_equity) ** (365 / days) - 1
    dd = (eq / eq.cummax() - 1).min()
    wins = [t for t in result.trades if t.pnl > 0]
    losses = [t for t in result.trades if t.pnl <= 0]
    gross_win = sum(t.pnl for t in wins)
    gross_loss = -sum(t.pnl for t in losses)
    monthly = eq.resample("ME").last()
    monthly_ret = monthly.pct_change()
    monthly_ret.iloc[0] = monthly.iloc[0] / result.start_equity - 1
    return {
        "toplam_getiri": ret,
        "yillik_getiri_cagr": cagr,
        "max_drawdown": dd,
        "islem_sayisi": len(result.trades),
        "kazanma_orani": len(wins) / len(result.trades) if result.trades else 0.0,
        "profit_factor": gross_win / gross_loss if gross_loss > 0 else float("inf"),
        "aylik_getiriler": monthly_ret,
        "funding_tahmini_mi": result.funding_estimated,
    }
