"""Risk cekirdegi: pozisyon boyutu, kaldirac tavani, aylik kill-switch.

Bu modul stratejiden bagimsizdir - hangi sinyal gelirse gelsin buradaki
sinirlar asilamaz.
"""
from dataclasses import dataclass


@dataclass
class RiskParams:
    risk_per_trade: float = 0.01          # islem basina ozsermaye riski
    max_leverage: float = 3.0             # toplam nominal / ozsermaye tavani
    max_position_notional_pct: float = 1.0  # tek pozisyon nominal tavani (x ozsermaye)
    monthly_kill_switch: float = 0.08     # aylik zarar limiti
    min_size_fraction: float = 0.25       # kaldirac tavani boyutu bunun altina dusurursa isleme girme
    max_same_direction: int = 3           # ayni yonde ayni anda acik pozisyon tavani (korelasyon freni)


def position_size(
    equity: float,
    price: float,
    stop_distance: float,
    open_notional: float,
    params: RiskParams,
    risk_scale: float = 1.0,
) -> float:
    """Miktar (adet) dondurur; sinirlara sigmiyorsa 0.

    Boyut = (ozsermaye x risk x risk_scale) / stop mesafesi -> stop yenirse
    kayip her zaman ozsermayenin sabit yuzdesi olur. risk_scale, kol carpani
    (meanrev 0.5) ve volatilite hedeflemesi carpanini tasir.
    """
    if equity <= 0 or stop_distance <= 0 or price <= 0:
        return 0.0
    qty = (equity * params.risk_per_trade * risk_scale) / stop_distance
    full_qty = qty

    max_notional = equity * params.max_position_notional_pct
    qty = min(qty, max_notional / price)

    headroom = equity * params.max_leverage - open_notional
    if headroom <= 0:
        return 0.0
    qty = min(qty, headroom / price)

    if qty < full_qty * params.min_size_fraction:
        return 0.0
    return qty


class MonthlyKillSwitch:
    """Ay basindaki ozsermayeye gore aylik zarar limiti. Limit asilirsa ay
    sonuna kadar yeni giris yapilmaz (acik pozisyonlar kapatilir).

    Para yatirma/cekme zarar-kar DEGILDIR: ay ici net transfer (net_transfers)
    tabana eklenir, boylece cekilen para zarar, yatirilan para kar sayilmaz.
    Yanlis tetikleme (ornegin para cekiminin zarar sanilmasi) sonradan gelen
    transfer bilgisiyle geri alinir; transfer yokken geri alma olmaz."""

    def __init__(self, limit: float):
        self.limit = limit
        self.month = None
        self.month_start_equity = None
        self.net_transfers = 0.0      # ay ici net yatirilan(+)/cekilen(-)
        self.transfers_at_trip = 0.0  # tetiklenme anindaki transfer bilgisi
        self.tripped = False

    def effective_start(self) -> float:
        """Transfer duzeltmeli taban: ay basi + ay ici net transfer."""
        return (self.month_start_equity or 0.0) + self.net_transfers

    def update(self, ts, equity: float, net_transfers: float = 0.0) -> bool:
        """Yeni ozsermaye ile gunceller; bu cagriyla tetiklendiyse True doner."""
        key = (ts.year, ts.month)
        if key != self.month:
            self.month = key
            self.month_start_equity = equity
            self.net_transfers = 0.0
            self.transfers_at_trip = 0.0
            self.tripped = False
            return False
        self.net_transfers = net_transfers
        start = self.effective_start()
        if start <= 0:
            return False
        threshold = start * (1 - self.limit)
        if self.tripped and net_transfers != self.transfers_at_trip and equity > threshold:
            self.tripped = False
        if not self.tripped and equity <= threshold:
            self.tripped = True
            self.transfers_at_trip = net_transfers
            return True
        return False
