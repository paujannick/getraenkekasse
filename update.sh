#!/usr/bin/env bash
# Aktualisiert den Code, erneuert Abhängigkeiten und Backup vor Migration.
set -euo pipefail
cd "$(dirname "$0")"

if git config --get remote.origin.url > /dev/null 2>&1; then
    echo "==> git pull"
    git pull --ff-only
fi

echo "==> Backup vor Migration"
if [ -f data/getraenkekasse.db ]; then
    python3 - <<'PY'
from src import backups
info = backups.create_backup()
print(f"backup: {info.path} ({info.size} bytes)")
PY
fi

if [ ! -d venv ]; then
    python3 -m venv venv --system-site-packages
fi
# shellcheck disable=SC1091
source venv/bin/activate

pip install --upgrade pip setuptools wheel

if [ -f requirements-pi.txt ] && command -v raspi-config >/dev/null 2>&1; then
    pip install --upgrade -r requirements-pi.txt
else
    pip install --upgrade -r requirements.txt
fi

echo "==> Migration"
venv/bin/python - <<'PY'
from src import database
database.init_db()
PY

echo "==> Update abgeschlossen"
