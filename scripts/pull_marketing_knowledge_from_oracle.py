"""Download MarketingKnowledgeBase/data from Oracle (canonical on-server sync)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_CONFIG_PATH = _REPO_ROOT / "oraclekeys" / "servers.json"
REMOTE_ROOT = "/home/rsadmin/bots/mirror-world"
REMOTE_FILES = [
    "MarketingKnowledgeBase/data/live_context.json",
    "MarketingKnowledgeBase/data/sync_state.json",
]


def _load_server() -> dict:
    with open(SERVER_CONFIG_PATH, "r", encoding="utf-8") as f:
        servers = json.load(f)
    if isinstance(servers, list):
        return servers[0]
    return servers


def main() -> int:
    parser = argparse.ArgumentParser(description="Pull marketing knowledge JSON from Oracle.")
    args = parser.parse_args()
    entry = _load_server()
    key = _REPO_ROOT / "oraclekeys" / entry["key"]
    if not key.exists():
        alt = _REPO_ROOT / "oracleserverkeys" / entry["key"]
        key = alt if alt.exists() else key
    host = entry["host"]
    user = entry["user"]
    local_data = _REPO_ROOT / "MarketingKnowledgeBase" / "data"
    local_data.mkdir(parents=True, exist_ok=True)
    for rel in REMOTE_FILES:
        remote = f"{REMOTE_ROOT}/{rel}"
        local = _REPO_ROOT / rel
        local.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "scp",
            "-i",
            str(key),
            "-o",
            "StrictHostKeyChecking=no",
            f"{user}@{host}:{remote}",
            str(local),
        ]
        print(f"Pulling {rel} ...")
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            print(proc.stderr or proc.stdout, file=sys.stderr)
            return proc.returncode
    print(json.dumps({"ok": True, "local_data_dir": str(local_data)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
