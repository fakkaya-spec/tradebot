"""Teknik indikatorler. Backtest ve canli bot ayni fonksiyonu kullanir."""
import numpy as np
import pandas as pd


def rma(series: pd.Series, period: int) -> pd.Series:
    """Wilder yumusatmasi (RMA)."""
    return series.ewm(alpha=1.0 / period, adjust=False).mean()


def add_indicators(
    df: pd.DataFrame,
    ema_fast: int = 20,
    ema_slow: int = 50,
    adx_period: int = 14,
    atr_period: int = 14,
    donchian_entry: int = 120,
    donchian_exit: int = 60,
    ema_macro: int = 200,
) -> pd.DataFrame:
    """OHLCV DataFrame'ine ema_fast, ema_slow, atr, adx ve Donchian kanallarini ekler.

    Donchian kanallari mevcut bari DISLAR (shift(1)) - kirilim ancak onceki
    kanalin disina kapanista tetiklenir, ileri bakis (lookahead) yoktur.
    """
    df = df.copy()
    h, l, c = df["high"], df["low"], df["close"]

    df["ema_fast"] = c.ewm(span=ema_fast, adjust=False).mean()
    df["ema_slow"] = c.ewm(span=ema_slow, adjust=False).mean()
    df["ema_macro"] = c.ewm(span=ema_macro, adjust=False).mean()

    prev_close = c.shift(1)
    tr = pd.concat([h - l, (h - prev_close).abs(), (l - prev_close).abs()], axis=1).max(axis=1)
    df["atr"] = rma(tr, atr_period)

    up = h.diff()
    down = -l.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    tr_s = rma(tr, adx_period)
    plus_di = 100 * rma(plus_dm, adx_period) / tr_s
    minus_di = 100 * rma(minus_dm, adx_period) / tr_s
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    df["adx"] = rma(dx.fillna(0.0), adx_period)

    df["don_hi"] = h.rolling(donchian_entry).max().shift(1)
    df["don_lo"] = l.rolling(donchian_entry).min().shift(1)
    df["don_exit_hi"] = h.rolling(donchian_exit).max().shift(1)
    df["don_exit_lo"] = l.rolling(donchian_exit).min().shift(1)
    return df
