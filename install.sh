#!/bin/bash
set -e

cd "$(dirname "$0")"

sudo apt install -y python3-pyqt5

# create venv and install requirements
if [ -d venv ]; then
    rm -rf venv
fi

python3 -m venv venv --system-site-packages

source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

./venv/bin/python -c "import src.database as d; d.init_db()"

# Install USB backup script
BACKUP_SCRIPT="/usr/local/bin/backup_to_usb.sh"
sudo tee "$BACKUP_SCRIPT" > /dev/null <<'SCRIPT'
#!/bin/bash

# ==== EINSTELLUNGEN ====
BACKUP_SOURCE="/home/paul/Desktop/getraenkekasse/data"
BACKUP_DEST="/media/paul/backup"
LOGFILE="/home/paul/backup.log"
TIMESTAMP=$(date "+%Y-%m-%d_%H-%M")
DEST="$BACKUP_DEST/backup_$TIMESTAMP"

# ==== STICK GEMOUNTET? ====
if [ ! -d "$BACKUP_DEST" ]; then
    echo "$(date) - ❌ Fehler: USB-Stick $BACKUP_DEST nicht gefunden!" >> "$LOGFILE"
    exit 1
fi

# ==== BACKUP START ====
mkdir -p "$DEST"
cp -r "$BACKUP_SOURCE"/* "$DEST"

if [ $? -eq 0 ]; then
    echo "$(date) - ✅ Backup erfolgreich nach $DEST" >> "$LOGFILE"
else
    echo "$(date) - ⚠️ Fehler beim Backup-Vorgang!" >> "$LOGFILE"
    exit 1
fi
SCRIPT
sudo chmod +x "$BACKUP_SCRIPT"

# Install cron job if missing
CRON_ENTRY='0 3 * * * /usr/local/bin/backup_to_usb.sh'
( crontab -l 2>/dev/null | grep -Fv "$CRON_ENTRY"; echo "$CRON_ENTRY" ) | crontab -

echo "Installation abgeschlossen"
echo "Backup-Skript installiert: $BACKUP_SCRIPT"
echo "Cronjob gesetzt: $CRON_ENTRY"
