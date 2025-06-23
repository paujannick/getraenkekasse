from __future__ import annotations

from PyQt5 import QtCore, QtWidgets

from .. import database
from .. import models


class AdminWindow(QtWidgets.QWidget):
    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Admin")
        self.resize(800, 480)
        layout = QtWidgets.QVBoxLayout(self)
        self.tabs = QtWidgets.QTabWidget()
        layout.addWidget(self.tabs)
        self._setup_users_tab()
        self._setup_drinks_tab()
        self._setup_log_tab()

    def _setup_users_tab(self):
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        self.tabs.addTab(widget, "Benutzer")
        self.user_list = QtWidgets.QListWidget()
        layout.addWidget(self.user_list)
        self.reload_users()

    def reload_users(self):
        self.user_list.clear()
        conn = database.get_connection()
        cur = conn.execute('SELECT * FROM users ORDER BY name')
        for row in cur.fetchall():
            self.user_list.addItem(f"{row['name']} - {row['balance']/100:.2f} €")
        conn.close()

    def _setup_drinks_tab(self):
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        self.tabs.addTab(widget, "Getränke")
        self.drink_list = QtWidgets.QListWidget()
        layout.addWidget(self.drink_list)
        self.reload_drinks()

    def reload_drinks(self):
        self.drink_list.clear()
        conn = database.get_connection()
        cur = conn.execute('SELECT * FROM drinks ORDER BY name')
        for row in cur.fetchall():
            self.drink_list.addItem(f"{row['name']} - {row['price']/100:.2f} €")
        conn.close()

    def _setup_log_tab(self):
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        self.tabs.addTab(widget, "Log")
        self.log_list = QtWidgets.QListWidget()
        layout.addWidget(self.log_list)
        self.reload_log()

    def reload_log(self):
        self.log_list.clear()
        conn = database.get_connection()
        cur = conn.execute('SELECT t.timestamp, u.name as user_name, '
                           'd.name as drink_name, t.quantity '
                           'FROM transactions t '
                           'JOIN users u ON u.id = t.user_id '
                           'JOIN drinks d ON d.id = t.drink_id '
                           'ORDER BY t.timestamp DESC LIMIT 100')
        for row in cur.fetchall():
            self.log_list.addItem(
                f"{row['timestamp']} - {row['user_name']} kaufte {row['quantity']} x {row['drink_name']}" )
        conn.close()
