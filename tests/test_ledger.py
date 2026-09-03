from __future__ import annotations

import io

import pytest

from src import database, ledger, migrations


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "l.db")
    monkeypatch.setenv("GK_BACKUP_DIR", str(tmp_path / "bk"))
    migrations.upgrade()


def test_system_accounts_seeded(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    codes = {a.code for a in ledger.list_accounts()}
    # Vereinfachtes Kontenmodell: nur diese 4 sind sichtbar.
    assert codes == {"terminal_cash", "main_cash", "income_other", "expenses_other"}


def test_post_and_balance(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    ledger.on_cash_sale(150, ref="test")
    ledger.on_cash_sale(200, ref="test")
    # Nur eine Zeile pro Barverkauf: → Kühlschrank-Kasse.
    assert ledger.balance_of(ledger.get_account("terminal_cash").id) == 350


def test_transfer_terminal_to_main(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    ledger.on_cash_sale(1000, ref="x")
    ledger.transfer_terminal_to_main(600, actor="tester")
    assert ledger.balance_of(ledger.get_account("terminal_cash").id) == 400
    assert ledger.balance_of(ledger.get_account("main_cash").id) == 600


def test_expense_reduces_main_cash(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    # Voraussetzung: 500 in Hauptkasse.
    ledger.post("transfer", 500, from_account="terminal_cash", to_account="main_cash",
                actor="setup", note="Ausgangskapital")
    # Einkauf 300 aus Hauptkasse.
    ledger.post("expense", 300, from_account="main_cash", to_account="expenses_shopping",
                actor="tester", note="Cola-Palette")
    assert ledger.balance_of(ledger.get_account("main_cash").id) == 500 - 300


def test_reverse_creates_mirror_entry(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    eid = ledger.post("income", 200, from_account=None, to_account="main_cash",
                      note="Spende", actor="tester")
    ledger.reverse(eid, actor="tester")
    assert ledger.balance_of(ledger.get_account("main_cash").id) == 0


def test_delete_account_denied_for_system(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    tid = ledger.get_account("terminal_cash").id
    assert not ledger.delete_account(tid)


def test_delete_custom_account_without_entries(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    a = ledger.create_account("kaffee", "Kaffeekasse", "cash")
    assert ledger.delete_account(a.id)


def test_delete_custom_account_with_entries_blocked(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    a = ledger.create_account("kaffee2", "Kaffeekasse 2", "cash")
    ledger.post("income", 100, from_account=None, to_account=a.code)
    assert not ledger.delete_account(a.id)
