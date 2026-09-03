#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ -z "${1:-}" ]; then
    echo "Usage: $0 <backup-file (.db oder .db.gz)>"
    exit 1
fi

python3 - "$1" <<'PY'
import sys
from pathlib import Path
from src import backups
backups.restore_backup(Path(sys.argv[1]))
print("Restore erfolgreich")
PY
