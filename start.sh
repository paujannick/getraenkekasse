#!/usr/bin/env bash
# Startet Web-Admin (Waitress) + Kiosk.
# Standardmäßig: Chromium im Kiosk-Modus (v3+).
# Für die alte PyQt-GUI: GK_UI=pyqt ./start.sh
set -euo pipefail
cd "$(dirname "$0")"

if [ -z "${DISPLAY:-}" ]; then
    echo "⚠ Kein DISPLAY gesetzt – Kiosk benötigt eine grafische Session (Raspberry Pi Desktop)."
    exit 1
fi

mkdir -p logs
LOGFILE="logs/log_$(date +%Y-%m-%d_%H-%M-%S).txt"
exec > >(tee -a "$LOGFILE") 2>&1
echo "Logfile: $LOGFILE"

if [ ! -d venv ] && [ ! -d .venv ]; then
    echo "⚠ Kein venv gefunden. Bitte zuerst ./install.sh ausführen."
    exit 1
fi
PY="venv/bin/python"
[ -x "$PY" ] || PY=".venv/bin/python"

# .env laden, falls vorhanden.
if [ -f .env ]; then
    set -a; . ./.env; set +a
fi

# UI-Modus wählen. GK_UI=pyqt|kiosk|off  (Default: kiosk)
UI="${GK_UI:-kiosk}"

echo "==> Starte Web-Admin (Waitress)"
"$PY" -m src.web.admin_server &
WEB_PID=$!
trap 'echo "==> Beende Web-Admin"; kill "$WEB_PID" 2>/dev/null || true' EXIT

# Warten bis Web-Admin bereit.
for _ in $(seq 1 20); do
    if curl -sSf "http://127.0.0.1:${GK_PORT:-8000}/healthz" >/dev/null 2>&1; then
        echo "Web-Admin bereit."
        break
    fi
    sleep 1
done

case "$UI" in
    off)
        echo "==> UI aus (nur Web-Admin läuft). Beenden mit Strg+C."
        wait "$WEB_PID"
        ;;
    pyqt)
        echo "==> Starte alte PyQt-GUI (Legacy)"
        "$PY" -m src.app --fullscreen
        ;;
    kiosk|*)
        # Bildschirmschoner deaktivieren (best effort).
        xset s off      >/dev/null 2>&1 || true
        xset s noblank  >/dev/null 2>&1 || true
        xset -dpms      >/dev/null 2>&1 || true

        URL="${GK_KIOSK_URL:-http://127.0.0.1:${GK_PORT:-8000}/kiosk}"

        # Chromium finden.
        CHR=""
        for cand in chromium-browser chromium google-chrome-stable google-chrome chrome; do
            if command -v "$cand" >/dev/null 2>&1; then CHR="$cand"; break; fi
        done
        if [ -z "$CHR" ]; then
            echo
            echo "⚠ Chromium nicht gefunden. Bitte installieren:"
            echo "    sudo apt install -y chromium-browser"
            echo
            echo "Alternative sofort: alte PyQt-GUI mit:"
            echo "    GK_UI=pyqt ./start.sh"
            echo
            echo "Oder nur Web-Admin ohne Kiosk-Browser:"
            echo "    GK_UI=off ./start.sh"
            echo "  -> im Browser $URL öffnen"
            exit 1
        fi

        echo "==> Starte $CHR im Kiosk-Modus → $URL"
        "$CHR" \
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
            "$URL"
        ;;
esac
