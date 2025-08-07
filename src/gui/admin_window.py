from __future__ import annotations

from PyQt5 import QtCore, QtWidgets, QtGui
from pathlib import Path
import platform

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
        self._setup_status_tab()
        self.web_button = QtWidgets.QPushButton("Webinterface")
        self.web_button.clicked.connect(self.show_web_qr)
        layout.addWidget(self.web_button)

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

    def _setup_status_tab(self):
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        self.tabs.addTab(widget, "Status")
        self.status_label = QtWidgets.QLabel()
        layout.addWidget(self.status_label)
        self.reload_status()

    def reload_status(self):
        conn = database.get_connection()
        users = conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        drinks = conn.execute('SELECT COUNT(*) FROM drinks').fetchone()[0]
        conn.close()
        system = platform.platform()
        self.status_label.setText(
            f"Nutzer: {users}\nGetränke: {drinks}\nSystem: {system}")

    def show_web_qr(self):
        data_dir = Path(__file__).resolve().parent.parent / 'data'
        path = data_dir / 'web_qr.png'
        if not path.exists():
            QtWidgets.QMessageBox.warning(self, "Fehler", "Kein QR-Code vorhanden.")
            return
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Webinterface QR-Code")
        pixmap = QtGui.QPixmap(str(path))
        label = QtWidgets.QLabel()
        label.setPixmap(pixmap)
        layout = QtWidgets.QVBoxLayout(dlg)
        layout.addWidget(label)
        dlg.exec_()
