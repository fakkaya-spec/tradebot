"""Binance USDT-M Futures gecmis verisi: 4h mumlar + funding oranlari.

Halka acik endpointler kullanilir (API anahtari gerekmez). Indirilen veri
backtest/cache/ altina yazilir; tekrar calistirmada oradan okunur.
"""
import os
import time

import pandas as pd
import requests

BASE = "https://fapi.binance.com"
CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")


def _get(path: str, params: dict) -> list:
    for attempt in range(5):
        resp = requests.get(BASE + path, params=params, timeout=30)
        if resp.status_code == 429:  # rate limit
            time.sleep(2 ** attempt)
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError(f"Binance API {path} yanit vermedi")


def fetch_klines(symbol: str, interval: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    rows = []
    cursor = start_ms
    while cursor < end_ms:
        batch = _get("/fapi/v1/klines", {
            "symbol": symbol, "interval": interval,
            "startTime": cursor, "endTime": end_ms, "limit": 1500,
        })
        if not batch:
            break
        rows.extend(batch)
        cursor = batch[-1][0] + 1
        if len(batch) < 1500:
            break
        time.sleep(0.3)
    df = pd.DataFrame(rows, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_base", "taker_quote", "ignore",
    ])
    df = df[["open_time", "open", "high", "low", "close", "volume"]].astype(float)
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df.set_index("ts")[["open", "high", "low", "close", "volume"]]


def fetch_funding(symbol: str, start_ms: int, end_ms: int) -> pd.Series:
    rows = []
    cursor = start_ms
    while cursor < end_ms:
        batch = _get("/fapi/v1/fundingRate", {
            "symbol": symbol, "startTime": cursor, "endTime": end_ms, "limit": 1000,
        })
        if not batch:
            break
        rows.extend(batch)
        cursor = int(batch[-1]["fundingTime"]) + 1
        if len(batch) < 1000:
            break
        time.sleep(0.3)
    if not rows:
        return pd.Series(dtype=float)
    s = pd.Series(
        [float(r["fundingRate"]) for r in rows],
        index=pd.to_datetime([int(r["fundingTime"]) for r in rows], unit="ms", utc=True),
    )
    # funding zamanlarini 8 saatlik grid'e oturt (00/08/16 UTC)
    s.index = s.index.round("h")
    return s[~s.index.duplicated(keep="last")]


def load(symbol: str, interval: str, days: int, use_cache: bool = True):
    """(mumlar, funding) dondurur; cache varsa indirmez."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    kpath = os.path.join(CACHE_DIR, f"{symbol}-{interval}-{days}d.csv")
    fpath = os.path.join(CACHE_DIR, f"{symbol}-funding-{days}d.csv")

    if use_cache and os.path.exists(kpath) and os.path.exists(fpath):
        klines = pd.read_csv(kpath, index_col=0, parse_dates=True)
        funding = pd.read_csv(fpath, index_col=0, parse_dates=True).iloc[:, 0]
        return klines, funding

    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 86_400_000
    klines = fetch_klines(symbol, interval, start_ms, end_ms)
    funding = fetch_funding(symbol, start_ms, end_ms)
    klines.to_csv(kpath)
    funding.to_frame("rate").to_csv(fpath)
    return klines, funding
