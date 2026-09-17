#!/usr/bin/env bash
# Deploy the internal FSTDD coordination hub on loopback port 8788.
# The script is idempotent and follows deploy_inbox_server.sh's verified
# pattern: upload, checksum, replace unit, enable/restart, then verify state.
set -euo pipefail

HOST="${FSTDD_SSH_HOST:-ubuntu@43.134.236.80}"
KEY="${FSTDD_SSH_KEY:-/d/id_ed25519}"
PORT="${FSTDD_HUB_PORT:-8788}"
REMOTE_DIR="${FSTDD_HUB_REMOTE_DIR:-/home/ubuntu/fstdd-hub-server}"
DB_PATH="${FSTDD_HUB_DB:-/home/ubuntu/fstdd-hub/fstdd-hub.sqlite3}"
SERVICE="fstdd-hub"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$REPO_ROOT/tools/fstdd_hub.py"
[ -f "$SRC" ] || { echo "[FAIL] missing $SRC"; exit 1; }

SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=20 "$HOST")
REMOTE_PY="$(${SSH[@]} 'command -v python3')"
[ -n "$REMOTE_PY" ] || { echo "[FAIL] remote python3 not found"; exit 1; }

REMOTE_MD5_DIR="$(dirname "$DB_PATH")"
"${SSH[@]}" "mkdir -p '$REMOTE_DIR' '$REMOTE_MD5_DIR'"
scp -i "$KEY" -o StrictHostKeyChecking=no -q "$SRC" "$HOST:$REMOTE_DIR/fstdd_hub.py"
LOCAL_MD5="$(md5sum "$SRC" | cut -d' ' -f1)"
REMOTE_MD5="$("${SSH[@]}" "md5sum '$REMOTE_DIR/fstdd_hub.py' | cut -d' ' -f1")"
echo "local md5:  $LOCAL_MD5"
echo "remote md5: $REMOTE_MD5"
[ "$LOCAL_MD5" = "$REMOTE_MD5" ] || { echo "[FAIL] checksum mismatch"; exit 1; }

"${SSH[@]}" "sudo tee /etc/systemd/system/$SERVICE.service >/dev/null" <<UNIT
[Unit]
Description=FSTDD internal coordination hub
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=$REMOTE_DIR
ExecStart=$REMOTE_PY $REMOTE_DIR/fstdd_hub.py --host 127.0.0.1 --port $PORT --db $DB_PATH
Restart=always
RestartSec=5
StandardOutput=append:$REMOTE_MD5_DIR/server.log
StandardError=append:$REMOTE_MD5_DIR/server.log

[Install]
WantedBy=multi-user.target
UNIT

"${SSH[@]}" "set -e
OLD=\$(sudo lsof -t -i:$PORT 2>/dev/null || true)
if [ -n \"\$OLD\" ]; then sudo kill \$OLD || true; fi
sudo systemctl daemon-reload
sudo systemctl enable --now $SERVICE >/dev/null 2>&1
sudo systemctl restart $SERVICE
sleep 1
test \"\$(systemctl is-active $SERVICE)\" = active
LISTEN=\$(ss -lntp | grep '127.0.0.1:$PORT' || true)
test -n \"\$LISTEN\"
HEALTH=\$(curl -fsS --max-time 10 http://127.0.0.1:$PORT/health)
echo \"service=\$(systemctl is-active $SERVICE)\"
echo \"listen=\$LISTEN\"
echo \"health=\$HEALTH\"
"

echo "[OK] $SERVICE deployed on 127.0.0.1:$PORT"
