#!/bin/bash

# Start-Skript für DRK Getränkekasse
# Autor: Pauls persönlicher Masterplan 😄

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

# Virtuelle Umgebung aktivieren (falls vorhanden)
if [ -d "venv" ]; then
  echo "Aktiviere virtuelle Umgebung..."
  source venv/bin/activate
else
  echo "⚠ Keine virtuelle Umgebung gefunden. Starte mit System-Python."
fi

# Anwendung starten (Vollbild-Modus)
echo "Starte Anwendung..."
python3 -m src.app --fullscreen
