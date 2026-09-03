from __future__ import annotations

from datetime import datetime

from src import database, discounts


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "d.db")
    database.init_db()
    discounts.ensure_schema()


def test_effective_price_without_discount(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    assert discounts.effective_price(200, drink_id=1) == 200


def test_percent_discount_applied_within_window(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    discounts.add_discount("HH", 25, start_time="17:00", end_time="19:00")
    active = datetime(2026, 1, 5, 18, 0)  # Montag 18:00
    inactive = datetime(2026, 1, 5, 20, 0)
    assert discounts.effective_price(200, drink_id=1, when=active) == 150
    assert discounts.effective_price(200, drink_id=1, when=inactive) == 200


def test_weekday_filter(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    discounts.add_discount("Wochenende", 50, weekdays=[6, 7])
    saturday = datetime(2026, 1, 3, 12, 0)  # Sa
    tuesday = datetime(2026, 1, 6, 12, 0)   # Di
    assert discounts.effective_price(200, drink_id=1, when=saturday) == 100
    assert discounts.effective_price(200, drink_id=1, when=tuesday) == 200


def test_drink_specific_discount(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    discounts.add_discount("Nur Cola", 10, drink_ids=[42])
    now = datetime(2026, 1, 5, 12, 0)
    assert discounts.effective_price(100, drink_id=42, when=now) == 90
    assert discounts.effective_price(100, drink_id=1, when=now) == 100


def test_stronger_of_two_discounts_wins(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    discounts.add_discount("klein", 10)
    discounts.add_discount("groß", 30)
    now = datetime(2026, 1, 5, 12, 0)
    assert discounts.effective_price(300, drink_id=1, when=now) == 210
