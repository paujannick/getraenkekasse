# Getränkekasse 2.0

Lokale Getränke-Abrechnung für einen DRK-Getränkestand (oder jeden anderen
Verein). Kombination aus **Touch-GUI (PyQt5)** auf dem Raspberry Pi und
einem eigenständigen **Web-Admin (Flask 3, Waitress)** samt **REST-API**,
**Self-Service-Portal** und **Telegram-Statusbot**. Persistenz mit SQLite.

Version **2.0** (2026) modernisiert das Projekt umfassend gegenüber der
Ursprungsimplementierung:

## Was ist neu in 2.0

**Sicherheit**
- Admin-Passwörter mit **Argon2id** (`argon2-cffi`); transparente Migration
  bestehender SHA-256-Hashes beim ersten Login.
- **CSRF-Schutz** aller Formulare via Flask-WTF.
- **Rate-Limiting** auf `/login` (default 5/min pro IP) via Flask-Limiter.
- Persistenter, zufälliger `SECRET_KEY` in `data/flask_secret.key` (0600) –
  überschreibbar per `FLASK_SECRET_KEY`.
- Sichere Session-Cookies (`HttpOnly`, `SameSite=Lax`, optional `Secure`).
- Content-Security-Policy und weitere Security-Header out-of-the-box.
- Sichere Datei-Uploads (`secure_filename`, MIME-Whitelist, zufälliger
  Dateiname, konfigurierbares Größenlimit).
- Erzwungener Warnhinweis, solange das Standardpasswort aktiv ist.
- POST-only für alle destruktiven Aktionen (Lösch-Links entfernt).

**Betrieb**
- **Waitress** als Produktions-WSGI (nicht mehr Flask-Devserver).
- **Dockerfile** + **docker-compose.yml** mit Health-Check.
- **GitHub Actions**: Ruff-Lint, mypy, pytest, Docker-Build.
- Strukturiertes **JSON-Logging** mit Rotation (`GK_LOG_JSON`, `GK_LOG_LEVEL`).
- `/healthz`-Endpunkt für Docker/Kubernetes/systemd.
- Zentrale Konfiguration per `.env` (`python-dotenv`).

**Neue Funktionen**
- **REST-API v1** (`/api/v1`) mit Bearer-Token-Auth. Endpunkte für Getränke,
  Nutzerabfragen, Aufladen und Kauf. Tokens werden Argon2-gehasht gespeichert.
- **User-Self-Service** (`/me/<token>`): mobiler Blick auf Guthaben, letzte
  Käufe und Aufladungen via signiertem QR-Code (itsdangerous).
- **Happy-Hour / Rabatte** (`/discounts`): zeit-, wochentag- und
  getränkespezifische Rabatte.
- **Backups 2.0** (`/backups`): konsistenter SQLite-Snapshot via
  `VACUUM INTO`, gzip-Kompression, SHA-256, Rotation nach Anzahl und Alter.
  Optionaler Upload auf WebDAV (Nextcloud).
- **Audit-Log** (`/audit`) für alle Admin-Aktionen (auch API-Aufrufe).
- Neuer Menüpunkt „System“ bündelt Einstellungen, Backups, API-Tokens und
  Audit-Log.

**Codequalität**
- `pyproject.toml`, `requirements-{dev,pi}.txt` sauber getrennt.
- Ruff- und mypy-Konfiguration.
- Modularisierung: `src/security.py`, `src/logging_setup.py`, `src/audit.py`,
  `src/discounts.py`, `src/backups.py`, `src/api/`.
- Getestet auf Python 3.11–3.14 (Windows/Linux).

## Projektstruktur

```
src/
  admin_auth.py     Passwörter (Argon2 + Legacy)
  audit.py          Audit-Log
  backups.py        Backups (gzip + SHA-256 + optional WebDAV)
  database.py       SQLite-Schema, Config, Refresh/Exit-Flags
  discounts.py      Happy-Hour-Engine
  logging_setup.py  JSON-Logging mit Rotation
  models.py         Domänen-Logik (Bestand, Guthaben, Statistiken)
  rfid.py           MFRC522 (nur Pi)
  security.py       SECRET_KEY, Session-Härtung, Upload-Sanitisation
  telegram_bot.py   Statusbot
  app.py            GUI-Einstiegspunkt
  gui/              PyQt-Fenster (main, admin)
  web/              Flask-Admin (Templates + Server)
  api/              REST-API v1, Tokens, Self-Service
tests/              pytest-Suite (grün auf Windows + CI)
```

## Schnellstart – Docker (empfohlen)

```bash
cp .env.example .env
# .env anpassen (mindestens FLASK_SECRET_KEY setzen)
docker compose up -d
```

Der Web-Admin ist danach unter `http://<host>:8000` erreichbar
(User `admin`, Passwort `admin` – **sofort ändern!**).

## Schnellstart – Raspberry Pi (GUI + Kasse)

```bash
./install.sh          # legt venv + Datenbank + .env an
./start.sh            # GUI + Web-Admin
```

Optionales USB-Backup (früherer Auto-Cron) wird nur mit Opt-in installiert:

```bash
GK_INSTALL_USB_BACKUP=1 ./install.sh
```

## Erste Schritte

1. Web-Admin öffnen → Passwort ändern (der rote Sicherheitsbanner
   verschwindet dann).
2. Unter „System → API-Tokens“ Tokens für externe Systeme anlegen.
3. Unter „System → Backups“ ein initiales Backup erstellen und den Pfad in
   `GK_BACKUP_DIR` konfigurieren (Default: `data/backups/`).
4. Für Telegram-Statusreports: „Telegram“ öffnen, Token + Chat-ID
   hinterlegen.

## REST-API

Alle Endpunkte unter `/api/v1`. Auth via `Authorization: Bearer <token>`.

| Method | Pfad                        | Scope   | Beschreibung                          |
| ------ | --------------------------- | ------- | ------------------------------------- |
| GET    | `/health`                   | public  | Liveness                              |
| GET    | `/drinks`                   | read    | Alle Getränke inkl. effektivem Preis  |
| GET    | `/users/by-uid/<uid>`       | read    | Nutzerdaten für RFID-UID              |
| GET    | `/users/<uid>/balance`      | read    | Nur Guthaben                           |
| POST   | `/topup`                    | write   | Guthaben aufladen                     |
| POST   | `/purchase`                 | write   | Kauf buchen (inkl. Bestand)           |

Beispiel:

```bash
curl -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"uid":"TESTCARD123","amount_cents":500}' \
     https://kasse.example.com/api/v1/topup
```

## Self-Service

Für jeden Nutzer mit RFID-UID lässt sich in der Benutzerübersicht ein
QR-Code generieren (`/users/qr/<user_id>`). Der QR verweist auf einen
signierten Token unter `/me/<token>` (7 Tage gültig, invalidierbar durch
Wechseln des `FLASK_SECRET_KEY`).

## Backups & Restore

- Web-Admin: „System → Backups → Jetzt Backup anlegen“.
- CLI:
  ```bash
  python3 -c "from src import backups; backups.create_backup()"
  ./restore_backup.sh data/backups/gkasse_20260901_120000.db.gz
  ```
- Konfiguration:
  - `GK_BACKUP_DIR` – Zielverzeichnis (Default `data/backups`)
  - `GK_BACKUP_KEEP` – wieviele Backups behalten (Default 20)
  - `GK_BACKUP_WEBDAV_URL/USER/PASS` – optionaler Upload nach jedem Backup

## Umgebungsvariablen

Siehe `.env.example`. Wichtige Werte:

| Variable              | Default        | Beschreibung                            |
| --------------------- | -------------- | --------------------------------------- |
| `FLASK_SECRET_KEY`    | (auto)         | Session-Signatur; sonst wird eine Datei angelegt |
| `GK_ADMIN_USER`       | `admin`        | Anmeldename                              |
| `GK_HOST`/`GK_PORT`   | `0.0.0.0/8000` | Bind-Adresse                             |
| `GK_SESSION_SECURE`   | `false`        | `true` hinter HTTPS setzen               |
| `GK_RATE_LIMIT_LOGIN` | `5/minute`     | Rate-Limit für Login                     |
| `GK_MAX_UPLOAD_MB`    | `5`            | Upload-Größenlimit                       |
| `GK_LOG_JSON`         | `true`         | JSON- vs. Textlog                        |
| `GK_DEV`              | `0`            | `1` = Flask-Devserver statt Waitress     |

## Migration von 1.x

`./update.sh` erledigt Pull + Backup + Requirements + Schema-Migration.
Bestehende Nutzer, Getränke, Guthaben, Bilder und alten `admin_pw.txt`
bleiben erhalten – das SHA-256-Passwort wird beim ersten Login transparent
auf Argon2 umgestellt.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

## Lizenz

MIT – siehe `pyproject.toml`.
