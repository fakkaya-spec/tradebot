"""Teknik indikatorler. Backtest ve canli bot ayni fonksiyonu kullanir."""
import numpy as np
import pandas as pd

REGIME_UP, REGIME_RANGE, REGIME_DOWN = 1, 0, -1


def rma(series: pd.Series, period: int) -> pd.Series:
    """Wilder yumusatmasi (RMA)."""
    return series.ewm(alpha=1.0 / period, adjust=False).mean()


def add_indicators(df: pd.DataFrame, cfg) -> pd.DataFrame:
    """OHLCV DataFrame'ine tum strateji kolonlarini ekler.

    Eklenenler: ema_fast/slow/macro, atr, adx, Donchian kanallari, RSI,
    Bollinger bantlari, vol_scale (volatilite hedeflemesi carpani) ve regime
    (1 trend-yukari, 0 yatay, -1 trend-asagi).

    Donchian kanallari mevcut bari DISLAR (shift(1)) - kirilim ancak onceki
    kanalin disina kapanista tetiklenir, ileri bakis (lookahead) yoktur.
    """
    df = df.copy()
    h, l, c = df["high"], df["low"], df["close"]

    df["ema_fast"] = c.ewm(span=cfg.ema_fast, adjust=False).mean()
    df["ema_slow"] = c.ewm(span=cfg.ema_slow, adjust=False).mean()
    df["ema_macro"] = c.ewm(span=cfg.ema_macro, adjust=False).mean()

    prev_close = c.shift(1)
    tr = pd.concat([h - l, (h - prev_close).abs(), (l - prev_close).abs()], axis=1).max(axis=1)
    df["atr"] = rma(tr, cfg.atr_period)

    up = h.diff()
    down = -l.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    tr_s = rma(tr, cfg.adx_period)
    plus_di = 100 * rma(plus_dm, cfg.adx_period) / tr_s
    minus_di = 100 * rma(minus_dm, cfg.adx_period) / tr_s
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    df["adx"] = rma(dx.fillna(0.0), cfg.adx_period)

    df["don_hi"] = h.rolling(cfg.donchian_entry).max().shift(1)
    df["don_lo"] = l.rolling(cfg.donchian_entry).min().shift(1)
    df["don_exit_hi"] = h.rolling(cfg.donchian_exit).max().shift(1)
    df["don_exit_lo"] = l.rolling(cfg.donchian_exit).min().shift(1)

    # RSI (Wilder) - ortalamaya donus kolu icin kisa periyot
    delta = c.diff()
    gain = rma(delta.clip(lower=0), cfg.rsi_period)
    loss = rma((-delta).clip(lower=0), cfg.rsi_period)
    rs = gain / loss.replace(0, np.nan)
    df["rsi"] = (100 - 100 / (1 + rs)).fillna(100.0)

    # Bollinger bantlari
    mid = c.rolling(cfg.bb_period).mean()
    std = c.rolling(cfg.bb_period).std()
    df["bb_mid"] = mid
    df["bb_upper"] = mid + cfg.bb_std * std
    df["bb_lower"] = mid - cfg.bb_std * std

    # Volatilite hedeflemesi: goreli ATR kendi medyaninin ustundeyse boyut kucultulur
    rel_atr = df["atr"] / c
    med = rel_atr.rolling(cfg.vol_window).median()
    df["vol_scale"] = (med / rel_atr).clip(0.5, 1.5).fillna(1.0)

    # Rejim: guclu ADX + fiyat EMA200'un dogru tarafinda + EMA200 egimi ayni yonde
    slope = df["ema_macro"].diff(cfg.regime_slope_bars)
    trending = df["adx"] > cfg.adx_threshold
    trend_up = trending & (c > df["ema_macro"]) & (slope > 0)
    trend_down = trending & (c < df["ema_macro"]) & (slope < 0)
    df["regime"] = np.select([trend_up, trend_down], [REGIME_UP, REGIME_DOWN], default=REGIME_RANGE)
    return df
