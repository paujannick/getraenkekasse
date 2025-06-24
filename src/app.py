import argparse
from PyQt5 import QtWidgets

from .gui.main_window import MainWindow


def main() -> None:
    parser = argparse.ArgumentParser(description="Getränkekasse")
    parser.add_argument('--fullscreen', action='store_true', help='Fullscreen GUI')
    args = parser.parse_args()

    app = QtWidgets.QApplication([])
    window = MainWindow()
    if args.fullscreen:
        window.showFullScreen()
    else:
        window.show()
    app.exec_()


if __name__ == "__main__":
    main()
