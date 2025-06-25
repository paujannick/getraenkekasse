# Getränkeabrechnungssystem

Dies ist eine Beispielimplementierung einer lokalen Getränkekasse für einen DRK-Getränkestand.
Die Anwendung nutzt Python 3 und PyQt5 und speichert alle Daten lokal in einer SQLite-Datenbank.

Die Anwendung setzt einen RFID-Leser voraus. Beim Kaufvorgang erscheint auf dem
Touchdisplay ein Hinweis "Bitte Karte auflegen…" und die UID wird automatisch
über den Leser erfasst. Die RFID-Funktion wurde überarbeitet und liest die UID
zuverlässiger ohne AUTH-Fehler.

## Struktur

- `src/` enthält die Python-Module
- `src/gui/` umfasst die PyQt-GUI

- `src/web/` bietet ein einfaches Web-Admin-Interface

- `data/` enthält die SQLite-Datenbank und Ressourcen (z.B. Bilder)

## Erste Schritte


1. Installation im virtuellen Umfeld:
   ```bash
   ./install.sh
   ```
   Das Script legt ein `venv`-Verzeichnis an (bestehendes wird überschrieben) und installiert alle Abhängigkeiten.
2. Datenbank initialisieren (legt automatisch einige Beispiel-Daten an):
   ```bash
   ./venv/bin/python -c "import src.database as d; d.init_db()"
   ```
3. Anwendung starten (z.B. im Vollbild auf dem Raspberry Pi):
   ```bash
   ./venv/bin/python -m src.app --fullscreen
   ```
4. Web-Admin starten (optional):
   ```bash
   ./venv/bin/python -m src.web.admin_server
   ```
   Danach im Browser `http://<RaspberryPi>:8000` öffnen und mit `admin/admin` anmelden.
   Das Passwort kann im Web-Admin unter "Passwort" geändert werden. Es wird
   verschlüsselt in `data/admin_pw.txt` gespeichert.

   Über die Startseite lässt sich die GUI mittels "GUI aktualisieren" neu laden, falls Getränke geändert wurden.

   Die GUI zeigt optional ein DRK-Logo im Hintergrund an. Lege dazu eine Bilddatei unter `data/background.png` ab. Ist diese Datei nicht vorhanden, wird kein Hintergrundbild angezeigt.

Zum Aufladen von Guthaben kann im Benutzerbereich eine UID gelesen und ein Betrag angegeben werden.

Im Web-Admin lassen sich jetzt sowohl Benutzer als auch Getränke bearbeiten. Für Getränke können optional Logos hochgeladen werden, die in der GUI angezeigt werden.

Beim Kauf wird der Lagerbestand des jeweiligen Getränks automatisch reduziert. Über die Getränkeübersicht im Web-Admin lassen sich Bestände bequem auffüllen.
Das Admin-Passwort lässt sich im Web-Admin über den Punkt "Passwort" ändern.


Diese Implementierung dient als Ausgangspunkt und kann nach Bedarf erweitert werden (z.B. weitere Admin-Funktionen, Export, Hardware-Anbindung des RFID-Lesers).
