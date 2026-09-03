from __future__ import annotations

from src import database, discounts, kiosk, migrations, models


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "k.db")
    monkeypatch.setenv("GK_BACKUP_DIR", str(tmp_path / "bk"))
    migrations.upgrade()


def _ids(name):
    with database.get_connection() as conn:
        row = conn.execute("SELECT id FROM drinks WHERE name=?", (name,)).fetchone()
    return row["id"]


def test_simple_purchase(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    wasser = _ids("Wasser")
    res = kiosk.book("TESTCARD123", [kiosk.LineItem(wasser, 1)])
    assert res.ok, res.error
    assert res.total_cents == 150
    # Guthaben abgezogen
    user = models.get_user_by_uid("TESTCARD123")
    assert user.balance == 1000 - 150


def test_multi_item_purchase(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    wasser = _ids("Wasser")
    cola = _ids("Cola")
    res = kiosk.book("TESTCARD123", [kiosk.LineItem(wasser, 2), kiosk.LineItem(cola, 1)])
    assert res.ok
    assert res.total_cents == 150 * 2 + 200


def test_insufficient_funds_rolls_back(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    cola = _ids("Cola")
    # 3× Cola (600 ct) > Bob 500 ct
    before = models.get_user_by_uid("TESTCARD456").balance
    res = kiosk.book("TESTCARD456", [kiosk.LineItem(cola, 3)])
    assert not res.ok
    assert "Guthaben" in res.error
    after = models.get_user_by_uid("TESTCARD456").balance
    assert before == after  # kein Abzug


def test_unknown_card(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    wasser = _ids("Wasser")
    res = kiosk.book("NOPE", [kiosk.LineItem(wasser, 1)])
    assert not res.ok
    assert "unbekannt" in res.error.lower()


def test_stock_shortage(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    wasser = _ids("Wasser")
    with database.get_connection() as conn:
        conn.execute("UPDATE drinks SET stock=1 WHERE id=?", (wasser,))
        conn.commit()
    res = kiosk.book("TESTCARD123", [kiosk.LineItem(wasser, 3)])
    assert not res.ok


def test_discount_applied(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    wasser = _ids("Wasser")
    discounts.add_discount("Test50", 50)
    res = kiosk.book("TESTCARD123", [kiosk.LineItem(wasser, 1)])
    assert res.ok
    assert res.total_cents == 75  # 50% Rabatt auf 150 ct
