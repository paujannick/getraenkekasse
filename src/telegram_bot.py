import threading
import time
from pathlib import Path
from typing import Optional
import io
import csv

import requests

from . import models

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / 'logs'


class TelegramNotifier:
    """Simple Telegram bot for status reports."""

    def __init__(self) -> None:
        self.token: str = ''
        self.chat_id: str = ''
        self.offset: int = 0
        self.last_month: str = ''
        self.thread: Optional[threading.Thread] = None
        self.running = False
        self.reload_settings()

    def reload_settings(self) -> None:
        """Reload credentials from database."""
        self.token = models.get_telegram_token()
        self.chat_id = models.get_telegram_chat()

    # --- Telegram API helpers -------------------------------------------------
    def _api(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.token}/{method}"

    def _enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    # --- Sending --------------------------------------------------------------
    def send_message(self, text: str) -> None:
        if not self._enabled():
            return
        try:
            requests.post(self._api('sendMessage'), json={'chat_id': self.chat_id, 'text': text})
        except Exception:
            pass

    def send_logfile(self) -> None:
        if not self._enabled():
            return
        try:
            logs = sorted(LOG_DIR.glob('log_*.txt'))
            if not logs:
                return
            with open(logs[-1], 'rb') as f:
                requests.post(
                    self._api('sendDocument'),
                    data={'chat_id': self.chat_id},
                    files={'document': f},
                )
        except Exception:
            pass

    def _send_csv(self, filename: str, headers: list[str], rows: list[tuple]) -> None:
        if not self._enabled():
            return
        try:
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(headers)
            for row in rows:
                writer.writerow(row)
            data = buf.getvalue().encode('utf-8')
            requests.post(
                self._api('sendDocument'),
                data={'chat_id': self.chat_id},
                files={'document': (filename, data)},
            )
        except Exception:
            pass

    def send_datafiles(self) -> None:
        if not self._enabled():
            return
        try:
            sales = models.get_transaction_log()
            self._send_csv(
                'sales.csv',
                ['timestamp', 'drink', 'quantity'],
                [(r['timestamp'], r['drink_name'], r['quantity']) for r in sales],
            )
            restocks = models.get_restock_log()
            self._send_csv(
                'restocks.csv',
                ['timestamp', 'drink', 'quantity'],
                [(r['timestamp'], r['drink_name'], r['quantity']) for r in restocks],
            )
            drinks = models.get_drinks()
            self._send_csv(
                'stock.csv',
                ['drink', 'stock'],
                [(d.name, d.stock) for d in drinks],
            )
        except Exception:
            pass

    def build_status(self) -> str:
        drinks = models.get_drinks_below_min()
        stats, _ = models.get_monthly_stats(1)
        lines: list[str] = []
        if drinks:
            lines.append('Nachzufüllende Getränke:')
            for d in drinks:
                lines.append(f"- {d.name}: {d.stock}/{d.min_stock}")
        else:
            lines.append('Alle Getränke ausreichend vorhanden.')
        if stats:
            s = stats[-1]
            lines.append('')
            lines.append('Monatsübersicht:')
            lines.append(f"Aufladungen: {s['topup']/100:.2f} €")
            lines.append(
                f"Verkäufe Karte: {s['card_value']/100:.2f} € ({s['card_count']})"
            )
            lines.append(
                f"Barverkäufe: {s['cash_value']/100:.2f} € ({s['cash_count']})"
            )
        return '\n'.join(lines)

    def send_status(self) -> None:
        text = self.build_status()
        self.send_message(text)
        self.send_logfile()
        self.send_datafiles()

    # --- Polling loop ---------------------------------------------------------
    def _poll(self) -> None:
        while self.running:
            try:
                if self._enabled():
                    resp = requests.get(
                        self._api('getUpdates'),
                        params={'timeout': 60, 'offset': self.offset + 1},
                        timeout=70,
                    )
                    data = resp.json()
                    for upd in data.get('result', []):
                        self.offset = max(self.offset, upd['update_id'])
                        msg = upd.get('message', {}).get('text', '')
                        if msg.strip() == '/status':
                            self.send_status()
                # --- Monatsreport: nur am 1. des Monats um 13:00 ---
                now = time.localtime()
                month_tag = f"{now.tm_year:04d}-{now.tm_mon:02d}"
                # Sende genau einmal pro Monat am 1. um 13:00 (Minute 0)
                if now.tm_mday == 1 and now.tm_hour == 13 and now.tm_min == 0:
                    if month_tag != self.last_month:
                        self.last_month = month_tag
                        self.send_status()
            except Exception:
                pass
            time.sleep(5)

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.running = True
        self.thread = threading.Thread(target=self._poll, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.running = False


notifier = TelegramNotifier()
