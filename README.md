# Getränkeabrechnungssystem

Dies ist eine Beispielimplementierung einer lokalen Getränkekasse für einen DRK-Getränkestand.
Die Anwendung nutzt Python 3 und PyQt5 und speichert alle Daten lokal in einer SQLite-Datenbank.

Diese Variante dient zu Testzwecken und kann problemlos auf einem Mac ohne angeschlossenen RFID-Reader ausgeführt werden.
Beim Kaufvorgang erscheint ein Dialog, in dem die UID der Karte manuell eingegeben wird.

## Struktur

- `src/` enthält die Python-Module
- `src/gui/` umfasst die PyQt-GUI
- `src/web/` bietet ein einfaches Web-Admin-Interface
- `data/` enthält die SQLite-Datenbank und Ressourcen (z.B. Bilder)

## Erste Schritte

1. Abhängigkeiten installieren:
   ```bash
   pip install -r requirements.txt
   ```
2. Datenbank initialisieren (legt automatisch einige Beispiel-Daten an):
   ```bash
   python -c "import src.database as d; d.init_db()"
   ```
3. Anwendung starten:
   ```bash
   python -m src.app
   ```
4. Web-Admin starten (optional):
   ```bash
   python -m src.web.admin_server
   ```
   Danach im Browser `http://<RaspberryPi>:8000` öffnen und mit `admin/admin` anmelden.
   Über die Startseite lässt sich die GUI mittels "GUI aktualisieren" neu laden, falls Getränke geändert wurden.

Zum Aufladen von Guthaben kann im Benutzerbereich eine UID gelesen und ein Betrag angegeben werden.

Diese Implementierung dient als Ausgangspunkt und kann nach Bedarf erweitert werden (z.B. weitere Admin-Funktionen, Export, Hardware-Anbindung des RFID-Lesers).
