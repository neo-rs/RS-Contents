#!/bin/bash
# Enable marketing knowledge sync timer on Oracle.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

for unit in mirror-world-marketing-knowledge-sync.service mirror-world-marketing-knowledge-sync.timer \
  mirror-world-marketing-daily-post.service mirror-world-marketing-daily-post.timer \
  mirror-world-marketing-review-agent.service mirror-world-reesebot.service; do
  src="$ROOT_DIR/systemd/$unit"
  if [ ! -f "$src" ]; then
    echo "ERROR: missing unit file: $src"
    exit 1
  fi
  sudo cp -f "$src" "/etc/systemd/system/$unit"
done

sudo systemctl daemon-reload
sudo systemctl enable --now mirror-world-marketing-knowledge-sync.timer

echo "Timer status:"
systemctl is-enabled mirror-world-marketing-knowledge-sync.timer || true
systemctl is-active mirror-world-marketing-knowledge-sync.timer || true
sudo systemctl enable --now mirror-world-marketing-daily-post.timer

echo "Daily post timer:"
systemctl list-timers mirror-world-marketing-daily-post.timer --no-pager || true

echo "Review agent service file installed; RSAdminBot remains the admin/review-controls bridge."
systemctl is-enabled mirror-world-marketing-review-agent.service || true
systemctl is-active mirror-world-marketing-review-agent.service || true

sudo systemctl enable --now mirror-world-reesebot.service
echo "ReeseBot service:"
systemctl is-enabled mirror-world-reesebot.service || true
systemctl is-active mirror-world-reesebot.service || true
