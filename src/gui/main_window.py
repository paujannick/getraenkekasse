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


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        database.init_db()
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
        self._populate_start_page()


        self.stack.addWidget(self.start_page)

        self.info_label = QtWidgets.QLabel()
        self.info_label.setAlignment(QtCore.Qt.AlignCenter)
        font = self.info_label.font()
        font.setPointSize(24)
        self.info_label.setFont(font)
        self.stack.addWidget(self.info_label)

        self.show_start_page()

    def _populate_start_page(self) -> None:
        layout = self.start_layout
        conn = database.get_connection()

        drinks = models.get_drinks(conn, limit=9)
        font = QtGui.QFont()
        font.setPointSize(16)

        for idx, drink in enumerate(drinks):
            button = QtWidgets.QPushButton()
            button.setText(f"{drink.name}\n{drink.price/100:.2f} €")
            button.setFont(font)
            if drink.image:
                button.setIcon(QtGui.QIcon(drink.image))

                button.setIconSize(QtCore.QSize(120, 120))
            button.setMinimumSize(220, 140)

            if drink.stock < 0:
                button.setStyleSheet('background-color:#fdd;')
            button.clicked.connect(lambda _, d=drink: self.on_drink_selected(d))
            r, c = divmod(idx, 3)
            layout.addWidget(button, r, c)
        self.buy_button = QtWidgets.QPushButton("Kaufen")
        self.cancel_button = QtWidgets.QPushButton("Abbrechen")

        for btn in (self.buy_button, self.cancel_button):
            f = btn.font()
            f.setPointSize(20)
            btn.setFont(f)
            btn.setMinimumHeight(80)

        layout.addWidget(self.buy_button, layout.rowCount(), 0)
        layout.addWidget(self.cancel_button, layout.rowCount() - 1, 1)
        self.buy_button.hide()
        self.cancel_button.hide()

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
            self.info_label.setText("Barverkauf verbucht")
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
        for i in reversed(range(self.start_layout.count())):
            item = self.start_layout.takeAt(i)
            widget = item.widget()
            if widget:
                widget.deleteLater()
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

