#!/bin/bash
set -e

# Pull latest changes if repository has remote
if git rev-parse --git-dir > /dev/null 2>&1; then
  git pull
fi

# Create venv if missing and install requirements
if [ ! -d venv ]; then
  python3 -m venv venv --system-site-packages
fi
source venv/bin/activate
pip install -r requirements.txt

# Create any new database tables without touching existing data
venv/bin/python - <<'PY'
import src.database as d
conn = d.get_connection()
d.init_db(conn)
conn.close()
PY

echo "Update completed"
