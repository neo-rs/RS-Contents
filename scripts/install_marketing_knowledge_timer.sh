#!/bin/bash
# Enable marketing knowledge sync timer on Oracle.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

for unit in mirror-world-marketing-knowledge-sync.service mirror-world-marketing-knowledge-sync.timer \
  mirror-world-marketing-daily-post.service mirror-world-marketing-daily-post.timer; do
  src="$ROOT_DIR/systemd/$unit"
  if [ ! -f "$src" ]; then
    echo "ERROR: missing unit file: $src"
    exit 1
  fi
  sudo cp -f "$src" "/etc/systemd/system/$unit"
done

sudo systemctl daemon-reload
sudo systemctl enable mirror-world-marketing-knowledge-sync.timer
sudo systemctl start mirror-world-marketing-knowledge-sync.timer
sudo systemctl start mirror-world-marketing-knowledge-sync.service || true

echo "Timer status:"
systemctl status mirror-world-marketing-knowledge-sync.timer --no-pager || true
sudo systemctl enable mirror-world-marketing-daily-post.timer
sudo systemctl start mirror-world-marketing-daily-post.timer

echo "Daily post timer:"
systemctl list-timers mirror-world-marketing-daily-post.timer --no-pager || true
