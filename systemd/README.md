# systemd-Services für Raspberry Pi

Zwei Unit-Files: **Web-Admin** als System-Service, **Chromium-Kiosk** als
User-Service (Grafik erforderlich).

## Web-Admin installieren

```bash
sudo cp systemd/getraenkekasse-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now getraenkekasse-web
sudo systemctl status getraenkekasse-web
```

Der Web-Admin läuft danach auch ohne Login und wird nach Absturz automatisch
neu gestartet.

## Kiosk (Chromium) installieren

```bash
mkdir -p ~/.config/systemd/user
cp systemd/getraenkekasse-kiosk.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now getraenkekasse-kiosk

# Damit der User-Service auch ohne interaktives Login startet:
sudo loginctl enable-linger paul
```

## Update ohne Datenverlust

Über den Web-Admin: **System → Update → Update installieren**. Das Skript
legt vor Änderungen automatisch ein Backup an. Falls etwas schiefgeht:

```bash
./restore_backup.sh data/backups/gkasse_<timestamp>.db.gz
sudo systemctl restart getraenkekasse-web
```
