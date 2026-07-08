"""Hibrit strateji: iki bagimsiz "kol" (sleeve) ayni sermayeyi bolusur.

- TREND kolu:    EMA20/50 yonu + ADX>25 filtresi + momentum teyidi.
                 2xATR stop, 1.5xATR sonra basabas, 3xATR iz suren stop.
- BREAKOUT kolu: 20 gunluk Donchian kanal kirilimi (turtle), 10 gunluk
                 kanal ile cikis, 2xATR sabit stop.

Sinyaller her zaman KAPANMIS barin degerleriyle uretilir.
"""
from dataclasses import dataclass

TREND = "trend"
BREAKOUT = "breakout"
SLEEVES = (TREND, BREAKOUT)

LONG = "long"
SHORT = "short"


@dataclass
class StrategyParams:
    adx_threshold: float = 25.0
    stop_atr: float = 2.0
    breakeven_atr: float = 1.5
    trail_atr: float = 3.0


def entry_signal(sleeve: str, row, params: StrategyParams):
    """Kapanan bar icin giris sinyali: LONG, SHORT veya None."""
    if sleeve == TREND:
        if row.adx > params.adx_threshold:
            if row.ema_fast > row.ema_slow and row.close > row.ema_fast:
                return LONG
            if row.ema_fast < row.ema_slow and row.close < row.ema_fast:
                return SHORT
        return None
    if sleeve == BREAKOUT:
        if row.don_hi == row.don_hi:  # NaN kontrolu
            if row.close > row.don_hi:
                return LONG
            if row.close < row.don_lo:
                return SHORT
        return None
    raise ValueError(f"bilinmeyen kol: {sleeve}")


def exit_signal(sleeve: str, row, side: str) -> bool:
    """Stop haricindeki kural bazli cikis."""
    if sleeve == TREND:
        return (side == LONG and row.ema_fast < row.ema_slow) or (
            side == SHORT and row.ema_fast > row.ema_slow
        )
    if sleeve == BREAKOUT:
        return (side == LONG and row.close < row.don_exit_lo) or (
            side == SHORT and row.close > row.don_exit_hi
        )
    raise ValueError(f"bilinmeyen kol: {sleeve}")


def initial_stop(side: str, entry_price: float, atr: float, params: StrategyParams) -> float:
    return entry_price - params.stop_atr * atr if side == LONG else entry_price + params.stop_atr * atr


def update_trailing_stop(sleeve, side, entry_price, entry_atr, best_price, atr_now, stop, params):
    """Trend kolunda basabas + iz suren stop; breakout kolunda stop sabittir
    (cikisi Donchian exit kanali yonetir). Yeni stop degerini dondurur."""
    if sleeve != TREND:
        return stop
    if side == LONG:
        if best_price - entry_price >= params.breakeven_atr * entry_atr:
            stop = max(stop, entry_price)
            stop = max(stop, best_price - params.trail_atr * atr_now)
    else:
        if entry_price - best_price >= params.breakeven_atr * entry_atr:
            stop = min(stop, entry_price)
            stop = min(stop, best_price + params.trail_atr * atr_now)
    return stop


def funding_blocks_entry(side: str, funding_rate: float, limit: float) -> bool:
    """Pozisyon yonundeki funding asiriysa yeni giris engellenir."""
    if side == LONG:
        return funding_rate > limit
    return funding_rate < -limit
