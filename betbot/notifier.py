"""Telegram bildirimi — kripto bottaki notifier ile aynı desen."""
import requests

from . import config


def send(text: str) -> None:
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        print("(telegram yapılandırılmamış, mesaj konsola yazıldı)\n" + text)
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": config.TELEGRAM_CHAT_ID, "text": text,
                  "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=15,
        ).raise_for_status()
    except Exception as e:
        print(f"telegram gönderilemedi: {e}\n{text}")
