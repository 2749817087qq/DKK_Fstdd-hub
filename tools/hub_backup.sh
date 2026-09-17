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
# 保留期随频率调整：daily/14天 -> hourly/3天（72 个恢复点）。
# 2026-09-18 起因：首个最小闭环跑完时，全部备份都早于任务创建时间，闭环状态无保护。
find "$BACKUP_DIR" -type f -name 'fstdd-hub-*.sqlite3' -mtime +3 -delete
# 清理孤儿 -wal/-shm：备份文件被只读打开（如审计查询）会生成这对文件，
# 上面的 -name 模式匹配不到它们，长期会残留。
find "$BACKUP_DIR" -type f -name 'fstdd-hub-*.sqlite3-wal' -mtime +3 -delete
find "$BACKUP_DIR" -type f -name 'fstdd-hub-*.sqlite3-shm' -mtime +3 -delete
