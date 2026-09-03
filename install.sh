#!/usr/bin/env bash
# Installation für den Raspberry Pi. Für generische Server siehe README (Docker).
set -euo pipefail

cd "$(dirname "$0")"

echo "==> Systempakete"
if command -v apt >/dev/null 2>&1; then
    sudo apt update
    sudo apt install -y python3 python3-venv python3-pip python3-pyqt5 libatlas-base-dev
fi

echo "==> Python-venv"
if [ -d venv ]; then
    rm -rf venv
fi
python3 -m venv venv --system-site-packages
# shellcheck disable=SC1091
source venv/bin/activate

echo "==> Python-Abhängigkeiten (inkl. Pi-Extras)"
pip install --upgrade pip setuptools wheel
pip install -r requirements-pi.txt

echo "==> Datenbank"
./venv/bin/python -c "import src.database as d; d.init_db()"

if [ ! -f .env ]; then
    cp .env.example .env
    echo "==> .env angelegt (bitte anpassen!)"
fi

# --- Optional: USB-Backup-Skript nur mit Zustimmung installieren.
if [ "${GK_INSTALL_USB_BACKUP:-0}" = "1" ]; then
    BACKUP_SCRIPT="/usr/local/bin/gkasse_backup_to_usb.sh"
    sudo tee "$BACKUP_SCRIPT" > /dev/null <<'SCRIPT'
#!/usr/bin/env bash
# USB-Backup der Getränkekasse. Kopiert data/-Ordner auf einen Stick.
set -euo pipefail

BACKUP_SOURCE="${BACKUP_SOURCE:-/home/paul/Desktop/getraenkekasse/data}"
BACKUP_DEST="${BACKUP_DEST:-/media/paul/backup}"
LOGFILE="${LOGFILE:-/home/paul/backup.log}"

TS=$(date "+%Y-%m-%d_%H-%M")
DEST="$BACKUP_DEST/backup_$TS"

if [ ! -d "$BACKUP_DEST" ]; then
    echo "$(date) - Fehler: USB-Stick $BACKUP_DEST nicht gefunden" >> "$LOGFILE"
    exit 1
fi

mkdir -p "$DEST"
cp -a "$BACKUP_SOURCE"/. "$DEST"/
echo "$(date) - Backup nach $DEST" >> "$LOGFILE"
SCRIPT
    sudo chmod +x "$BACKUP_SCRIPT"
    CRON_ENTRY="0 3 * * * $BACKUP_SCRIPT"
    ( crontab -l 2>/dev/null | grep -Fv "$BACKUP_SCRIPT"; echo "$CRON_ENTRY" ) | crontab -
    echo "==> USB-Backup installiert ($BACKUP_SCRIPT), Cron 03:00 Uhr"
fi

echo
echo "Fertig. Start mit ./start.sh (GUI + Web-Admin) oder docker compose up -d."
