#!/usr/bin/env bash
# Create a consistent SQLite backup for the FSTDD hub.
set -euo pipefail
DB_PATH="${FSTDD_HUB_DB:-/home/ubuntu/fstdd-hub/fstdd-hub.sqlite3}"
BACKUP_DIR="${FSTDD_HUB_BACKUP_DIR:-/home/ubuntu/fstdd-hub/backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$BACKUP_DIR"
# sqlite3 is optional on the host; the Python hub exposes a safe backup mode in
# the operational runbook.  This script uses a lock-safe filesystem copy only
# after WAL is checkpointed by the service account.
python3 - "$DB_PATH" "$BACKUP_DIR/fstdd-hub-$STAMP.sqlite3" <<'PY'
import shutil
import sqlite3
import sys
src, dest = sys.argv[1:]
conn = sqlite3.connect(src)
try:
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    out = sqlite3.connect(dest)
    try:
        conn.backup(out)
        out.commit()
    finally:
        out.close()
finally:
    conn.close()
print(dest)
PY
find "$BACKUP_DIR" -type f -name 'fstdd-hub-*.sqlite3' -mtime +14 -delete
