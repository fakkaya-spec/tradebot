# tradebot — Binance Futures Hibrit Trend + Kırılım Botu

Binance USDT-M Futures üzerinde 4 saatlik mumlarla çalışan, orta risk profilli
otomatik işlem botu. Railway üzerinde 7/24 worker olarak koşar.

**Pariteler:** BTCUSDT, ETHUSDT, SOLUSDT
(LTC 5 yıllık backtest'te tüm pencerelerde zarar üretti ve evrenden çıkarıldı)
**Hedef:** yıllık ~%50 (garanti değildir; trend yıllarında aşılabilir, yatay
yıllarda drawdown normaldir)

## Strateji

Sermaye iki bağımsız "kol" arasında paylaşılır; ikisi de aynı risk çekirdeğini
kullanır:

| | TREND kolu | BREAKOUT kolu | MEANREV kolu (v2) |
|---|---|---|---|
| Giriş | EMA20/EMA50 yönü + ADX(14) > 25 + kapanış EMA20'nin doğru tarafında | 20 günlük Donchian kanal kırılımı (turtle) | Sadece yatay rejimde: Bollinger(20,2) dışı + RSI(2) aşırılığı |
| Stop | 2×ATR(14) | 2×ATR(14) sabit | 2×ATR(14) sabit, yarım risk |
| Yönetim | 1.5×ATR kârda başabaş, sonra 3×ATR iz süren stop | 10 günlük karşı kanal kapanışında çıkış | Orta banda dönüşte kâr al |
| Çıkış | EMA kesişimi tersine dönerse | Donchian exit kanalı | Bollinger orta bandı |

### v2 deneyi: izole holdout (2025+) sınavı sonuçları

Üç aday özellik görülmemiş dönemde tek tek test edildi (çıta: v1 +%49.3,
DD -%18.9, PF 1.37):

- **Volatilite hedeflemesi — KABUL (varsayılan açık):** +%64.0, PF 1.47,
  aynı işlem sayısı. Pozisyon riski, göreli ATR'nin kendi medyanına oranıyla
  0.5×–1.5× ölçeklenir: sakin piyasada büyük, çalkantılıda küçük pozisyon.
- **Rejim kapılaması — RED:** holdout +%7.6, PF 1.07. Geciken ADX
  sınıflaması iyi girişleri kesiyor. (`ENABLE_REGIME` ile açılabilir, önerilmez)
- **Meanrev kolu — RED:** eğitimde de holdout'ta da zararda; kripto 4h'de
  "yatay" dönemler bile patlamalı. (`ENABLE_MEANREV` ile açılabilir, önerilmez)

MEANREV satırı ve rejim davranışı yukarıdaki tabloda yalnızca ilgili
bayraklar açıkken geçerlidir.

Ek filtreler:
- **Makro yön filtresi:** fiyat EMA200(4h) üstündeyken sadece long, altındayken
  sadece short alınır — büyük resme karşı işlem yapılmaz
- **Cooldown:** stop yiyen pozisyon 24 saat (6 bar) boyunca aynı yöne yeniden
  giremez — testere piyasada öğütülmeyi engeller
- Pozisyon yönündeki funding oranı > %0.1/8s ise yeni giriş yapılmaz

### Risk çekirdeği (strateji ne derse desin aşılamaz)

- İşlem başına risk: özsermayenin **%1**'i (stop mesafesine göre boyutlama)
- Toplam kaldıraç tavanı: **3x** — tek pozisyon nominali en fazla 1× özsermaye
- **Aynı yön tavanı:** aynı anda aynı yönde en fazla 3 pozisyon — pariteler
  korele olduğu için hepsinin birlikte stop yemesini sınırlar
- **Aylık kill-switch:** ay içi zarar %8'e ulaşırsa tüm pozisyonlar kapatılır,
  ay sonuna kadar işlem yapılmaz
- Stop emirleri borsa tarafında `STOP_MARKET` olarak durur — bot çökse bile
  pozisyon korumasız kalmaz

## Güvenlik

1. **API anahtarını sadece "Enable Futures" yetkisiyle oluşturun.**
   "Enable Withdrawals" işaretliyse bot açılışta bunu tespit eder ve
   **çalışmayı reddeder** (`bot/exchange.py: assert_key_safety`).
2. Anahtarlar yalnızca ortam değişkenlerinde yaşar (Railway → Variables).
   Repoya asla anahtar koymayın; `.env` dosyası `.gitignore`'dadır.
3. Mümkünse anahtara IP kısıtlaması ekleyin (Railway static outbound IP).
4. Önce `TESTNET=true` + `DRY_RUN=true` ile başlayın; sırasıyla
   DRY_RUN → testnet gerçek emir → küçük gerçek sermaye şeklinde ilerleyin.
5. Binance hesabınızda 2FA açık olsun.

## Kurulum

```bash
pip install -r requirements.txt
cp .env.example .env   # doldurun (sadece lokal; Railway'de Variables kullanın)
```

## Backtest

```bash
python -m backtest.selftest              # motor doğrulaması (sentetik veri)
python -m backtest.run --days 365        # son 1 yıl (v2)
python -m backtest.run --days 365 --legacy   # v1 davranışı (v2 özellikleri kapalı)
python -m backtest.run --days 1825 --holdout 2025-01-01 --compare
    # v1 ve v2'yi eğitim (<2025) ve görülmemiş (≥2025) dönemde yan yana karşılaştırır
```

Backtest, canlı botla **birebir aynı** strateji ve risk modüllerini kullanır.
Komisyon (%0.05 taker), slippage (%0.03) ve gerçek funding oranları dahildir.
Veri `backtest/cache/` altına indirilir ve tekrar kullanılır.

> Not: Claude Code cloud ortamında çalıştırıyorsanız ortamın ağ politikasında
> `fapi.binance.com` adresine izin verilmiş olmalıdır.

## Railway'e kurulum

1. Bu repoyu Railway'de **New Project → Deploy from GitHub repo** ile bağlayın.
2. Railway `railway.json` + `Procfile`'ı algılar; başlangıç komutu
   `python -m bot.main` (worker, HTTP portu yok).
3. **Variables** bölümüne `.env.example`'daki değişkenleri girin
   (`TESTNET=true`, `DRY_RUN=true` ile başlayın).
4. Deploy loglarında `Bot basladi [DRY_RUN]` mesajını ve 4 saatlik bar
   kapanışlarında sinyal loglarını doğrulayın.
5. Telegram bildirimi için `TELEGRAM_BOT_TOKEN` ve `TELEGRAM_CHAT_ID` girin
   (BotFather'dan bot oluşturun; chat id için @userinfobot).
6. Kademeli geçiş: `DRY_RUN=false` (testnet) → gözlem → `TESTNET=false` ve
   küçük sermaye.

## Mimari

```
bot/
  config.py      # env tabanlı konfigürasyon
  indicators.py  # EMA, ATR, ADX (Wilder), Donchian — backtest ile ortak
  strategy.py    # hibrit sinyal mantığı — backtest ile ortak
  risk.py        # boyutlama, kaldıraç tavanı, aylık kill-switch — ortak
  exchange.py    # ccxt Binance USDT-M + anahtar güvenlik kontrolü
  notifier.py    # Telegram
  main.py        # canlı döngü (4h bar kapanışında)
backtest/
  data.py        # Binance public API'den mum + funding indirme (cache'li)
  engine.py      # portföy backtest motoru (komisyon/slippage/funding dahil)
  run.py         # rapor: getiri, drawdown, aylık kırılım, kol/sembol bazında
  selftest.py    # sentetik veriyle motor doğrulaması
```

---

# betbot — Spor Bahis Öneri Botu (değer bahsi)

Kripto botundan bağımsız ikinci modül. Futbol (13 lig, Süper Lig dahil),
tenis (ATP/WTA) ve NBA maçlarını her sabah tarar; kendi modelinin olasılığı
piyasa oranının ima ettiğinden belirgin yüksekse ("edge") günün en iyi
**en fazla 5** seçimini Kelly stake önerisiyle Telegram'a gönderir.
Edge yoksa "bugün oynama" der.

**Bot bahis OYNAMAZ, sadece öneri üretir.** Para ve hesap her zaman sizde
kalır; bu hem güvenlik hem de bahis sitesi kuralları açısından bilinçli bir
tasarım kararıdır.

## Modeller

| Spor | Model | Piyasa |
|---|---|---|
| Futbol | Online atak/defans Poisson (Dixon-Coles düzeltmeli) | 1X2, Ü/A 2.5 |
| Tenis | Yüzey harmanlı Elo (538 tarzı, K maç sayısıyla azalır) | Maç kazananı |
| NBA | Elo + ev avantajı + galibiyet farkı çarpanı | Moneyline |

Model olasılığı, kitapçılar-arası konsensüsle harmanlanır (`MARKET_BLEND`,
varsayılan 0.5) — yalnızca modelin piyasaya GÜÇLÜ itirazlarında bahis çıkar.

## Risk profilleri

| | orta (varsayılan) | agresif |
|---|---|---|
| Kelly çarpanı | 0.25 | 0.70 |
| Tek bahis tavanı | %2 | %7 |
| Min. edge | %5 | %4 |
| Günlük toplam risk | %10 | %30 |
| Aylık kill-switch | -%15 | -%30 |

Gerçekçi beklenti (orta profil): iyi yılda çift haneli büyüme, kötü ayda
kill-switch. Yıllık %500 hedefi ancak "agresif" profille ve **yüksek iflas
olasılığıyla** denenebilir — backtest raporundaki drawdown satırını okumadan
karar vermeyin.

## Backtest

```bash
python -m betbacktest.selftest                    # motor doğrulaması (ağ gerektirmez)
python -m betbacktest.run --start 2023-07-01      # son 3 sezon, iki profil yan yana
python -m betbacktest.run --start 2023-07-01 --best-price   # en iyi piyasa oranıyla (iyimser)
```

Veri kaynakları (ücretsiz, gerçek tarihsel oranlar): football-data.co.uk,
tennis-data.co.uk, sportsbookreviewsonline.com. İndirilenler
`betbacktest/cache/` altında saklanır. Backtest **walk-forward** çalışır:
model her maç için yalnızca o tarihten önceki maçları bilir; bahisler
Bet365 oranıyla (temkinli) fiyatlanır, konsensüs marj çıkarılarak hesaplanır.

> Claude Code cloud ortamında ağ politikasına şu alan adları eklenmelidir:
> `www.football-data.co.uk`, `www.tennis-data.co.uk`,
> `www.sportsbookreviewsonline.com` (backtest) ve `api.the-odds-api.com`,
> `api.telegram.org` (canlı mod).

**Dürüstlük notu:** backtest sonuçları kapanış oranlarıyla hesaplanır;
gerçekte o oranı her zaman bulamazsınız ve kazanan hesaplara siteler limit
koyar. Gerçek performansı backtest'in ~%20-30 altında bekleyin.

## Canlı çalıştırma (Railway)

1. [the-odds-api.com](https://the-odds-api.com) üzerinden ücretsiz API anahtarı alın.
2. Railway'de aynı repodan **ikinci bir servis** oluşturun; start komutu:
   `python -m betbot.main --loop`
3. Variables: `ODDS_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
   `LIVE_PROFILE=orta`, `BANKROLL`, `DRY_RUN=true` (ilk hafta böyle izleyin).
4. Öneriler tatmin ediyorsa `DRY_RUN=false` yapın; kuponlar Telegram'a düşer.

## Sorumluluk reddi

Bu yazılım eğitim amaçlıdır. Kaldıraçlı kripto türevleri yüksek risk içerir;
geçmiş performans (backtest dahil) gelecek getirinin garantisi değildir.
Kaybetmeyi göze alamayacağınız parayla işlem yapmayın.
