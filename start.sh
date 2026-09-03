#!/usr/bin/env bash
# Start-Skript: Web-Admin (Waitress) + PyQt-GUI.
set -euo pipefail
cd "$(dirname "$0")"

if [ -z "${DISPLAY:-}" ]; then
    echo "⚠ Kein DISPLAY gesetzt – GUI benötigt eine grafische Session (Raspberry Pi Desktop)."
    exit 1
fi

mkdir -p logs
LOGFILE="logs/log_$(date +%Y-%m-%d_%H-%M-%S).txt"
exec > >(tee -a "$LOGFILE") 2>&1
echo "Logfile: $LOGFILE"

if [ ! -d venv ]; then
    echo "⚠ Kein venv gefunden. Bitte zuerst ./install.sh ausführen."
    exit 1
fi

# .env laden, falls vorhanden.
if [ -f .env ]; then
    set -a; . ./.env; set +a
fi

echo "==> Starte Web-Admin (Waitress)"
venv/bin/python -m src.web.admin_server &
WEB_PID=$!
trap 'echo "==> Beende Web-Admin"; kill "$WEB_PID" 2>/dev/null || true' EXIT

echo "==> Starte GUI im Vollbild"
venv/bin/python -m src.app --fullscreen
