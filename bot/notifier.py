"""Telegram bildirimleri. Token yoksa sessizce loglamaya duser."""
import logging

import requests

log = logging.getLogger("notifier")


class Notifier:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id

    def send(self, text: str) -> None:
        log.info("BILDIRIM: %s", text)
        if not self.token or not self.chat_id:
            return
        try:
            requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text},
                timeout=10,
            )
        except Exception as exc:  # bildirim hatasi botu durdurmamali
            log.warning("Telegram gonderilemedi: %s", exc)
