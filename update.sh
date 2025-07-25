#!/bin/bash
set -e

# Always operate relative to script location
cd "$(dirname "$0")"

# Pull latest changes if repository has remote
if git config --get remote.origin.url > /dev/null 2>&1; then
  git pull --ff-only
fi

# Backup existing database
DB_PATH="data/getraenkekasse.db"
if [ -f "$DB_PATH" ]; then
  cp "$DB_PATH" "$DB_PATH.bak.$(date +%s)"
fi

# Create venv if missing and install requirements
if [ ! -d venv ]; then
  python3 -m venv venv --system-site-packages
fi
source venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install --upgrade -r requirements.txt

# Create any new database tables without touching existing data
venv/bin/python - <<'PY'
import src.database as d
conn = d.get_connection()
d.init_db(conn)
cur = conn.execute("PRAGMA table_info(drinks)")
cols = [r[1] for r in cur.fetchall()]
if 'min_stock' not in cols:
    conn.execute("ALTER TABLE drinks ADD COLUMN min_stock INTEGER NOT NULL DEFAULT 0")
if 'page' not in cols:
    conn.execute("ALTER TABLE drinks ADD COLUMN page INTEGER NOT NULL DEFAULT 1")
cur = conn.execute("SELECT COUNT(*) FROM config WHERE key='admin_pin'")
if cur.fetchone()[0] == 0:
    conn.execute("INSERT INTO config(key, value) VALUES ('admin_pin', '1234')")
conn.commit()
conn.close()
PY

echo "Update completed"
