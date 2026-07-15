import sys
import types

qtwidgets = types.SimpleNamespace(QMessageBox=object, QApplication=object)
qtcore = types.SimpleNamespace(Qt=types.SimpleNamespace())
pyqt5 = types.SimpleNamespace(QtWidgets=qtwidgets, QtCore=qtcore)
sys.modules.setdefault("PyQt5", pyqt5)
sys.modules.setdefault("PyQt5.QtWidgets", qtwidgets)
sys.modules.setdefault("PyQt5.QtCore", qtcore)

from src import database, models


def setup_db(tmp_path, monkeypatch):
    db_file = tmp_path / 'test.db'
    monkeypatch.setattr(database, 'DB_PATH', db_file)
    conn = database.get_connection()
    database.init_db(conn)
    return conn


def test_purchase_and_stock_update(tmp_path, monkeypatch):
    conn = setup_db(tmp_path, monkeypatch)
    user = conn.execute("SELECT id, balance FROM users WHERE name='Alice'").fetchone()
    drink = conn.execute("SELECT id, price, stock FROM drinks WHERE name='Wasser'").fetchone()
    assert models.update_balance(user['id'], -drink['price'])
    assert models.update_drink_stock(drink['id'], -1)
    models.add_transaction(user['id'], drink['id'], 1)
    new_balance = conn.execute('SELECT balance FROM users WHERE id=?', (user['id'],)).fetchone()['balance']
    new_stock = conn.execute('SELECT stock FROM drinks WHERE id=?', (drink['id'],)).fetchone()['stock']
    assert new_balance == user['balance'] - drink['price']
    assert new_stock == drink['stock'] - 1
    conn.close()


def test_topup_updates_balance_and_log(tmp_path, monkeypatch):
    conn = setup_db(tmp_path, monkeypatch)
    user = conn.execute("SELECT id, balance FROM users WHERE name='Bob'").fetchone()
    assert models.update_balance(user['id'], 500)
    models.add_topup(user['id'], 500)
    new_balance = conn.execute('SELECT balance FROM users WHERE id=?', (user['id'],)).fetchone()['balance']
    topup = conn.execute('SELECT amount FROM topups WHERE user_id=? ORDER BY id DESC LIMIT 1', (user['id'],)).fetchone()
    assert new_balance == user['balance'] + 500
    assert topup['amount'] == 500
    conn.close()


def test_storno_booking_restores_balance_and_stock(tmp_path, monkeypatch):
    conn = setup_db(tmp_path, monkeypatch)
    user = conn.execute("SELECT id, balance FROM users WHERE name='Alice'").fetchone()
    drink = conn.execute("SELECT id, price, stock FROM drinks WHERE name='Cola'").fetchone()
    models.update_balance(user['id'], -drink['price'])
    models.update_drink_stock(drink['id'], -1)
    models.update_balance(user['id'], drink['price'])
    models.update_drink_stock(drink['id'], +1)
    new_balance = conn.execute('SELECT balance FROM users WHERE id=?', (user['id'],)).fetchone()['balance']
    new_stock = conn.execute('SELECT stock FROM drinks WHERE id=?', (drink['id'],)).fetchone()['stock']
    assert new_balance == user['balance']
    assert new_stock == drink['stock']
    conn.close()


def test_assign_rfid_to_pending_standard_user(tmp_path, monkeypatch):
    conn = setup_db(tmp_path, monkeypatch)
    conn.execute(
        "INSERT INTO users (name, rfid_uid, balance, is_event, active) VALUES (?, NULL, ?, 0, 1)",
        ("Charlie", 0),
    )
    conn.commit()
    conn.close()

    pending = models.get_unassigned_users()
    user = next(u for u in pending if u.name == "Charlie")
    assert models.assign_rfid_to_user(user.id, "NEWCARD123")

    assigned = models.get_user_by_uid("NEWCARD123")
    assert assigned is not None
    assert assigned.name == "Charlie"
    assert all(u.id != user.id for u in models.get_unassigned_users())


def test_assign_rfid_rejects_already_assigned_uid(tmp_path, monkeypatch):
    conn = setup_db(tmp_path, monkeypatch)
    conn.execute(
        "INSERT INTO users (name, rfid_uid, balance, is_event, active) VALUES (?, NULL, ?, 0, 1)",
        ("Dana", 0),
    )
    pending_id = conn.execute("SELECT id FROM users WHERE name='Dana'").fetchone()["id"]
    conn.close()

    assert not models.assign_rfid_to_user(pending_id, "TESTCARD123")
    assert models.get_user_by_uid("TESTCARD123").name == "Alice"
