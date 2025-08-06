#!/bin/bash
set -e

cd "$(dirname "$0")"

if [ -z "$1" ]; then
  echo "Usage: $0 <backup-file>" >&2
  exit 1
fi

python3 - "$1" <<'PY'
import sys
from pathlib import Path
import src.database as d

backup = Path(sys.argv[1])
d.restore_database(backup)
print(f"Restored database from {backup}")
PY

