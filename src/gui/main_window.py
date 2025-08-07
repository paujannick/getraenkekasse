from __future__ import annotations

from pathlib import Path
from PyQt5 import QtCore, QtGui, QtWidgets

from .. import database
from .. import models
from .. import rfid
from .. import led


class QuantityDialog(QtWidgets.QDialog):

    """Dialog zum Wählen der Menge über +/--Buttons."""


    def __init__(self, drink: models.Drink, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.drink = drink
        self.setWindowTitle("Menge wählen")
        self.setWindowState(QtCore.Qt.WindowFullScreen)
        self.quantity = 1
        layout = QtWidgets.QVBoxLayout(self)

        if drink.image:
            self.setStyleSheet(
                f"QDialog {{background-image: url({drink.image}); "
                "background-position: center; background-repeat: no-repeat;}}"
            )

        self.product_label = QtWidgets.QLabel(drink.name)
        f = self.product_label.font()
        f.setPointSize(24)
        self.product_label.setFont(f)
        self.product_label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(self.product_label)

        qty_layout = QtWidgets.QHBoxLayout()
        self.minus_btn = QtWidgets.QPushButton("-")
        self.plus_btn = QtWidgets.QPushButton("+")
        for b in (self.minus_btn, self.plus_btn):
            b.setFixedSize(100, 100)
            f = b.font()
            f.setPointSize(32)
            b.setFont(f)
        self.label = QtWidgets.QLabel(f"{self.quantity} Stück")
        self.label.setAlignment(QtCore.Qt.AlignCenter)
        font = self.label.font()
        font.setPointSize(40)
        font.setBold(True)
        self.label.setFont(font)
        self.label.setMinimumWidth(200)
        qty_layout.addWidget(self.minus_btn)
        qty_layout.addWidget(self.label)
        qty_layout.addWidget(self.plus_btn)
        layout.addLayout(qty_layout)

        self.minus_btn.clicked.connect(self.dec)
        self.plus_btn.clicked.connect(self.inc)

        grid = QtWidgets.QGridLayout()
        self.cash_btn = QtWidgets.QPushButton("Bar")
        self.cash_btn.clicked.connect(self.cash)
        self.chip_btn = QtWidgets.QPushButton("Chip")
        self.chip_btn.clicked.connect(self.accept)
        buttons = [self.cash_btn, self.chip_btn]

        self._event_user_id: int | None = None
        for user in models.get_event_payment_users():
            btn = QtWidgets.QPushButton(user.name)
            btn.clicked.connect(lambda _, uid=user.id: self._select_event_user(uid))
            buttons.append(btn)

        for i, btn in enumerate(buttons):
            r, c = divmod(i, 2)
            f = btn.font()
            f.setPointSize(18)
            btn.setFont(f)
            btn.setMinimumHeight(80)
            grid.addWidget(btn, r, c)

        layout.addLayout(grid)

        cancel_btn = QtWidgets.QPushButton("Abbrechen")
        f = cancel_btn.font()
        f.setPointSize(16)
        cancel_btn.setFont(f)
        cancel_btn.setMinimumHeight(60)
        cancel_btn.clicked.connect(self.reject)
        layout.addWidget(cancel_btn)

        self._cash = False

    def inc(self) -> None:
        if self.quantity < 10:
            self.quantity += 1
            self.label.setText(f"{self.quantity} Stück")

    def dec(self) -> None:
        if self.quantity > 1:
            self.quantity -= 1
            self.label.setText(f"{self.quantity} Stück")

    def accept(self) -> None:
        super().accept()

    def cash(self) -> None:
        self._cash = True
        self.accept()

    def _select_event_user(self, uid: int) -> None:
        """Store the selected event user's id and close the dialog."""
        self._event_user_id = uid
        self.accept()

    @property
    def is_cash(self) -> bool:
        return self._cash

    @property
    def event_user_id(self) -> int | None:
        return self._event_user_id


class StockPage(QtWidgets.QWidget):
    """Page showing drinks below minimum stock."""

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)

        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Getränk", "Bestand", "Min"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        layout.addWidget(self.table)

        self.back_btn = QtWidgets.QPushButton("Zurück")
        f = self.back_btn.font()
        f.setPointSize(16)
        self.back_btn.setFont(f)
        self.back_btn.setMinimumHeight(60)
        layout.addWidget(self.back_btn)

    def reload(self) -> None:
        drinks = models.get_drinks_below_min()
        self.table.setRowCount(len(drinks))
        for row, drink in enumerate(drinks):
            self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(drink.name))
            self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(drink.stock)))
            self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(str(drink.min_stock)))


class AdminMenu(QtWidgets.QWidget):
    """Main admin menu with navigation buttons."""

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)

        self.stock_btn = QtWidgets.QPushButton("Bestandswarnungen")
        self.topup_btn = QtWidgets.QPushButton("Konten aufladen")
        self.quit_btn = QtWidgets.QPushButton("Beenden")
        self.back_btn = QtWidgets.QPushButton("Zurück")

        for btn in (self.stock_btn, self.topup_btn, self.quit_btn, self.back_btn):
            f = btn.font()
            f.setPointSize(20)
            btn.setFont(f)
            btn.setMinimumHeight(80)
            layout.addWidget(btn)

        layout.addStretch(1)


class TopupPage(QtWidgets.QWidget):
    """Page to top up user accounts."""

    def __init__(self, parent: "MainWindow"):
        super().__init__(parent)
        self._main = parent
        layout = QtWidgets.QVBoxLayout(self)

        self.prompt = QtWidgets.QLabel("Betrag wählen")
        f = self.prompt.font()
        f.setPointSize(24)
        self.prompt.setFont(f)
        self.prompt.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(self.prompt)

        grid = QtWidgets.QGridLayout()
        for i, amount in enumerate((5, 10, 20, 50)):
            btn = QtWidgets.QPushButton(f"{amount} €")
            f = btn.font()
            f.setPointSize(24)
            btn.setFont(f)
            btn.setMinimumHeight(80)
            btn.clicked.connect(lambda _, a=amount: self.start_topup(a))
            r, c = divmod(i, 2)
            grid.addWidget(btn, r, c)
        layout.addLayout(grid)

        self.back_btn = QtWidgets.QPushButton("Zurück")
        f = self.back_btn.font()
        f.setPointSize(16)
        self.back_btn.setFont(f)
        self.back_btn.setMinimumHeight(60)
        layout.addWidget(self.back_btn)

        self.info = QtWidgets.QLabel("", alignment=QtCore.Qt.AlignCenter)
        f = self.info.font()
        f.setPointSize(20)
        self.info.setFont(f)
        layout.addWidget(self.info)

    def start_topup(self, amount: int) -> None:
        self.info.setText("Bitte Zielkarte auflegen…")
        QtWidgets.QApplication.processEvents()
        uid = rfid.read_uid(show_dialog=False)
        if not uid:
            QtWidgets.QMessageBox.warning(self, "Fehler", "Karte konnte nicht gelesen werden")
            self.info.setText("")
            return
        user = models.get_user_by_uid(uid)
        if not user:
            led.indicate_error()
            QtWidgets.QMessageBox.warning(self, "Fehler", "Unbekannte Karte")
            self.info.setText("")
            return
        old_balance = user.balance
        if not models.update_balance(user.id, amount * 100):
            led.indicate_error()
            QtWidgets.QMessageBox.warning(self, "Fehler", "Aufladen fehlgeschlagen")
            self.info.setText("")
            return
        models.add_topup(user.id, amount * 100)
        new_user = models.get_user_by_uid(uid)
        led.indicate_success()
        msg = (
            f"Aufgeladen!\nAltes Guthaben: {old_balance/100:.2f} €\n"
            f"Neues Guthaben: {new_user.balance/100:.2f} €"
        )
        self.info.setText(msg)
        QtCore.QTimer.singleShot(3000, self._main.show_admin_menu)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        database.init_db()
        database.clear_exit_flag()
        self.setWindowTitle("Getränkekasse")
        self.resize(800, 480)
        self.central = QtWidgets.QWidget()
        self.central.setObjectName("central_widget")
        self.setCentralWidget(self.central)

        logo_path = Path(__file__).resolve().parent.parent / 'data' / 'background.png'
        if logo_path.exists():
            self.central.setStyleSheet(
                "#central_widget{"
                f"background-image: url('{logo_path}');"
                "background-repeat: no-repeat;"
                "background-position: center;"
                "}"
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

        self.admin_menu = AdminMenu(self)
        self.admin_menu.stock_btn.clicked.connect(self.show_stock_page)
        self.admin_menu.topup_btn.clicked.connect(self.show_topup_page)
        self.admin_menu.quit_btn.clicked.connect(self._quit)
        self.admin_menu.back_btn.clicked.connect(self.show_start_page)
        self.stack.addWidget(self.admin_menu)

        self.stock_page = StockPage(self)
        self.stock_page.back_btn.clicked.connect(self.show_admin_menu)
        self.stack.addWidget(self.stock_page)

        self.topup_page = TopupPage(self)
        self.topup_page.back_btn.clicked.connect(self.show_admin_menu)
        self.stack.addWidget(self.topup_page)

        self.show_start_page()

    def _populate_start_page(self) -> None:
        layout = self.start_layout
        conn = database.get_connection()

        self.page_count = models.get_max_page(conn)
        drinks = models.get_drinks(conn, limit=8, page=self.current_page)
        font = QtGui.QFont()
        font.setPointSize(16)

        rows = ((len(drinks) + 1) + 2) // 3
        for row in range(rows):
            layout.setRowStretch(row, 0)

        balance_btn = QtWidgets.QPushButton("Guthaben\nabfragen")
        balance_btn.setFont(font)
        balance_btn.setMinimumSize(220, 120)
        balance_btn.setStyleSheet("background-color: #87cefa;")
        balance_btn.clicked.connect(self._check_balance)
        layout.addWidget(balance_btn, 0, 0)

        for idx, drink in enumerate(drinks):
            button = QtWidgets.QPushButton()
            button.setText(f"{drink.name}\n{drink.price/100:.2f} €")
            button.setFont(font)
            if drink.image:
                button.setIcon(QtGui.QIcon(drink.image))
                button.setIconSize(QtCore.QSize(120, 120))
            button.setMinimumSize(220, 120)

            style = ""
            if drink.stock < 0:
                style = "color: red;"
            elif drink.stock < drink.min_stock:
                style = "color: orange;"

            button.setStyleSheet(style)
            button.clicked.connect(lambda _, d=drink: self.on_drink_selected(d))
            r, c = divmod(idx + 1, 3)
            layout.addWidget(button, r, c)
        self.prev_button = QtWidgets.QPushButton("◀")
        self.next_button = QtWidgets.QPushButton("▶")

        nav_size = QtCore.QSize(int(80 * 4 / 3), int(40 * 4 / 3))
        for btn in (self.prev_button, self.next_button):
            f = btn.font()
            f.setPointSize(20)
            btn.setFont(f)
            btn.setFixedSize(nav_size)
        self.prev_button.clicked.connect(self.prev_page)
        self.next_button.clicked.connect(self.next_page)


        spacer_row = rows
        bottom = rows + 1
        layout.setRowStretch(spacer_row, 0)
        layout.addWidget(self.prev_button, bottom, 0, alignment=QtCore.Qt.AlignBottom)
        layout.addWidget(self.next_button, bottom, 1, alignment=QtCore.Qt.AlignBottom)

        self.admin_button = QtWidgets.QPushButton("Admin")
        f = self.admin_button.font()
        f.setPointSize(12)
        self.admin_button.setFont(f)
        self.admin_button.setFixedSize(nav_size)
        self.admin_button.clicked.connect(self._open_admin)
        layout.addWidget(self.admin_button, bottom, 2,
                         alignment=QtCore.Qt.AlignBottom | QtCore.Qt.AlignRight)
        layout.setRowStretch(bottom, 0)

        self.prev_button.setEnabled(self.current_page > 1)
        self.next_button.setEnabled(self.current_page < self.page_count)

    def show_start_page(self):
        self.stack.setCurrentWidget(self.start_page)

    def show_admin_menu(self) -> None:
        self.stack.setCurrentWidget(self.admin_menu)

    def show_stock_page(self) -> None:
        self.stock_page.reload()
        self.stack.setCurrentWidget(self.stock_page)

    def show_topup_page(self) -> None:
        self.topup_page.info.setText("")
        self.stack.setCurrentWidget(self.topup_page)

    def _check_balance(self) -> None:
        self.info_label.setText("Bitte Karte auflegen…")
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
        led.indicate_success()
        self.info_label.setText(
            f"{user.name}\nGuthaben: {user.balance/100:.2f} €"
        )
        self.stack.setCurrentWidget(self.info_label)
        QtCore.QTimer.singleShot(3000, self.show_start_page)

    def on_drink_selected(self, drink: models.Drink) -> None:
        dialog = QuantityDialog(drink, self)
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
        if dialog.event_user_id is not None:
            user = models.get_user(dialog.event_user_id)
            if not user:
                QtWidgets.QMessageBox.warning(self, "Fehler", "Benutzer nicht gefunden")
                self.show_start_page()
                return
            total_price = drink.price * quantity
            if not models.update_balance(user.id, -total_price):
                QtWidgets.QMessageBox.information(
                    self, "Guthaben", "Limit überschritten - bitte Guthaben aufladen"
                )
                self.show_start_page()
                return
            models.update_drink_stock(drink.id, -quantity)
            models.add_transaction(user.id, drink.id, quantity)
            led.indicate_success()
            self.info_label.setText(
                f"Danke {user.name}!\nVeranstaltung wird verbucht."
            )
            self.stack.setCurrentWidget(self.info_label)
            QtCore.QTimer.singleShot(4000, self.show_start_page)
            return
        self.info_label.setText("Bitte Karte auflegen…")
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

    def _open_admin(self) -> None:
        self.info_label.setText("Bitte Admin-Karte auflegen…")
        self.stack.setCurrentWidget(self.info_label)
        uid = rfid.read_uid(show_dialog=False)
        if not uid:
            QtWidgets.QMessageBox.warning(self, "Fehler", "Karte konnte nicht gelesen werden")
            self.show_start_page()
            return
        user = models.get_user_by_uid(uid)
        if not user or not user.is_admin:
            led.indicate_error()
            QtWidgets.QMessageBox.warning(self, "Fehler", "Kein Zugang")
            self.show_start_page()
            return
        led.indicate_success()
        self.show_admin_menu()

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

