from __future__ import annotations

from pathlib import Path
import platform
import random
from typing import Any
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
        self.setWindowFlag(QtCore.Qt.FramelessWindowHint)
        self.setWindowState(QtCore.Qt.WindowFullScreen)
        self.quantity = 1

        screen = QtWidgets.QApplication.primaryScreen()
        screen_size = screen.size() if screen else QtCore.QSize()
        self._compact_layout = bool(
            screen and min(screen_size.width(), screen_size.height()) <= 600
        )

        layout = QtWidgets.QVBoxLayout(self)
        if self._compact_layout:
            layout.setContentsMargins(28, 20, 28, 20)
            layout.setSpacing(18)
        else:
            layout.setContentsMargins(40, 30, 40, 30)
            layout.setSpacing(24)

        self.setObjectName("quantity_dialog")

        style = {
            "title_font": "26px" if self._compact_layout else "32px",
            "info_font": "14px" if self._compact_layout else "16px",
            "frame_padding": "18px" if self._compact_layout else "24px",
            "quantity_font": "40px" if self._compact_layout else "48px",
            "quantity_btn_radius": "36px" if self._compact_layout else "44px",
            "quantity_btn_size": "110px" if self._compact_layout else "140px",
            "quantity_btn_font": "36px" if self._compact_layout else "42px",
            "payment_font": "18px" if self._compact_layout else "20px",
            "payment_padding": "16px" if self._compact_layout else "20px",
            "payment_radius": "18px" if self._compact_layout else "20px",
            "payment_height": "90px" if self._compact_layout else "110px",
            "action_font": "16px" if self._compact_layout else "18px",
            "action_padding": "16px" if self._compact_layout else "20px",
            "action_height": "60px" if self._compact_layout else "70px",
            "cancel_font": "18px" if self._compact_layout else "20px",
        }

        base_style = f"""
            #quantity_dialog {{
                background-color: #f4f6fb;
            }}
            #quantity_dialog QLabel#title_label {{
                font-size: {style['title_font']};
                font-weight: 700;
                color: #0f172a;
            }}
            #quantity_dialog QLabel#info_label {{
                font-size: {style['info_font']};
                color: #475569;
            }}
            #quantity_dialog QFrame#quantity_frame {{
                background-color: #ffffff;
                border-radius: 24px;
                padding: {style['frame_padding']};
                border: 2px solid #e2e8f0;
            }}
            #quantity_dialog QLabel#quantity_value {{
                font-size: {style['quantity_font']};
                font-weight: 700;
                color: #0f172a;
            }}
            #quantity_dialog QPushButton[btnClass="quantity"] {{
                border-radius: {style['quantity_btn_radius']};
                background-color: #1f2937;
                color: #ffffff;
                font-size: {style['quantity_btn_font']};
                min-width: {style['quantity_btn_size']};
                min-height: {style['quantity_btn_size']};
            }}
            #quantity_dialog QGroupBox#payment_group {{
                border: 2px solid #e2e8f0;
                border-radius: 24px;
                margin-top: 12px;
                background-color: #ffffff;
                font-size: 18px;
                font-weight: 600;
                color: #0f172a;
                padding: {style['frame_padding']};
            }}
            #quantity_dialog QPushButton[btnClass="payment"] {{
                border-radius: {style['payment_radius']};
                background-color: #2563eb;
                color: #ffffff;
                font-size: {style['payment_font']};
                font-weight: 600;
                min-height: {style['payment_height']};
                padding: {style['payment_padding']};
                text-align: center;
            }}
            #quantity_dialog QPushButton[btnClass="payment"][variant="cash"] {{
                background-color: #f97316;
            }}
            #quantity_dialog QPushButton[btnClass="payment"][variant="event"] {{
                background-color: #0ea5e9;
            }}
            #quantity_dialog QPushButton[btnClass="payment"]:hover {{
                background-color: #1d4ed8;
            }}
            #quantity_dialog QPushButton[btnClass="action"] {{
                border-radius: 16px;
                background-color: #e2e8f0;
                color: #1f2937;
                font-size: {style['action_font']};
                font-weight: 600;
                min-height: {style['action_height']};
                padding: {style['action_padding']};
            }}
            #quantity_dialog QPushButton[btnClass="action"]:hover {{
                background-color: #cbd5f5;
            }}
            #quantity_dialog QPushButton[btnClass="action"][variant="cancel"] {{
                background-color: #ef4444;
                color: #ffffff;
                font-size: {style['cancel_font']};
            }}
            #quantity_dialog QPushButton[btnClass="action"][variant="cancel"]:hover {{
                background-color: #dc2626;
            }}
        """
        self.setStyleSheet(base_style)

        pixmap = QtGui.QPixmap(drink.image) if drink.image else QtGui.QPixmap()
        if not pixmap.isNull():
            image_path = Path(drink.image).as_posix()
            style = (
                f"#quantity_dialog{{"
                f"background-image: url('{image_path}');"
                "background-repeat: no-repeat;"
                "background-position: top right;"
                "background-origin: content;"
                "}}"
            )
            existing_style = self.styleSheet()
            self.setStyleSheet(f"{existing_style}\n{style}" if existing_style else style)

        self.product_label = QtWidgets.QLabel(drink.name)
        self.product_label.setObjectName("title_label")
        self.product_label.setAlignment(QtCore.Qt.AlignCenter)
        self.product_label.setWordWrap(True)
        layout.addWidget(self.product_label)

        info_label = QtWidgets.QLabel(
            "Bitte Menge wählen und danach eine Zahlungsart antippen."
        )
        info_label.setObjectName("info_label")
        info_label.setAlignment(QtCore.Qt.AlignCenter)
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        qty_frame = QtWidgets.QFrame()
        qty_frame.setObjectName("quantity_frame")
        qty_layout = QtWidgets.QHBoxLayout(qty_frame)
        if self._compact_layout:
            qty_layout.setContentsMargins(12, 12, 12, 12)
            qty_layout.setSpacing(16)
        else:
            qty_layout.setContentsMargins(16, 16, 16, 16)
            qty_layout.setSpacing(24)
        self.minus_btn = QtWidgets.QPushButton("−")
        self.plus_btn = QtWidgets.QPushButton("+")
        for btn in (self.minus_btn, self.plus_btn):
            btn.setProperty("btnClass", "quantity")
            size = 110 if self._compact_layout else 140
            btn.setMinimumSize(size, size)
        self.label = QtWidgets.QLabel(f"{self.quantity} Stück")
        self.label.setObjectName("quantity_value")
        self.label.setAlignment(QtCore.Qt.AlignCenter)
        self.label.setMinimumWidth(200 if self._compact_layout else 220)
        qty_layout.addWidget(self.minus_btn)
        qty_layout.addWidget(self.label, stretch=1)
        qty_layout.addWidget(self.plus_btn)
        layout.addWidget(qty_frame)

        self.minus_btn.clicked.connect(self.dec)
        self.plus_btn.clicked.connect(self.inc)

        payment_group = QtWidgets.QGroupBox("Zahlungsart wählen")
        payment_group.setObjectName("payment_group")
        payment_group.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding
        )
        payment_layout = QtWidgets.QGridLayout(payment_group)
        if self._compact_layout:
            payment_layout.setContentsMargins(10, 18, 10, 10)
            payment_layout.setHorizontalSpacing(14)
            payment_layout.setVerticalSpacing(14)
        else:
            payment_layout.setContentsMargins(12, 24, 12, 12)
            payment_layout.setHorizontalSpacing(18)
            payment_layout.setVerticalSpacing(18)

        self.cash_btn = QtWidgets.QPushButton("Barzahlung")
        self.cash_btn.setProperty("btnClass", "payment")
        self.cash_btn.setProperty("variant", "cash")
        self.cash_btn.setMinimumSize(
            200 if self._compact_layout else 220,
            90 if self._compact_layout else 110,
        )
        self.cash_btn.clicked.connect(self.cash)
        self.chip_btn = QtWidgets.QPushButton("Chip / Karte")
        self.chip_btn.setProperty("btnClass", "payment")
        self.chip_btn.setProperty("variant", "chip")
        self.chip_btn.setMinimumSize(
            200 if self._compact_layout else 220,
            90 if self._compact_layout else 110,
        )
        self.chip_btn.clicked.connect(self.accept)

        payment_buttons: list[QtWidgets.QWidget] = [self.cash_btn, self.chip_btn]

        self._event_user_id: int | None = None
        event_users = models.get_event_payment_users()
        for user in event_users:
            btn = QtWidgets.QPushButton(
                f"{user.name}\n{user.balance / 100:.2f} €"
            )
            btn.setProperty("btnClass", "payment")
            btn.setProperty("variant", "event")
            btn.setToolTip("Veranstaltungskarte direkt belasten")
            btn.setMinimumSize(
                200 if self._compact_layout else 220,
                90 if self._compact_layout else 110,
            )
            btn.clicked.connect(lambda _, uid=user.id: self._select_event_user(uid))
            payment_buttons.append(btn)

        columns = 3
        for index, btn in enumerate(payment_buttons):
            row, col = divmod(index, columns)
            payment_layout.addWidget(btn, row, col)
            payment_layout.setColumnStretch(col, 1)

        for col in range(columns):
            if payment_layout.columnStretch(col) == 0:
                payment_layout.setColumnStretch(col, 1)

        if not event_users:
            hint = QtWidgets.QLabel(
                "Keine Veranstaltungskarten für die Schnellzahlung aktiviert."
            )
            hint.setObjectName("info_label")
            hint.setAlignment(QtCore.Qt.AlignCenter)
            hint.setWordWrap(True)
            rows_used = (len(payment_buttons) + columns - 1) // columns
            payment_layout.addWidget(hint, rows_used, 0, 1, columns)

        layout.addWidget(payment_group, stretch=1)

        layout.addStretch(1)

        cancel_btn = QtWidgets.QPushButton("Abbrechen")
        cancel_btn.setProperty("btnClass", "action")
        cancel_btn.setProperty("variant", "cancel")
        cancel_btn.setMinimumHeight(70 if self._compact_layout else 90)
        cancel_btn.setMinimumWidth(240 if self._compact_layout else 260)
        cancel_btn.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Fixed,
        )
        cancel_btn.clicked.connect(self.reject)
        action_layout = QtWidgets.QHBoxLayout()
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.addStretch(1)
        action_layout.addWidget(cancel_btn, stretch=2)
        action_layout.addStretch(1)
        layout.addLayout(action_layout)

        self._cash = False

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        self.setWindowState(QtCore.Qt.WindowFullScreen)

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


class PinDialog(QtWidgets.QDialog):
    """Dialog to enter the admin PIN via on-screen buttons."""

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Admin-PIN")
        self.setWindowState(QtCore.Qt.WindowFullScreen)
        layout = QtWidgets.QVBoxLayout(self)

        self.edit = QtWidgets.QLineEdit(alignment=QtCore.Qt.AlignCenter)
        font = self.edit.font()
        font.setPointSize(24)
        self.edit.setFont(font)
        self.edit.setEchoMode(QtWidgets.QLineEdit.Password)
        layout.addWidget(self.edit)

        grid = QtWidgets.QGridLayout()
        buttons = [
            ('1', 0, 0), ('2', 0, 1), ('3', 0, 2),
            ('4', 1, 0), ('5', 1, 1), ('6', 1, 2),
            ('7', 2, 0), ('8', 2, 1), ('9', 2, 2),
            ('←', 3, 0), ('0', 3, 1), ('C', 3, 2),
        ]
        for text, r, c in buttons:
            btn = QtWidgets.QPushButton(text)
            f = btn.font()
            f.setPointSize(24)
            btn.setFont(f)
            btn.setMinimumSize(100, 80)
            grid.addWidget(btn, r, c)
            if text.isdigit():
                btn.clicked.connect(lambda _, t=text: self.edit.insert(t))
            elif text == '←':
                btn.clicked.connect(lambda _=None: self.edit.backspace())
            else:  # 'C'
                btn.clicked.connect(self.edit.clear)
        layout.addLayout(grid)

        btn_layout = QtWidgets.QHBoxLayout()
        ok_btn = QtWidgets.QPushButton("OK")
        cancel_btn = QtWidgets.QPushButton("Abbrechen")
        for btn in (ok_btn, cancel_btn):
            f = btn.font()
            f.setPointSize(20)
            btn.setFont(f)
            btn.setMinimumHeight(60)
            btn_layout.addWidget(btn)
        ok_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        layout.addLayout(btn_layout)

    @property
    def pin(self) -> str:
        return self.edit.text()


class TicTacToeDialog(QtWidgets.QDialog):
    """Simple Tic-Tac-Toe game against the computer."""

    _WIN_LINES = (
        (0, 1, 2), (3, 4, 5), (6, 7, 8),
        (0, 3, 6), (1, 4, 7), (2, 5, 8),
        (0, 4, 8), (2, 4, 6),
    )

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Tic Tac Toe")
        self.setWindowState(QtCore.Qt.WindowFullScreen)
        self.setModal(True)
        self.setWindowFlag(QtCore.Qt.WindowCloseButtonHint, False)
        self.setObjectName("tictactoe_dialog")

        base_style = """
            #tictactoe_dialog {
                background-color: #0f172a;
            }
            #tictactoe_container {
                background-color: rgba(15, 23, 42, 0.88);
                border-radius: 32px;
                border: 2px solid #334155;
                padding: 32px;
            }
            #tictactoe_info {
                color: #f8fafc;
                font-weight: 600;
            }
            QPushButton[ttt="cell"] {
                border-radius: 18px;
                background-color: #1e293b;
                color: #38bdf8;
                font-weight: 700;
                border: 3px solid #334155;
                min-width: 120px;
                min-height: 120px;
            }
            QPushButton[ttt="cell"]:hover {
                background-color: #0ea5e9;
                color: #0f172a;
            }
            QPushButton[ttt="cell"]:disabled {
                background-color: #0f172a;
                color: #38bdf8;
            }
            QPushButton[ttt="cell"][symbol="O"] {
                color: #f472b6;
            }
            QPushButton[ttt="cell"][state="winner"] {
                background-color: #22c55e;
                color: #0f172a;
            }
            QPushButton[ttt="close"] {
                border-radius: 16px;
                background-color: #38bdf8;
                color: #0f172a;
                font-weight: 700;
                padding: 18px 32px;
            }
            QPushButton[ttt="close"]:hover {
                background-color: #0ea5e9;
            }
        """
        self.setStyleSheet(base_style)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(60, 40, 60, 40)

        container = QtWidgets.QFrame()
        container.setObjectName("tictactoe_container")
        container_layout = QtWidgets.QVBoxLayout(container)
        container_layout.setSpacing(30)
        container_layout.setAlignment(QtCore.Qt.AlignCenter)

        self.info_label = QtWidgets.QLabel("Schlage den Computer! Du spielst 'X'.")
        self.info_label.setObjectName("tictactoe_info")
        screen = QtWidgets.QApplication.primaryScreen()
        geometry = screen.availableGeometry() if screen else QtCore.QRect(0, 0, 1280, 720)
        height = geometry.height()
        width = geometry.width()

        base_font_size = max(20, min(32, height // 15))
        font = self.info_label.font()
        font.setPointSize(base_font_size)
        self.info_label.setFont(font)
        self.info_label.setAlignment(QtCore.Qt.AlignCenter)
        container_layout.addWidget(self.info_label)

        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(18)
        self._board: list[str] = [""] * 9
        self._buttons: list[QtWidgets.QPushButton] = []
        button_size = max(110, min(180, int(min(width, height) / 4)))
        symbol_font_size = max(32, int(button_size * 0.4))
        for index in range(9):
            button = QtWidgets.QPushButton("")
            btn_font = button.font()
            btn_font.setPointSize(symbol_font_size)
            button.setFont(btn_font)
            button.setFixedSize(button_size, button_size)
            button.setProperty("ttt", "cell")
            button.clicked.connect(lambda _, idx=index: self._player_move(idx))
            self._buttons.append(button)
            grid.addWidget(button, index // 3, index % 3)
        container_layout.addLayout(grid)

        self._close_button = QtWidgets.QPushButton("Schließen")
        self._close_button.setProperty("ttt", "close")
        close_font = self._close_button.font()
        close_font.setPointSize(max(18, int(base_font_size * 0.85)))
        self._close_button.setFont(close_font)
        self._close_button.setMinimumWidth(max(200, button_size))
        self._close_button.clicked.connect(self.accept)
        self._close_button.hide()
        container_layout.addWidget(self._close_button, alignment=QtCore.Qt.AlignCenter)

        layout.addWidget(container, alignment=QtCore.Qt.AlignCenter)

        self._game_over = False
        self._result: str | None = None
        self._winning_line: tuple[int, int, int] | None = None

    @property
    def result(self) -> str | None:
        return self._result

    def _player_move(self, index: int) -> None:
        if self._game_over or self._board[index]:
            return
        self._winning_line = None
        self._set_move(index, 'X')
        if self._check_winner('X'):
            self._finish('win', "Du hast gewonnen! Dein Getränk ist gratis.")
            return
        if self._is_draw():
            self._finish('draw', "Unentschieden! Preis bleibt gleich.")
            return
        self.info_label.setText("Computer ist am Zug…")
        QtWidgets.QApplication.processEvents()
        self._computer_move()

    def _computer_move(self) -> None:
        self._winning_line = None
        move = self._find_best_move('O')
        if move is None:
            move = self._find_best_move('X')
        if move is None and not self._board[4]:
            move = 4
        if move is None:
            corners = [i for i in (0, 2, 6, 8) if not self._board[i]]
            if corners:
                move = random.choice(corners)
        if move is None:
            available = [i for i, cell in enumerate(self._board) if not cell]
            move = random.choice(available)
        self._set_move(move, 'O')
        if self._check_winner('O'):
            self._finish('lose', "Der Computer hat gewonnen. Getränk kostet doppelt.")
            return
        if self._is_draw():
            self._finish('draw', "Unentschieden! Preis bleibt gleich.")
            return
        self.info_label.setText("Du bist wieder dran!")

    def _find_best_move(self, symbol: str) -> int | None:
        for line in self._WIN_LINES:
            values = [self._board[i] for i in line]
            if values.count(symbol) == 2 and values.count('') == 1:
                return line[values.index('')]
        return None

    def _set_move(self, index: int, symbol: str) -> None:
        self._board[index] = symbol
        button = self._buttons[index]
        button.setText(symbol)
        button.setProperty("symbol", symbol)
        button.setProperty("state", "")
        button.setEnabled(False)
        self._refresh_button_style(button)

    def _check_winner(self, symbol: str) -> bool:
        for line in self._WIN_LINES:
            if all(self._board[i] == symbol for i in line):
                self._winning_line = line
                return True
        return False

    def _is_draw(self) -> bool:
        return all(cell for cell in self._board)

    def _finish(self, result: str, message: str) -> None:
        self._game_over = True
        self._result = result
        self.info_label.setText(message)
        for button in self._buttons:
            button.setEnabled(False)
            self._refresh_button_style(button)
        if self._winning_line:
            for index in self._winning_line:
                btn = self._buttons[index]
                btn.setProperty("state", "winner")
                self._refresh_button_style(btn)
        self._close_button.show()

    def _refresh_button_style(self, button: QtWidgets.QPushButton) -> None:
        button.style().unpolish(button)
        button.style().polish(button)
        button.update()


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
        self.event_cards_btn = QtWidgets.QPushButton("Veranstaltungskarten")
        self.status_btn = QtWidgets.QPushButton("Status")
        self.web_btn = QtWidgets.QPushButton()
        self.quit_btn = QtWidgets.QPushButton("Beenden")
        self.back_btn = QtWidgets.QPushButton("Zurück")

        for btn in (
            self.stock_btn,
            self.topup_btn,
            self.event_cards_btn,
            self.status_btn,
            self.web_btn,
            self.quit_btn,
            self.back_btn,
        ):
            f = btn.font()
            f.setPointSize(20)
            btn.setFont(f)
            btn.setMinimumHeight(60)
            layout.addWidget(btn)

        self.reload_web_qr()

        layout.addStretch(1)

    def reload_web_qr(self) -> None:
        self.web_btn.setIcon(QtGui.QIcon())
        self.web_btn.setText("Webinterface")


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


class EventCardPage(QtWidgets.QWidget):
    """Page to enable/disable event cards and their visibility."""

    def __init__(self, parent: "MainWindow"):
        super().__init__(parent)
        self._main = parent
        layout = QtWidgets.QVBoxLayout(self)

        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Karte", "Aktiv", "Anzeigen"])
        f = self.table.font()
        f.setPointSize(16)
        self.table.setFont(f)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.table.setFocusPolicy(QtCore.Qt.NoFocus)
        layout.addWidget(self.table)

        btn_layout = QtWidgets.QHBoxLayout()
        self.save_btn = QtWidgets.QPushButton("Speichern")
        self.back_btn = QtWidgets.QPushButton("Zurück")
        for btn in (self.save_btn, self.back_btn):
            f = btn.font()
            f.setPointSize(16)
            btn.setFont(f)
            btn.setMinimumHeight(60)
            btn_layout.addWidget(btn)
        layout.addLayout(btn_layout)

        self.save_btn.clicked.connect(self.save)
        self.back_btn.clicked.connect(self._main.show_admin_menu)
        self._rows: list[tuple[int, QtWidgets.QCheckBox, QtWidgets.QCheckBox]] = []

    def reload(self) -> None:
        self.table.setRowCount(0)

        for _, active_box, show_box in self._rows:
            active_box.deleteLater()
            show_box.deleteLater()
        self._rows.clear()

        conn = database.get_connection()
        cur = conn.execute(
            'SELECT id, name, active, show_on_payment FROM users WHERE is_event=1 ORDER BY name'
        )
        for user in cur.fetchall():
            row = self.table.rowCount()
            self.table.insertRow(row)

            name_item = QtWidgets.QTableWidgetItem(user['name'])
            name_item.setFlags(name_item.flags() & ~QtCore.Qt.ItemIsEditable)
            self.table.setItem(row, 0, name_item)

            style = (
                "QCheckBox{margin-left:auto; margin-right:auto;}"
                "QCheckBox::indicator{width:40px;height:40px;}"
            )
            active_box = QtWidgets.QCheckBox()
            active_box.setChecked(bool(user['active']))
            active_box.setStyleSheet(style)
            self.table.setCellWidget(row, 1, active_box)

            show_box = QtWidgets.QCheckBox()
            show_box.setChecked(bool(user['show_on_payment']))
            show_box.setStyleSheet(style)
            self.table.setCellWidget(row, 2, show_box)

            self.table.setRowHeight(row, 50)
            self._rows.append((user['id'], active_box, show_box))
        conn.close()

    def save(self) -> None:
        conn = database.get_connection()
        for uid, active_box, show_box in self._rows:
            active = 1 if active_box.isChecked() else 0
            show = 1 if show_box.isChecked() else 0
            conn.execute(
                'UPDATE users SET active=?, show_on_payment=? WHERE id=? AND is_event=1',
                (active, show, uid),
            )
        conn.commit()
        conn.close()
        self._main.show_admin_menu()


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        database.init_db()
        database.clear_exit_flag()
        self.setWindowTitle("Getränkekasse")
        self.setWindowState(self.windowState() | QtCore.Qt.WindowFullScreen)
        self.central = QtWidgets.QWidget()
        self.central.setObjectName("central_widget")
        self.setCentralWidget(self.central)

        self._game_enabled: bool = True
        self._setup_styles()

        data_dir = Path(__file__).resolve().parent.parent / 'data'
        self._default_bg = data_dir / 'background.png'
        self._thank_bg = data_dir / 'background_thanks.png'
        self._apply_background(self._default_bg)

        self.stack = QtWidgets.QStackedLayout(self.central)
        self._info_timer = QtCore.QTimer(self)
        self._info_timer.setSingleShot(True)
        self._info_timer.timeout.connect(self.show_start_page)

        self.refresh_mtime = database.REFRESH_FLAG.stat().st_mtime if database.REFRESH_FLAG.exists() else 0.0
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.check_refresh)
        self.timer.start(3000)

        self.start_page = QtWidgets.QWidget()
        self.start_layout = QtWidgets.QGridLayout(self.start_page)
        self.start_layout.setContentsMargins(30, 30, 30, 30)
        self.start_layout.setHorizontalSpacing(24)
        self.start_layout.setVerticalSpacing(24)
        self.current_page = 1
        self.page_count = 1
        self._start_page_needs_refresh = False
        self._populate_start_page()


        self.stack.addWidget(self.start_page)

        self.info_page = QtWidgets.QWidget()
        info_layout = QtWidgets.QVBoxLayout(self.info_page)
        info_layout.addStretch()
        self.info_label = QtWidgets.QLabel()
        self.info_label.setAlignment(QtCore.Qt.AlignCenter)
        font = self.info_label.font()
        font.setPointSize(24)
        self.info_label.setFont(font)
        self.info_label.setWordWrap(True)
        info_layout.addWidget(self.info_label, alignment=QtCore.Qt.AlignCenter)
        self.game_button = QtWidgets.QPushButton("Tic Tac Toe spielen")
        game_font = self.game_button.font()
        game_font.setPointSize(20)
        self.game_button.setFont(game_font)
        self.game_button.setMinimumSize(260, 80)
        self.game_button.setProperty("btnClass", "game")
        self._apply_button_style(self.game_button)
        self.game_button.clicked.connect(self._start_tictactoe)
        self.game_button.hide()
        info_layout.addWidget(self.game_button, alignment=QtCore.Qt.AlignCenter)
        info_layout.addStretch()
        self.stack.addWidget(self.info_page)

        self.admin_menu = AdminMenu(self)
        self.admin_menu.stock_btn.clicked.connect(self.show_stock_page)
        self.admin_menu.topup_btn.clicked.connect(self.show_topup_page)
        self.admin_menu.event_cards_btn.clicked.connect(self.show_event_cards_page)
        self.admin_menu.status_btn.clicked.connect(self.show_status)
        self.admin_menu.web_btn.clicked.connect(self.show_web_qr)
        self.admin_menu.quit_btn.clicked.connect(self._quit)
        self.admin_menu.back_btn.clicked.connect(self.show_start_page)
        self.stack.addWidget(self.admin_menu)

        self.stock_page = StockPage(self)
        self.stock_page.back_btn.clicked.connect(self.show_admin_menu)
        self.stack.addWidget(self.stock_page)

        self.topup_page = TopupPage(self)
        self.topup_page.back_btn.clicked.connect(self.show_admin_menu)
        self.stack.addWidget(self.topup_page)

        self.event_card_page = EventCardPage(self)
        self.stack.addWidget(self.event_card_page)

        self._pending_game: dict[str, Any] | None = None
        self._sync_game_setting()
        self.show_start_page()

    def _apply_background(self, path: Path) -> None:
        if path and path.exists():
            self.central.setStyleSheet(
                "#central_widget{"\
                f"background-image: url('{path}');"\
                "background-repeat: no-repeat;"\
                "background-position: center;"\
                "}"
            )
        else:
            self.central.setStyleSheet("")

    def _setup_styles(self) -> None:
        self.setStyleSheet(
            """
            QPushButton[btnClass='tile'] {
                border-radius: 18px;
                background-color: #2a9d8f;
                color: #ffffff;
                font-size: 18px;
                font-weight: 600;
                padding: 18px 16px;
            }
            QPushButton[btnClass='tile'][accent='info'] {
                background-color: #4f46e5;
            }
            QPushButton[btnClass='tile'][accent='secondary'] {
                background-color: #028090;
            }
            QPushButton[btnClass='tile'][state='warning'] {
                background-color: #f4a261;
                color: #1f2937;
            }
            QPushButton[btnClass='tile'][state='error'] {
                background-color: #e76f51;
            }
            QPushButton[btnClass='tile']:hover {
                background-color: #23867b;
            }
            QPushButton[btnClass='nav'] {
                border-radius: 12px;
                background-color: rgba(15, 23, 42, 0.78);
                color: #f8fafc;
                font-size: 16px;
                font-weight: 600;
                padding: 10px 14px;
            }
            QPushButton[btnClass='nav'][accent='admin'] {
                background-color: #ef4444;
            }
            QPushButton[btnClass='nav']:disabled {
                background-color: rgba(148, 163, 184, 0.45);
                color: #e2e8f0;
            }
            QPushButton[btnClass='game'] {
                border-radius: 18px;
                background-color: #9333ea;
                color: #ffffff;
                font-size: 20px;
                font-weight: 700;
                padding: 18px 28px;
            }
            QPushButton[btnClass='game']:hover {
                background-color: #7c3aed;
            }
        """
        )

    @staticmethod
    def _apply_button_style(button: QtWidgets.QPushButton) -> None:
        style = button.style()
        style.unpolish(button)
        style.polish(button)
        button.update()

    def _sync_game_setting(self) -> None:
        self._game_enabled = models.is_game_enabled()
        if not self._game_enabled:
            self._pending_game = None
            self.game_button.hide()

    def _game_message_duration(self) -> int:
        return 12000 if self._game_enabled else 5000

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
        balance_btn.setProperty("btnClass", "tile")
        balance_btn.setProperty("accent", "info")
        self._apply_button_style(balance_btn)
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
            button.setProperty("btnClass", "tile")
            if drink.stock < 0:
                button.setProperty("state", "error")
            elif drink.stock < drink.min_stock:
                button.setProperty("state", "warning")
            else:
                button.setProperty("state", "normal")
            self._apply_button_style(button)
            button.clicked.connect(lambda _, d=drink: self.on_drink_selected(d))
            r, c = divmod(idx + 1, 3)
            layout.addWidget(button, r, c)
        self.prev_button = QtWidgets.QPushButton("◀")
        self.next_button = QtWidgets.QPushButton("▶")

        nav_size = QtCore.QSize(80, 40)
        for btn in (self.prev_button, self.next_button):
            f = btn.font()
            f.setPointSize(20)
            btn.setFont(f)
            btn.setFixedSize(nav_size)
            btn.setProperty("btnClass", "nav")
            self._apply_button_style(btn)
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
        self.admin_button.setFixedSize(nav_size)
        self.admin_button.setProperty("btnClass", "nav")
        self.admin_button.setProperty("accent", "admin")
        self._apply_button_style(self.admin_button)
        self.admin_button.clicked.connect(self._open_admin)
        layout.addWidget(self.admin_button, bottom, 2,
                         alignment=QtCore.Qt.AlignBottom | QtCore.Qt.AlignRight)
        layout.setRowStretch(bottom, 0)

        self.prev_button.setEnabled(self.current_page > 1)
        self.next_button.setEnabled(self.current_page < self.page_count)

    def show_start_page(self):
        self._info_timer.stop()
        self._pending_game = None
        self.game_button.hide()
        self.game_button.setEnabled(True)
        if self._start_page_needs_refresh:
            self._rebuild_start_page()
            self._start_page_needs_refresh = False
        self._apply_background(self._default_bg)
        self.stack.setCurrentWidget(self.start_page)

    def _show_info_message(
        self,
        message: str,
        *,
        allow_game: bool = False,
        game_context: dict[str, Any] | None = None,
        auto_return_ms: int | None = 4000,
    ) -> None:
        self.info_label.setText(message)
        self._info_timer.stop()
        enable_game = allow_game and self._game_enabled and bool(game_context)
        if enable_game:
            self._pending_game = game_context
            self.game_button.show()
            self.game_button.setEnabled(True)
        else:
            self._pending_game = None
            self.game_button.hide()
            self.game_button.setEnabled(True)
        self.stack.setCurrentWidget(self.info_page)
        if auto_return_ms is not None:
            self._info_timer.start(auto_return_ms)

    def show_admin_menu(self) -> None:
        self.admin_menu.reload_web_qr()
        self.stack.setCurrentWidget(self.admin_menu)

    def show_stock_page(self) -> None:
        self.stock_page.reload()
        self.stack.setCurrentWidget(self.stock_page)

    def show_topup_page(self) -> None:
        self.topup_page.info.setText("")
        self.stack.setCurrentWidget(self.topup_page)

    def show_event_cards_page(self) -> None:
        self.event_card_page.reload()
        self.stack.setCurrentWidget(self.event_card_page)

    def show_status(self) -> None:
        conn = database.get_connection()
        users = conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        drinks = conn.execute('SELECT COUNT(*) FROM drinks').fetchone()[0]
        transactions = conn.execute('SELECT COUNT(*) FROM transactions').fetchone()[0]
        conn.close()
        system = platform.platform()
        python = platform.python_version()
        db_path = database.DB_PATH
        msg = (
            f"Nutzer: {users}\n"
            f"Getränke: {drinks}\n"
            f"Transaktionen: {transactions}\n"
            f"Datenbank: {db_path}\n"
            f"System: {system}\n"
            f"Python: {python}"
        )
        QtWidgets.QMessageBox.information(self, "Status", msg)

    def show_web_qr(self) -> None:
        data_dir = Path(__file__).resolve().parent.parent / 'data'
        path = data_dir / 'web_qr.png'
        if not path.exists():
            QtWidgets.QMessageBox.warning(self, "Fehler", "Kein QR-Code vorhanden.")
            return
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Webinterface QR-Code")
        dlg.setWindowState(QtCore.Qt.WindowFullScreen)
        pixmap = QtGui.QPixmap(str(path))
        screen_size = QtWidgets.QApplication.primaryScreen().availableSize()
        scaled = pixmap.scaled(screen_size, QtCore.Qt.KeepAspectRatio,
                               QtCore.Qt.SmoothTransformation)
        label = QtWidgets.QLabel(alignment=QtCore.Qt.AlignCenter)
        label.setPixmap(scaled)
        layout = QtWidgets.QVBoxLayout(dlg)
        layout.addWidget(label)
        def close(_event):
            dlg.accept()

        label.mousePressEvent = close
        dlg.mousePressEvent = close
        dlg.exec_()

    def _check_balance(self) -> None:
        self._show_info_message("Bitte Karte auflegen…", auto_return_ms=None)
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
        self._show_info_message(
            f"{user.name}\nGuthaben: {user.balance/100:.2f} €",
            auto_return_ms=3000,
        )

    def _start_tictactoe(self) -> None:
        if not self._pending_game:
            return
        context = self._pending_game
        self._pending_game = None
        self._info_timer.stop()
        self.game_button.setEnabled(False)
        self.game_button.hide()

        dialog = TicTacToeDialog(self)
        dialog.exec_()
        result = dialog.result
        if result is None:
            self._show_info_message("Spiel abgebrochen. Preis bleibt unverändert.", auto_return_ms=4000)
            return

        user_id = context.get("user_id")
        if user_id is None:
            self._handle_cash_game_result(result, context)
            return
        total_price = context["total_price"]
        drink_name = context.get("drink_name", "")
        before_user = models.get_user(user_id)
        if before_user is None:
            self._show_info_message(
                "Benutzer konnte nicht ermittelt werden. Preis bleibt unverändert.",
                auto_return_ms=4000,
            )
            return

        message: str
        if result == 'win':
            models.update_balance(user_id, total_price)
            after_user = models.get_user(user_id) or before_user
            message = (
                f"Glückwunsch {after_user.name}! Du hast gewonnen.\n"
                f"{drink_name} ist gratis. Neues Guthaben: {after_user.balance/100:.2f} €"
            )
        elif result == 'lose':
            if models.update_balance(user_id, -total_price):
                after_user = models.get_user(user_id) or before_user
                message = (
                    f"Leider verloren, {after_user.name}.\n"
                    f"{drink_name} kostet nun doppelt. Neues Guthaben: {after_user.balance/100:.2f} €"
                )
            else:
                message = (
                    "Leider verloren! Der Zusatzbetrag konnte nicht verbucht werden.\n"
                    f"Guthaben bleibt bei {before_user.balance/100:.2f} €."
                )
        elif result == 'draw':
            after_user = models.get_user(user_id) or before_user
            message = (
                "Unentschieden! Preis bleibt gleich.\n"
                f"Aktuelles Guthaben: {after_user.balance/100:.2f} €."
            )
        else:
            message = "Spiel abgebrochen. Preis bleibt unverändert."

        self._show_info_message(message, auto_return_ms=4000)

    def _handle_cash_game_result(self, result: str | None, context: dict[str, Any]) -> None:
        total_price = context.get("total_price", 0)
        drink_name = context.get("drink_name") or "dein Getränk"
        base_price_text = f"{total_price / 100:.2f} €" if total_price else "den Betrag"
        message: str
        if result == "win":
            message = (
                "Glückwunsch! Du hast gewonnen.\n"
                f"Du darfst {base_price_text} für {drink_name} behalten."
            )
        elif result == "lose":
            double_total = context.get("double_amount", total_price * 2)
            double_text = f"{double_total / 100:.2f} €" if double_total else "den Betrag noch einmal"
            message = (
                "Leider verloren!\n"
                f"Bitte insgesamt {double_text} in die Getränkekasse legen."
            )
        elif result == "draw":
            message = (
                "Unentschieden! Preis bleibt gleich.\n"
                f"{drink_name} kostet weiterhin {base_price_text}."
            )
        else:
            message = "Spiel abgebrochen. Preis bleibt unverändert."

        self._show_info_message(message, auto_return_ms=5000)

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
            message = f"Bitte {total_price/100:.2f} \u20ac passend in die Getränkekasse legen."
            game_context: dict[str, Any] | None = None
            auto_return = 3000
            if self._game_enabled:
                message += (
                    "\n\nGewinne im Tic Tac Toe, dann darfst du dein Geld behalten. "
                    "Bei einer Niederlage kostet das Getränk doppelt!"
                    "\n\nTippe auf \"Tic Tac Toe spielen\" oder warte kurz,"
                    " dann kehrst du automatisch zum Start zurück."
                )
                game_context = {
                    "user_id": None,
                    "drink_id": drink.id,
                    "quantity": quantity,
                    "total_price": total_price,
                    "drink_name": drink.name,
                    "payment": "cash",
                }
                auto_return = self._game_message_duration()
            else:
                message += "\n\nDu kehrst gleich automatisch zum Startbildschirm zurück."
            self._show_info_message(
                message,
                allow_game=self._game_enabled,
                game_context=game_context,
                auto_return_ms=auto_return,
            )
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
            if self._thank_bg.exists():
                self._apply_background(self._thank_bg)
            thank_message = f"Danke {user.name}!\nKauf wird verbucht."
            thank_message += "\n\nDu kehrst gleich automatisch zum Startbildschirm zurück."
            self._show_info_message(
                thank_message,
                allow_game=False,
                game_context=None,
                auto_return_ms=5000,
            )
            return
        self._show_info_message("Bitte Karte auflegen…", auto_return_ms=None)
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
        if self._thank_bg.exists():
            self._apply_background(self._thank_bg)
        msg = (
            f"Danke {new_user.name}!\nAltes Guthaben: {old_balance/100:.2f} €\n"
            f"Neues Guthaben: {new_user.balance/100:.2f} €"
        )
        if new_user.balance < 0:
            msg += "\nBitte Guthaben aufladen!"
        game_context = {
            "user_id": user.id,
            "drink_id": drink.id,
            "quantity": quantity,
            "total_price": total_price,
            "user_name": new_user.name,
            "drink_name": drink.name,
            "event_user": False,
        }
        if self._game_enabled:
            msg += (
                "\n\nGewinne im Tic Tac Toe, dann ist dein Getränk gratis. "
                "Bei einer Niederlage kostet es doppelt!"
                "\n\nTippe auf \"Tic Tac Toe spielen\" oder warte kurz,"
                " dann kehrst du automatisch zum Start zurück."
            )
        else:
            msg += "\n\nDu kehrst gleich automatisch zum Startbildschirm zurück."
        self._show_info_message(
            msg,
            allow_game=self._game_enabled,
            game_context=game_context if self._game_enabled else None,
            auto_return_ms=self._game_message_duration(),
        )
        QtWidgets.QApplication.processEvents()


    def check_refresh(self) -> None:
        if database.exit_flag_set():
            database.clear_exit_flag()
            QtWidgets.QApplication.quit()
            return
        if database.refresh_needed(self.refresh_mtime):
            self.refresh_mtime = database.REFRESH_FLAG.stat().st_mtime
            self._start_page_needs_refresh = True
            self._sync_game_setting()
            if self.stack.currentWidget() is self.start_page:
                self._rebuild_start_page()
                self._start_page_needs_refresh = False

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
        self._show_info_message("Bitte Admin-Karte auflegen…", auto_return_ms=None)
        uid = rfid.read_uid(show_dialog=False)
        user = models.get_user_by_uid(uid) if uid else None
        if not (user and user.is_admin):
            pin_dialog = PinDialog(self)
            if pin_dialog.exec_() != QtWidgets.QDialog.Accepted or \
                    pin_dialog.pin != models.get_admin_pin():
                led.indicate_error()
                QtWidgets.QMessageBox.warning(self, "Fehler", "Kein Zugang")
                self.show_start_page()
                return
        led.indicate_success()
        self.show_admin_menu()

    def _quit(self) -> None:
        reply = QtWidgets.QMessageBox.question(
            self,
            "Beenden",
            "Wirklich beenden?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if reply == QtWidgets.QMessageBox.Yes:
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

