from __future__ import annotations

from PyQt5 import QtCore, QtGui, QtWidgets

from .. import database
from .. import models
from .. import rfid


class QuantityDialog(QtWidgets.QDialog):
    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Menge wählen")
        self.quantity = 1
        layout = QtWidgets.QVBoxLayout(self)
        self.spin = QtWidgets.QSpinBox()
        self.spin.setRange(1, 10)
        layout.addWidget(self.spin)
        btns = QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        self.btnBox = QtWidgets.QDialogButtonBox(btns)
        self.btnBox.accepted.connect(self.accept)
        self.btnBox.rejected.connect(self.reject)
        layout.addWidget(self.btnBox)

    def accept(self) -> None:
        self.quantity = self.spin.value()
        super().accept()


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        database.init_db()
        self.setWindowTitle("Getränkekasse")
        self.resize(800, 480)
        self.central = QtWidgets.QWidget()
        self.setCentralWidget(self.central)
        self.stack = QtWidgets.QStackedLayout(self.central)

        self.start_page = QtWidgets.QWidget()
        self._setup_start_page()
        self.stack.addWidget(self.start_page)

        self.info_label = QtWidgets.QLabel()
        self.info_label.setAlignment(QtCore.Qt.AlignCenter)
        self.stack.addWidget(self.info_label)

        self.show_start_page()

    def _setup_start_page(self) -> None:
        layout = QtWidgets.QGridLayout(self.start_page)
        conn = database.get_connection()
        drinks = models.get_drinks(conn)
        for idx, drink in enumerate(drinks):
            button = QtWidgets.QPushButton()
            button.setText(f"{drink.name}\n{drink.price/100:.2f} €")
            if drink.image:
                button.setIcon(QtGui.QIcon(drink.image))
                button.setIconSize(QtCore.QSize(100, 100))
            button.clicked.connect(lambda _, d=drink: self.on_drink_selected(d))
            r, c = divmod(idx, 3)
            layout.addWidget(button, r, c)
        self.buy_button = QtWidgets.QPushButton("Kaufen")
        self.cancel_button = QtWidgets.QPushButton("Abbrechen")
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
        self.info_label.setText("Bitte Karte auflegen…")
        self.stack.setCurrentWidget(self.info_label)
        uid = rfid.read_uid()
        if not uid:
            QtWidgets.QMessageBox.warning(self, "Fehler", "Karte konnte nicht gelesen werden")
            self.show_start_page()
            return
        user = models.get_user_by_uid(uid)
        if not user:
            QtWidgets.QMessageBox.warning(self, "Fehler", "Unbekannte Karte")
            self.show_start_page()
            return
        total_price = drink.price * quantity
        old_balance = user.balance
        if not models.update_balance(user.id, -total_price):
            QtWidgets.QMessageBox.information(self, "Guthaben", "Nicht genug Guthaben")
            self.show_start_page()
            return
        models.add_transaction(user.id, drink.id, quantity)
        new_user = models.get_user_by_uid(uid)
        self.info_label.setText(
            f"Danke {new_user.name}!\nAltes Guthaben: {old_balance/100:.2f} €\n"
            f"Neues Guthaben: {new_user.balance/100:.2f} €")
        QtCore.QTimer.singleShot(3000, self.show_start_page)
        self.stack.setCurrentWidget(self.info_label)
