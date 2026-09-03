from __future__ import annotations

from datetime import date, datetime, timedelta

from src import database, ledger, migrations, recurring


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "r.db")
    monkeypatch.setenv("GK_BACKUP_DIR", str(tmp_path / "bk"))
    migrations.upgrade()


def test_create_and_post(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    main = ledger.get_account("main_cash").id
    exp = ledger.get_account("expenses_other").id
    rid = recurring.create(
        "Wartung", 2500,
        from_account_id=main, to_account_id=exp,
        interval="monthly", next_due=date.today().isoformat(),
    )
    entry = recurring.post_once(rid, actor="test")
    assert entry
    assert ledger.balance_of(main) == -2500
    assert ledger.balance_of(exp) == 2500


def test_run_due_only_when_ready(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    main = ledger.get_account("main_cash").id
    exp = ledger.get_account("expenses_other").id
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    rid = recurring.create(
        "Fällig morgen", 100, from_account_id=main, to_account_id=exp,
        interval="daily", next_due=tomorrow,
    )
    assert recurring.run_due(datetime.now()) == []
    recurring.update(rid, next_due=date.today().isoformat())
    assert rid in recurring.run_due(datetime.now())


def test_kiosk_reverse_returns_balance(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    from src import kiosk, models
    with database.get_connection() as c:
        d = c.execute("SELECT id, price FROM drinks LIMIT 1").fetchone()
    before = models.get_user_by_uid("TESTCARD123").balance
    res = kiosk.book("TESTCARD123", [kiosk.LineItem(d["id"], 1)])
    assert res.ok and res.receipt_ref
    kiosk.reverse_booking(res.receipt_ref, actor="test")
    after = models.get_user_by_uid("TESTCARD123").balance
    assert after == before
