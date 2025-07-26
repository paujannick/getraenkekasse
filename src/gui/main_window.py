from __future__ import annotations

from pathlib import Path
from PyQt5 import QtCore, QtGui, QtWidgets

from .. import database
from .. import models
from .. import rfid
from .. import led


class QuantityDialog(QtWidgets.QDialog):

    """Dialog zum Wählen der Menge über +/--Buttons."""


    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Menge wählen")
        self.quantity = 1
        layout = QtWidgets.QVBoxLayout(self)

        self.label = QtWidgets.QLabel(str(self.quantity))
        self.label.setAlignment(QtCore.Qt.AlignCenter)
        font = self.label.font()
        font.setPointSize(20)
        self.label.setFont(font)
        layout.addWidget(self.label)

        btn_layout = QtWidgets.QHBoxLayout()
        self.minus_btn = QtWidgets.QPushButton("-")
        self.plus_btn = QtWidgets.QPushButton("+")
        for b in (self.minus_btn, self.plus_btn):
            b.setFixedSize(80, 80)
            f = b.font()
            f.setPointSize(24)
            b.setFont(f)
        btn_layout.addWidget(self.minus_btn)
        btn_layout.addWidget(self.plus_btn)
        layout.addLayout(btn_layout)

        self.minus_btn.clicked.connect(self.dec)
        self.plus_btn.clicked.connect(self.inc)

        btns = QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        self.btnBox = QtWidgets.QDialogButtonBox(btns)
        self.cash_btn = QtWidgets.QPushButton("Barzahlung")
        self.btnBox.addButton(self.cash_btn, QtWidgets.QDialogButtonBox.ActionRole)
        self.cash_btn.clicked.connect(self.cash)
        self.btnBox.accepted.connect(self.accept)
        self.btnBox.rejected.connect(self.reject)
        for btn in [self.cash_btn, self.btnBox.button(QtWidgets.QDialogButtonBox.Ok),
                    self.btnBox.button(QtWidgets.QDialogButtonBox.Cancel)]:
            if btn is not None:
                f = btn.font()
                f.setPointSize(16)
                btn.setFont(f)
                btn.setMinimumHeight(60)
        layout.addWidget(self.btnBox)
        self._cash = False

    def inc(self) -> None:
        if self.quantity < 10:
            self.quantity += 1
            self.label.setText(str(self.quantity))

    def dec(self) -> None:
        if self.quantity > 1:
            self.quantity -= 1
            self.label.setText(str(self.quantity))

    def accept(self) -> None:
        super().accept()

    def cash(self) -> None:
        self._cash = True
        self.accept()

    @property
    def is_cash(self) -> bool:
        return self._cash


class TopupDialog(QtWidgets.QDialog):
    """Dialog to choose a top-up amount."""

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Betrag wählen")
        layout = QtWidgets.QVBoxLayout(self)
        self.combo = QtWidgets.QComboBox()
        for val in (5, 10, 20, 50):
            self.combo.addItem(f"{val} €", val)
        layout.addWidget(self.combo)

        btns = QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        self.btnBox = QtWidgets.QDialogButtonBox(btns)
        self.btnBox.accepted.connect(self.accept)
        self.btnBox.rejected.connect(self.reject)
        for role in (QtWidgets.QDialogButtonBox.Ok, QtWidgets.QDialogButtonBox.Cancel):
            btn = self.btnBox.button(role)
            if btn is not None:
                f = btn.font()
                f.setPointSize(16)
                btn.setFont(f)
                btn.setMinimumHeight(60)
        layout.addWidget(self.btnBox)

    @property
    def amount(self) -> int:
        return int(self.combo.currentData())


class PinDialog(QtWidgets.QDialog):
    """Numeric keypad dialog for entering the admin PIN."""

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("PIN eingeben")
        layout = QtWidgets.QVBoxLayout(self)
        self.edit = QtWidgets.QLineEdit()
        self.edit.setEchoMode(QtWidgets.QLineEdit.Password)
        layout.addWidget(self.edit)

        grid = QtWidgets.QGridLayout()
        for i in range(9):
            btn = QtWidgets.QPushButton(str(i + 1))
            btn.setFixedSize(60, 60)
            btn.clicked.connect(lambda _, d=i + 1: self.edit.insert(str(d)))
            r, c = divmod(i, 3)
            grid.addWidget(btn, r, c)
        zero = QtWidgets.QPushButton("0")
        zero.setFixedSize(60, 60)
        zero.clicked.connect(lambda: self.edit.insert("0"))
        grid.addWidget(zero, 3, 1)
        layout.addLayout(grid)

        btns = QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        box = QtWidgets.QDialogButtonBox(btns)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    @property
    def pin(self) -> str:
        return self.edit.text()


class AdminPage(QtWidgets.QWidget):
    """Simple admin page showing low stock items and quit option."""

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)

        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Getränk", "Bestand", "Min"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        layout.addWidget(self.table)

        btn_layout = QtWidgets.QHBoxLayout()
        self.back_btn = QtWidgets.QPushButton("Zurück")
        self.quit_btn = QtWidgets.QPushButton("Beenden")
        btn_layout.addWidget(self.back_btn)
        btn_layout.addStretch(1)
        btn_layout.addWidget(self.quit_btn)
        layout.addLayout(btn_layout)

    def reload(self) -> None:
        drinks = models.get_drinks_below_min()
        self.table.setRowCount(len(drinks))
        for row, drink in enumerate(drinks):
            self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(drink.name))
            self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(drink.stock)))
            self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(str(drink.min_stock)))


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        database.init_db()
        database.clear_exit_flag()
        self.setWindowTitle("Getränkekasse")
        self.resize(800, 480)
        self.central = QtWidgets.QWidget()
        self.setCentralWidget(self.central)

        logo_path = Path(__file__).resolve().parent.parent / 'data' / 'background.png'
        if logo_path.exists():
            self.central.setStyleSheet(
                f"background-image: url('{logo_path}');"
                "background-repeat: no-repeat;"
                "background-position: center;"
            )

        self.stack = QtWidgets.QStackedLayout(self.central)

        self.refresh_mtime = database.REFRESH_FLAG.stat().st_mtime if database.REFRESH_FLAG.exists() else 0.0
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.check_refresh)
        self.timer.start(3000)

        self.start_page = QtWidgets.QWidget()
        self.start_layout = QtWidgets.QGridLayout(self.start_page)
        self.current_page = 1
        self.page_count = 1
        self._populate_start_page()


        self.stack.addWidget(self.start_page)

        self.info_label = QtWidgets.QLabel()
        self.info_label.setAlignment(QtCore.Qt.AlignCenter)
        font = self.info_label.font()
        font.setPointSize(24)
        self.info_label.setFont(font)
        self.stack.addWidget(self.info_label)

        self.admin_page = AdminPage(self)
        self.admin_page.back_btn.clicked.connect(self.show_start_page)
        self.admin_page.quit_btn.clicked.connect(self._quit)
        self.stack.addWidget(self.admin_page)

        self.show_start_page()

    def _populate_start_page(self) -> None:
        layout = self.start_layout
        conn = database.get_connection()

        self.page_count = models.get_max_page(conn)
        drinks = models.get_drinks(conn, limit=9, page=self.current_page)
        font = QtGui.QFont()
        font.setPointSize(16)

        rows = (len(drinks) + 2) // 3
        for row in range(rows):
            layout.setRowStretch(row, 0)

        for idx, drink in enumerate(drinks):
            button = QtWidgets.QPushButton()
            button.setText(f"{drink.name}\n{drink.price/100:.2f} €")
            button.setFont(font)
            if drink.image:
                button.setIcon(QtGui.QIcon(drink.image))

                button.setIconSize(QtCore.QSize(120, 120))
            button.setMinimumSize(220, 140)

            if drink.stock < 0:
                button.setStyleSheet('background-color:#f00; color:#888;')
            elif drink.stock < drink.min_stock:
                button.setStyleSheet('background-color:#ff0;')
            button.clicked.connect(lambda _, d=drink: self.on_drink_selected(d))
            r, c = divmod(idx, 3)
            layout.addWidget(button, r, c)
        self.prev_button = QtWidgets.QPushButton("◀")
        self.next_button = QtWidgets.QPushButton("▶")

        for btn in (self.prev_button, self.next_button):
            f = btn.font()
            f.setPointSize(20)
            btn.setFont(f)
            btn.setFixedSize(80, 40)
        self.prev_button.clicked.connect(self.prev_page)
        self.next_button.clicked.connect(self.next_page)


        spacer_row = rows
        bottom = rows + 1
        layout.setRowStretch(spacer_row, 1)
        layout.addWidget(self.prev_button, bottom, 0, alignment=QtCore.Qt.AlignBottom)
        layout.addWidget(self.next_button, bottom, 1, alignment=QtCore.Qt.AlignBottom)

        self.admin_button = QtWidgets.QPushButton("Admin")
        f = self.admin_button.font()
        f.setPointSize(12)
        self.admin_button.setFont(f)
        self.admin_button.setFixedSize(80, 40)
        self.admin_button.clicked.connect(self._open_admin)
        layout.addWidget(self.admin_button, bottom, 2,
                         alignment=QtCore.Qt.AlignBottom | QtCore.Qt.AlignRight)
        layout.setRowStretch(bottom, 0)

        self.prev_button.setEnabled(self.current_page > 1)
        self.next_button.setEnabled(self.current_page < self.page_count)

    def show_start_page(self):
        self.stack.setCurrentWidget(self.start_page)

    def on_drink_selected(self, drink: models.Drink) -> None:
        dialog = QuantityDialog(self)
        if dialog.exec_() != QtWidgets.QDialog.Accepted:
            return
        quantity = dialog.quantity
        if dialog.is_cash:
            models.update_drink_stock(drink.id, -quantity)
            cash_id = models.get_cash_user_id()
            models.add_transaction(cash_id, drink.id, quantity)
            led.indicate_success()
            total_price = drink.price * quantity
            self.info_label.setText(
                f"Bitte {total_price/100:.2f} \u20ac passend in die Getränkekasse legen"
            )
            self.stack.setCurrentWidget(self.info_label)
            QtCore.QTimer.singleShot(2000, self.show_start_page)
            return
        self.info_label.setText("Bitte Karte auflegen…")
        self.stack.setCurrentWidget(self.info_label)
        uid = rfid.read_uid(show_dialog=False)
        if not uid:
            QtWidgets.QMessageBox.warning(self, "Fehler", "Karte konnte nicht gelesen werden")
            self.show_start_page()
            return
        topup_uid = models.get_topup_uid()
        if topup_uid and uid == topup_uid:
            self._handle_topup()
            return
        user = models.get_user_by_uid(uid)
        if not user:
            led.indicate_error()
            QtWidgets.QMessageBox.warning(self, "Fehler", "Unbekannte Karte")
            self.show_start_page()
            return
        total_price = drink.price * quantity
        old_balance = user.balance
        if not models.update_balance(user.id, -total_price):
            QtWidgets.QMessageBox.information(self, "Guthaben", "Limit überschritten - bitte Guthaben aufladen")
            self.show_start_page()
            return
        models.update_drink_stock(drink.id, -quantity)
        models.add_transaction(user.id, drink.id, quantity)
        new_user = models.get_user_by_uid(uid)
        led.indicate_success()
        msg = (
            f"Danke {new_user.name}!\nAltes Guthaben: {old_balance/100:.2f} €\n"
            f"Neues Guthaben: {new_user.balance/100:.2f} €"
        )
        if new_user.balance < 0:
            msg += "\nBitte Guthaben aufladen!"
        self.info_label.setText(msg)
        self.stack.setCurrentWidget(self.info_label)
        QtWidgets.QApplication.processEvents()
        QtCore.QTimer.singleShot(4000, self.show_start_page)


    def check_refresh(self) -> None:
        if database.exit_flag_set():
            database.clear_exit_flag()
            QtWidgets.QApplication.quit()
            return
        if self.stack.currentWidget() is self.start_page and database.refresh_needed(self.refresh_mtime):
            self.refresh_mtime = database.REFRESH_FLAG.stat().st_mtime
            self._rebuild_start_page()

    def _rebuild_start_page(self) -> None:
        layout = self.start_layout
        for i in reversed(range(layout.count())):
            item = layout.takeAt(i)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        for r in range(layout.rowCount()):
            layout.setRowStretch(r, 0)
        self._populate_start_page()

    def _handle_topup(self) -> None:
        dialog = TopupDialog(self)
        if dialog.exec_() != QtWidgets.QDialog.Accepted:
            self.show_start_page()
            return
        amount = dialog.amount
        self.info_label.setText("Bitte Zielkarte auflegen…")
        self.stack.setCurrentWidget(self.info_label)
        uid = rfid.read_uid(show_dialog=False)
        if not uid:
            QtWidgets.QMessageBox.warning(self, "Fehler", "Karte konnte nicht gelesen werden")
            self.show_start_page()
            return
        user = models.get_user_by_uid(uid)
        if not user:
            led.indicate_error()
            QtWidgets.QMessageBox.warning(self, "Fehler", "Unbekannte Karte")
            self.show_start_page()
            return
        old_balance = user.balance
        if not models.update_balance(user.id, amount * 100):
            led.indicate_error()
            QtWidgets.QMessageBox.warning(self, "Fehler", "Aufladen fehlgeschlagen")
            self.show_start_page()
            return
        new_user = models.get_user_by_uid(uid)
        led.indicate_success()
        msg = (
            f"Aufgeladen!\nAltes Guthaben: {old_balance/100:.2f} €\n"
            f"Neues Guthaben: {new_user.balance/100:.2f} €"
        )
        self.info_label.setText(msg)
        QtCore.QTimer.singleShot(3000, self.show_start_page)
        self.stack.setCurrentWidget(self.info_label)

    def _open_admin(self) -> None:
        dialog = PinDialog(self)
        if dialog.exec_() != QtWidgets.QDialog.Accepted:
            return
        if dialog.pin == models.get_admin_pin():
            self.admin_page.reload()
            self.stack.setCurrentWidget(self.admin_page)
        else:
            QtWidgets.QMessageBox.warning(self, "Fehler", "Falscher PIN")

    def _quit(self) -> None:
        database.set_exit_flag()
        QtWidgets.QApplication.quit()

    def next_page(self) -> None:
        if self.current_page < self.page_count:
            self.current_page += 1
            self._rebuild_start_page()

    def prev_page(self) -> None:
        if self.current_page > 1:
            self.current_page -= 1
            self._rebuild_start_page()

