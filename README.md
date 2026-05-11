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
   Zusätzlich lässt sich unter "Einstellungen" ein Admin-PIN festlegen, der in
   der GUI als Alternative zur Admin-RFID-Karte verwendet werden kann.

   Über die Startseite lässt sich die GUI mittels "GUI aktualisieren" neu laden, falls Getränke geändert wurden.

Die GUI zeigt optional Hintergrundbilder. Über den Web-Admin unter "Einstellungen" lassen sich Bilder für Start- und Dankesseite hochladen. Die Dateien werden als `data/background.png` bzw. `data/background_thanks.png` gespeichert. Ist eine Datei nicht vorhanden, wird kein Bild angezeigt.

Die Startseite zeigt maximal neun Getränke je Seite an. Über Pfeiltasten am unteren Rand lässt sich zwischen zwei Seiten wechseln. In den Getränkeeinstellungen kann mit dem neuen Feld "Seite" festgelegt werden, auf welcher Seite ein Artikel erscheint. Unterschreitet ein Getränk seinen Mindestbestand, wird der zugehörige Button in der GUI gelb hinterlegt. Fällt der Lagerbestand unter 0, erscheint der Button deutlich rot und der Text wird ausgegraut.

Zum Aufladen von Guthaben kann im Benutzerbereich eine UID gelesen und ein Betrag angegeben werden.
Über die Einstellungen lässt sich zudem eine spezielle Aufladekarte definieren.
Wird diese Karte an der GUI erkannt, erscheint ein Menü, über das ein Betrag
in 5/10/20/50&nbsp;€ ausgewählt werden kann. Anschließend legt man die zu
aufladende Karte auf und der Betrag wird gutgeschrieben.

Im Web-Admin lassen sich jetzt sowohl Benutzer als auch Getränke bearbeiten. Für Getränke können optional Logos hochgeladen werden, die in der GUI angezeigt werden.

Beim Kauf wird der Lagerbestand des jeweiligen Getränks automatisch reduziert. Über die Getränkeübersicht im Web-Admin lassen sich Bestände bequem auffüllen.

Das Admin-Passwort lässt sich im Web-Admin über den Punkt "Passwort" ändern.


Diese Implementierung dient als Ausgangspunkt und kann nach Bedarf erweitert werden (z.B. weitere Admin-Funktionen, Export, Hardware-Anbindung des RFID-Lesers).


## Start per `start.sh`

Für den täglichen Betrieb auf dem Raspberry Pi ist `start.sh` der empfohlene Einstiegspunkt.
Das Skript:

- erstellt automatisch ein Logfile unter `logs/log_YYYY-MM-DD_HH-MM-SS.txt`,
- startet den Web-Admin (`src.web.admin_server`) im Hintergrund,
- startet anschließend die GUI im Vollbild (`src.app --fullscreen`),
- beendet den Webserver automatisch, wenn die GUI geschlossen wird.

Starten:

```bash
./start.sh
```

> Hinweis: Das Skript bricht bewusst ab, wenn keine grafische Oberfläche (`DISPLAY`) verfügbar ist.

### Autostart (Raspberry Pi Desktop / LXDE)

Damit die Kasse nach dem Booten automatisch startet, kann `start.sh` in den LXDE-Autostart eingetragen werden:

1. Datei öffnen oder anlegen:
   ```bash
   nano ~/.config/lxsession/LXDE-pi/autostart
   ```
2. Diese Zeile ergänzen:
   ```text
   @/bin/bash /home/pi/getraenkekasse/start.sh
   ```
3. Pfad bei Bedarf an deinen Installationsordner anpassen und Raspberry Pi neu starten.

Alternativ kann ein `systemd`-Service verwendet werden; wichtig ist in jedem Fall, dass `start.sh` in einer Desktop-Session mit gesetzter `DISPLAY`-Variable läuft.

## Integrierte Backup-Funktion & Cron

Das Projekt enthält bereits eine integrierte Datenbank-Backup-Funktion:

- `update.sh` erstellt vor einem Update automatisch ein Backup der Datei `data/getraenkekasse.db`.
- Die Backups werden als `data/getraenkekasse.db.bak.<timestamp>` gespeichert.
- Es werden automatisch nur die 10 neuesten Backups aufbewahrt (ältere werden gelöscht).

Manuelle Wiederherstellung eines Backups:

```bash
./restore_backup.sh data/getraenkekasse.db.bak.<timestamp>
```


### USB-Backup-Skript (wird bei Installation automatisch angelegt)

Ab sofort legt `install.sh` automatisch das Skript `/usr/local/bin/backup_to_usb.sh` an und macht es ausführbar.
Zusätzlich wird automatisch folgender Cronjob gesetzt:

```cron
0 3 * * * /usr/local/bin/backup_to_usb.sh
```

Damit wird das USB-Backup jeden Tag um **03:00 Uhr** ausgeführt.
Das Skript sichert standardmäßig von:

- Quelle: `/home/paul/Desktop/getraenkekasse/data`
- Ziel: `/media/paul/backup`
- Log: `/home/paul/backup.log`

Wenn der USB-Stick nicht gemountet ist, wird ein Fehler ins Log geschrieben und der Lauf beendet.

## Update von älteren Versionen

Um die neuen Funktionen (z.B. Auflade- und Bestandslog) ohne Datenverlust zu nutzen,
reicht es aus, das Repository zu aktualisieren und die Datenbanktabellen anzulegen.
Führe dazu einfach folgende Schritte aus:

```bash
# Im Projektordner
./update.sh
```

Das Skript holt die neuesten Dateien, installiert benötigte Pakete und ruft
`init_db()` auf. Bestehende Daten wie Benutzer, Guthaben, Bilder und Getränke
bleiben erhalten. Beim Start des Webservers werden neue Tabellen sowie neue
Spalten (z.B. das "page"-Feld für die Seitenauswahl) automatisch angelegt
und verwendet.
