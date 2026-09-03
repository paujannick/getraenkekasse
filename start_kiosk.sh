#!/usr/bin/env bash
# Startet Web-Admin im Hintergrund und öffnet Chromium im Kiosk-Modus.
# Für den Raspberry Pi gedacht (X11-Session).
set -euo pipefail
cd "$(dirname "$0")"

if [ -z "${DISPLAY:-}" ]; then
    echo "⚠ DISPLAY nicht gesetzt – Kiosk benötigt eine grafische Session." >&2
    exit 1
fi

# .env laden, wenn vorhanden.
if [ -f .env ]; then
    set -a; . ./.env; set +a
fi

mkdir -p logs
LOGFILE="logs/kiosk_$(date +%Y-%m-%d_%H-%M-%S).txt"
exec > >(tee -a "$LOGFILE") 2>&1
echo "Logfile: $LOGFILE"

# Bildschirmschoner deaktivieren (best effort).
xset s off || true
xset s noblank || true
xset -dpms || true

# Web-Admin starten.
if [ -d venv ]; then
    PY=venv/bin/python
elif [ -d .venv ]; then
    PY=.venv/bin/python
else
    PY=python3
fi

echo "==> Starte Web-Admin"
"$PY" -m src.web.admin_server &
WEB_PID=$!
trap 'echo "==> Beende Web-Admin"; kill $WEB_PID 2>/dev/null || true' EXIT

# Auf /healthz warten (max. 20 s).
for i in $(seq 1 20); do
    if curl -sSf http://127.0.0.1:${GK_PORT:-8000}/healthz >/dev/null 2>&1; then
        echo "Web-Admin bereit."
        break
    fi
    sleep 1
done

# Chromium-Kiosk-Argumente.
KIOSK_URL="${GK_KIOSK_URL:-http://127.0.0.1:${GK_PORT:-8000}/kiosk}"
CHROMIUM_BIN="${GK_CHROMIUM:-chromium-browser}"
command -v "$CHROMIUM_BIN" >/dev/null || CHROMIUM_BIN="chromium"
command -v "$CHROMIUM_BIN" >/dev/null || { echo "⚠ Chromium nicht gefunden."; exit 1; }

echo "==> Starte $CHROMIUM_BIN im Kiosk-Modus → $KIOSK_URL"
"$CHROMIUM_BIN" \
    --kiosk \
    --noerrdialogs \
    --disable-features=TranslateUI \
    --disable-restore-session-state \
    --disable-session-crashed-bubble \
    --disable-infobars \
    --disable-pinch \
    --overscroll-history-navigation=0 \
    --incognito \
    --window-position=0,0 \
    --check-for-update-interval=31536000 \
    --user-data-dir="${HOME}/.gkasse_kiosk_profile" \
    "$KIOSK_URL"
