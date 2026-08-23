"""Hibrit strateji: uc bagimsiz "kol" (sleeve) ayni sermayeyi bolusur.

- TREND kolu:    EMA20/50 yonu + ADX>25 filtresi + momentum teyidi.
                 2xATR stop, 1.5xATR sonra basabas, 3xATR iz suren stop.
- BREAKOUT kolu: 20 gunluk Donchian kanal kirilimi (turtle), 10 gunluk
                 kanal ile cikis, 2xATR sabit stop.
- MEANREV kolu:  SADECE yatay rejimde: Bollinger disina tasan asiri
                 hareketde ortalamaya donus, orta banda cikis, yarim risk.

Rejim kapilamasi (enable_regime):
- Trend kolu yalnizca kendi yonundeki trend rejiminde acilir.
- Breakout kolu karsi trend rejiminde ACILMAZ (yatayda serbesttir - kirilim
  cogu zaman rejim daha "yatay" gorunurken gerceklesir, ADX geciken gosterge).
- Meanrev kolu yalnizca yatay rejimde acilir.

Sinyaller her zaman KAPANMIS barin degerleriyle uretilir.
"""
from dataclasses import dataclass

from .indicators import REGIME_DOWN, REGIME_RANGE, REGIME_UP

TREND = "trend"
BREAKOUT = "breakout"
MEANREV = "meanrev"
SUPERTREND = "supertrend"
SLEEVES = (TREND, BREAKOUT, MEANREV, SUPERTREND)

LONG = "long"
SHORT = "short"


def active_sleeves(cfg):
    out = []
    if getattr(cfg, "enable_trend", True):
        out.append(TREND)
    if getattr(cfg, "enable_breakout", True):
        out.append(BREAKOUT)
    if cfg.enable_meanrev:
        out.append(MEANREV)
    if getattr(cfg, "enable_supertrend", False):
        out.append(SUPERTREND)
    return tuple(out)


@dataclass
class StrategyParams:
    adx_threshold: float = 25.0
    stop_atr: float = 2.0
    breakeven_atr: float = 1.5
    trail_atr: float = 3.0
    enable_regime: bool = True
    rsi_oversold: float = 10.0
    rsi_overbought: float = 90.0


def entry_signal(sleeve: str, row, params: StrategyParams):
    """Kapanan bar icin giris sinyali: LONG, SHORT veya None.

    Makro filtre trend/breakout icin gecerlidir: fiyat EMA200 ustundeyken
    sadece long, altindayken sadece short - buyuk resme karsi islem yapilmaz.
    """
    macro_long = row.close > row.ema_macro
    regime = row.regime if params.enable_regime else None

    if sleeve == TREND:
        if row.adx > params.adx_threshold:
            if macro_long and row.ema_fast > row.ema_slow and row.close > row.ema_fast:
                if regime is None or regime == REGIME_UP:
                    return LONG
            if not macro_long and row.ema_fast < row.ema_slow and row.close < row.ema_fast:
                if regime is None or regime == REGIME_DOWN:
                    return SHORT
        return None

    if sleeve == BREAKOUT:
        if row.don_hi == row.don_hi:  # NaN kontrolu
            if macro_long and row.close > row.don_hi:
                if regime is None or regime != REGIME_DOWN:
                    return LONG
            if not macro_long and row.close < row.don_lo:
                if regime is None or regime != REGIME_UP:
                    return SHORT
        return None

    if sleeve == SUPERTREND:
        # Kuzen deneyinden dogan kol: Supertrend yonu + makro filtre.
        # Fren sistemi (stop/trailing/boyutlama) motorun genel katmanindan gelir.
        if macro_long and row.st_dir == 1:
            return LONG
        if not macro_long and row.st_dir == -1:
            return SHORT
        return None

    if sleeve == MEANREV:
        if row.bb_lower != row.bb_lower:  # NaN kontrolu
            return None
        if regime is not None and regime != REGIME_RANGE:
            return None
        if row.close < row.bb_lower and row.rsi < params.rsi_oversold:
            return LONG
        if row.close > row.bb_upper and row.rsi > params.rsi_overbought:
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
    if sleeve == MEANREV:
        # ortalamaya dondu: orta banda dokununca kar al
        return (side == LONG and row.close >= row.bb_mid) or (
            side == SHORT and row.close <= row.bb_mid
        )
    if sleeve == SUPERTREND:
        return (side == LONG and row.st_dir == -1) or (
            side == SHORT and row.st_dir == 1
        )
    raise ValueError(f"bilinmeyen kol: {sleeve}")


def initial_stop(side: str, entry_price: float, atr: float, params: StrategyParams) -> float:
    return entry_price - params.stop_atr * atr if side == LONG else entry_price + params.stop_atr * atr


def update_trailing_stop(sleeve, side, entry_price, entry_atr, best_price, atr_now, stop, params):
    """Trend ve supertrend kollarinda basabas + iz suren stop; diger kollarda
    stop sabittir (breakout'u Donchian exit, meanrev'i orta bant yonetir)."""
    if sleeve not in (TREND, SUPERTREND):
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
