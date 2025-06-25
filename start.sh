#!/bin/bash

echo "----------------------------------------"
echo "Starte Getränkekassen-System..."
echo "----------------------------------------"

# Prüfen, ob wir uns innerhalb einer SSH-Session befinden (kein X11 verfügbar)
if [ -z "$DISPLAY" ]; then
  echo "⚠ Kein Display gefunden (DISPLAY-Variable leer)"
  echo "Bitte sicherstellen, dass du auf dem Raspberry Pi Desktop arbeitest."
  echo "Abbruch."
  exit 1
fi

# Virtuelle Umgebung aktivieren (nur für den PATH, optional)
if [ -d "venv" ]; then
  echo "Nutze virtuelle Umgebung (direkter Aufruf)..."
else
  echo "⚠ Keine virtuelle Umgebung gefunden. Starte mit System-Python."
fi

# Erstelle Logfile mit Zeitstempel
LOGDIR="logs"
mkdir -p "$LOGDIR"
LOGFILE="$LOGDIR/log_$(date +%Y-%m-%d_%H-%M-%S).txt"

echo "Logfile: $LOGFILE"
echo "----------------------------------------"

# Starte Webserver
echo "Starte Webserver..."
venv/bin/python -m src.web.admin_server 2>&1 | tee -a "$LOGFILE" &
WEB_PID=$!

# Webserver bei Skriptende beenden
trap "echo 'Beende Webserver...'; kill $WEB_PID" EXIT

# Starte Anwendung und schreibe alles ins Log
echo "Starte Anwendung..."
venv/bin/python -m src.app 2>&1 | tee -a "$LOGFILE"
APP_EXITCODE=$?

# Nach Programmende:
echo "----------------------------------------"
if [ $APP_EXITCODE -ne 0 ]; then
  echo "⚠ Anwendung mit Fehlercode $APP_EXITCODE beendet!"
else
  echo "✅ Anwendung sauber beendet."
fi

echo "----------------------------------------"
echo "Logfile gespeichert unter: $LOGFILE"
echo
echo "Drücke [Enter] zum Schließen..."
read
