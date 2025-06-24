#!/bin/bash
set -e

sudo apt install python3-pyqt5


# create venv and install requirements
if [ -d venv ]; then
    rm -rf venv
fi

python3 -m venv venv --system-site-packages

source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

./venv/bin/python -c "import src.database as d; d.init_db()"

echo "Installation abgeschlossen"

