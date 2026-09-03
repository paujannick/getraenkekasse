#!/usr/bin/env bash
# Update-Skript für die Getränkekasse.
# Läuft verlustfrei: Auto-Backup vor jeder Migration, Rollback bei Fehler.
set -euo pipefail
cd "$(dirname "$0")"

# --------------------------------------------------------------------------
# 1) Code aktualisieren
# --------------------------------------------------------------------------
if git config --get remote.origin.url > /dev/null 2>&1; then
    echo "==> git pull"
    git pull --ff-only
fi

# --------------------------------------------------------------------------
# 2) venv wählen / anlegen
# --------------------------------------------------------------------------
if [ -x .venv/bin/python ]; then
    PY=".venv/bin/python"
elif [ -x venv/bin/python ]; then
    PY="venv/bin/python"
else
    echo "==> Lege venv an"
    python3 -m venv venv --system-site-packages
    PY="venv/bin/python"
fi
echo "==> Python: $PY"

"$PY" -m pip install --upgrade pip setuptools wheel

# --------------------------------------------------------------------------
# 3) Dependencies (auf Pi mit requirements-pi.txt inkl. PyQt5/RFID)
# --------------------------------------------------------------------------
if [ -f requirements-pi.txt ] && command -v raspi-config >/dev/null 2>&1; then
    "$PY" -m pip install --upgrade -r requirements-pi.txt
else
    "$PY" -m pip install --upgrade -r requirements.txt
fi

# --------------------------------------------------------------------------
# 4) Backup vor jeder Änderung (falls DB vorhanden)
# --------------------------------------------------------------------------
if [ -f data/getraenkekasse.db ]; then
    echo "==> Auto-Backup"
    "$PY" - <<'PY'
from src import backups
info = backups.create_backup()
print(f"  {info.path.name}  ({info.size} bytes)  sha256={info.sha256[:16]}...")
PY
fi

# --------------------------------------------------------------------------
# 5) Migrationen anwenden (init_db + upgrade). Bei Fehler wird das obige
#    Backup automatisch wiederhergestellt.
# --------------------------------------------------------------------------
echo "==> Datenbank-Migrationen"
"$PY" - <<'PY'
from src import database, migrations
database.init_db()
applied = migrations.upgrade()
print(f"  Schema-Version: {migrations.current_version()}  |  neu angewendet: {applied or 'keine'}")
PY

# --------------------------------------------------------------------------
# 6) systemd-Service ggf. neu starten
# --------------------------------------------------------------------------
if systemctl is-active --quiet getraenkekasse-web 2>/dev/null; then
    echo "==> systemctl restart getraenkekasse-web"
    sudo systemctl restart getraenkekasse-web || true
fi
if systemctl --user is-active --quiet getraenkekasse-kiosk 2>/dev/null; then
    echo "==> systemctl --user restart getraenkekasse-kiosk"
    systemctl --user restart getraenkekasse-kiosk || true
fi

echo
echo "✅ Update abgeschlossen."
echo
echo "Falls nicht über systemd betrieben, jetzt starten mit:"
echo "   ./start.sh              # Web-Admin + Chromium-Kiosk (Standard)"
echo "   GK_UI=pyqt ./start.sh   # alte PyQt-GUI (Fallback)"
echo "   GK_UI=off  ./start.sh   # nur Web-Admin, ohne Kiosk-Browser"
