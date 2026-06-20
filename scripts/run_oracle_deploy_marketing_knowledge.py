#!/usr/bin/env python3
"""Deploy MarketingKnowledgeBase to Oracle (code sync + timer + first sync).

Typical workflow after code changes:
  1) push_rsbots_py_only.bat
  2) update_marketing_knowledge.bat

One-shot from local without git push:
  py -3 scripts/run_oracle_deploy_marketing_knowledge.py --from-local
"""
from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mirror_world_config import load_oracle_servers, pick_oracle_server, resolve_oracle_ssh_key_path  # noqa: E402

SYNC_PATHS = [
    "MarketingKnowledgeBase",
    "systemd/mirror-world-marketing-knowledge-sync.service",
    "systemd/mirror-world-marketing-knowledge-sync.timer",
    "systemd/mirror-world-marketing-daily-post.service",
    "systemd/mirror-world-marketing-daily-post.timer",
    "systemd/mirror-world-reesebot.service",
    "scripts/install_marketing_knowledge_timer.sh",
    "scripts/pull_marketing_knowledge_from_oracle.py",
]
SKIP_DIR_NAMES = {".venv", "__pycache__", "node_modules", ".git"}
SKIP_FILE_SUFFIXES = (".pyc",)
SKIP_REL_PATHS = {
    "MarketingKnowledgeBase/data/live_context.json",
    "MarketingKnowledgeBase/data/sync_state.json",
}


def _ssh(entry: dict, cmd: str, *, timeout: int = 600) -> subprocess.CompletedProcess:
    key = str(resolve_oracle_ssh_key_path(str(entry.get("key", "")), REPO_ROOT))
    args = ["ssh", "-i", key, "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes"]
    opts = str(entry.get("ssh_options", "") or "").strip()
    if opts:
        args.extend(shlex.split(opts))
    args.append(f'{entry["user"]}@{entry["host"]}')
    args.extend(["bash", "-lc", cmd])
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def _scp(entry: dict, local: Path, remote_path: str, *, timeout: int = 180) -> subprocess.CompletedProcess:
    key = str(resolve_oracle_ssh_key_path(str(entry.get("key", "")), REPO_ROOT))
    args = ["scp", "-i", key, "-o", "StrictHostKeyChecking=no"]
    opts = str(entry.get("ssh_options", "") or "").strip()
    if opts:
        args.extend(shlex.split(opts))
    args.extend([str(local), f'{entry["user"]}@{entry["host"]}:{remote_path}'])
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def _build_local_tar() -> Path:
    tmp = tempfile.NamedTemporaryFile(prefix="marketing_kb_", suffix=".tar.gz", delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()

    def _should_skip(rel_posix: str) -> bool:
        rel_norm = rel_posix.replace("\\", "/")
        if rel_norm.startswith("MarketingKnowledgeBase/data/"):
            return True
        if rel_norm in SKIP_REL_PATHS:
            return True
        parts = Path(rel_norm).parts
        if any(part in SKIP_DIR_NAMES for part in parts):
            return True
        return rel_norm.endswith(SKIP_FILE_SUFFIXES)

    with tarfile.open(tmp_path, "w:gz") as tar:
        for rel in SYNC_PATHS:
            src = REPO_ROOT / rel
            if not src.exists():
                continue
            if src.is_file():
                if not _should_skip(rel.replace("\\", "/")):
                    tar.add(src, arcname=rel.replace("\\", "/"))
                continue
            for path in src.rglob("*"):
                if path.is_dir():
                    continue
                rel_posix = path.relative_to(REPO_ROOT).as_posix()
                if _should_skip(rel_posix):
                    continue
                tar.add(path, arcname=rel_posix)
    return tmp_path


def _remote_sync_from_rsbots(remote_root: str) -> str:
    code_root = "/home/rsadmin/bots/rsbots-code"
    paths_q = " ".join(shlex.quote(p) for p in SYNC_PATHS)
    return f"""
set -euo pipefail
CODE_ROOT={shlex.quote(code_root)}
LIVE_ROOT={shlex.quote(remote_root)}

if [ ! -d "$CODE_ROOT/.git" ]; then
  echo "ERR=missing_rsbots_code_root"
  exit 2
fi

cd "$CODE_ROOT"
git fetch origin
git pull --ff-only origin main

TMP_LIST="/tmp/marketing_kb_sync.txt"
: > "$TMP_LIST"
for p in {paths_q}; do
  if [ -f "$p" ]; then
    echo "$p" >> "$TMP_LIST"
  elif [ -d "$p" ]; then
    git ls-files "$p" >> "$TMP_LIST" || true
  fi
done
grep -v -E "(^|/)data/live_context\\.json$" "$TMP_LIST" > "$TMP_LIST.ex" 2>/dev/null && mv "$TMP_LIST.ex" "$TMP_LIST" || true
grep -v -E "(^|/)data/sync_state\\.json$" "$TMP_LIST" > "$TMP_LIST.ex" 2>/dev/null && mv "$TMP_LIST.ex" "$TMP_LIST" || true
grep -v -E "^MarketingKnowledgeBase/data/" "$TMP_LIST" > "$TMP_LIST.ex" 2>/dev/null && mv "$TMP_LIST.ex" "$TMP_LIST" || true
sort -u "$TMP_LIST" -o "$TMP_LIST"

COUNT="$(wc -l < "$TMP_LIST" | tr -d ' ')"
if [ "$COUNT" = "0" ]; then
  echo "ERR=no_tracked_files"
  exit 3
fi

mkdir -p "$LIVE_ROOT/MarketingKnowledgeBase/data"
env -u TAR_OPTIONS /bin/tar -cf - -T "$TMP_LIST" | (cd "$LIVE_ROOT" && env -u TAR_OPTIONS /bin/tar -xf - --overwrite --no-same-owner --no-same-permissions)
echo "OK=sync_from_rsbots SYNC_COUNT=$COUNT"
"""


def _remote_post_install(remote_root: str, *, skip_sync_run: bool) -> str:
    sync_cmd = ""
    if not skip_sync_run:
        sync_cmd = f"""
echo "Running marketing knowledge Discord sync..."
"$PY" -m MarketingKnowledgeBase.sync --quiet
"""
    return f"""
set -euo pipefail
LIVE_ROOT={shlex.quote(remote_root)}
cd "$LIVE_ROOT"

PY="$LIVE_ROOT/.venv/bin/python"
if [ ! -x "$PY" ]; then
  PY="$LIVE_ROOT/venv/bin/python"
fi
if [ ! -x "$PY" ]; then
  PY="$(command -v python3)"
fi

chmod +x scripts/install_marketing_knowledge_timer.sh
bash scripts/install_marketing_knowledge_timer.sh
{sync_cmd}
echo OK=post_install
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Deploy MarketingKnowledgeBase to Oracle.")
    ap.add_argument("--server-name", default=None)
    ap.add_argument(
        "--from-local",
        action="store_true",
        help="Upload from this workspace (skip rsbots-code git pull on server).",
    )
    ap.add_argument("--skip-timer", action="store_true", help="Sync files only; do not install/enable timer.")
    ap.add_argument("--skip-sync-run", action="store_true", help="Do not run MarketingKnowledgeBase.sync after deploy.")
    args = ap.parse_args(argv)

    servers, _ = load_oracle_servers(REPO_ROOT)
    entry = pick_oracle_server(servers, args.server_name) if args.server_name else servers[0]
    remote_root = str(entry.get("remote_root") or "/home/rsadmin/bots/mirror-world").rstrip("/")

    print(f"Server: {entry.get('user')}@{entry.get('host')}")
    print(f"Remote root: {remote_root}")

    if args.from_local:
        tar_path = _build_local_tar()
        remote_tar = f"/tmp/{tar_path.name}"
        print(f"Uploading local bundle: {tar_path}")
        scp_res = _scp(entry, tar_path, remote_tar, timeout=180)
        if scp_res.returncode != 0:
            print(scp_res.stderr or scp_res.stdout, file=sys.stderr)
            return scp_res.returncode or 1

        extract_cmd = f"""
set -euo pipefail
LIVE_ROOT={shlex.quote(remote_root)}
TMP=/tmp/mkb_deploy_$$
mkdir -p "$TMP" "$LIVE_ROOT/MarketingKnowledgeBase/data" "$LIVE_ROOT/scripts"
tar -xzf {shlex.quote(remote_tar)} -C "$TMP"
cp -a "$TMP/MarketingKnowledgeBase/." "$LIVE_ROOT/MarketingKnowledgeBase/"
cp -f "$TMP/scripts/install_marketing_knowledge_timer.sh" "$LIVE_ROOT/scripts/"
if [ -f "$TMP/scripts/pull_marketing_knowledge_from_oracle.py" ]; then
  cp -f "$TMP/scripts/pull_marketing_knowledge_from_oracle.py" "$LIVE_ROOT/scripts/"
fi
if [ -f "$TMP/scripts/run_oracle_deploy_marketing_knowledge.py" ]; then
  cp -f "$TMP/scripts/run_oracle_deploy_marketing_knowledge.py" "$LIVE_ROOT/scripts/"
fi
mkdir -p "$LIVE_ROOT/systemd"
if [ -d "$TMP/systemd" ]; then
  for unit in "$TMP"/systemd/mirror-world-marketing-*.service "$TMP"/systemd/mirror-world-marketing-*.timer "$TMP"/systemd/mirror-world-reesebot.service; do
    [ -f "$unit" ] || continue
    cp -f "$unit" "$LIVE_ROOT/systemd/" 2>/dev/null || sudo cp -f "$unit" "$LIVE_ROOT/systemd/"
  done
  sudo chown rsadmin:rsadmin "$LIVE_ROOT"/systemd/mirror-world-marketing-* "$LIVE_ROOT"/systemd/mirror-world-reesebot.service 2>/dev/null || true
fi
rm -rf "$TMP" {shlex.quote(remote_tar)}
echo OK=extract_local
"""
        res = _ssh(entry, extract_cmd, timeout=120)
        print(res.stdout)
        if res.returncode != 0:
            print(res.stderr, file=sys.stderr)
            return res.returncode or 1
        tar_path.unlink(missing_ok=True)
    else:
        res = _ssh(entry, _remote_sync_from_rsbots(remote_root), timeout=300)
        print(res.stdout)
        if res.returncode != 0:
            print(res.stderr, file=sys.stderr)
            return res.returncode or 1

    if args.skip_timer and args.skip_sync_run:
        print("Skip timer + sync run requested. Done.")
        return 0

    post = _remote_post_install(remote_root, skip_sync_run=args.skip_sync_run)
    if args.skip_timer:
        post = post.replace("bash scripts/install_marketing_knowledge_timer.sh\n", "echo SKIP=timer\n")
    res = _ssh(entry, post, timeout=600)
    if res.stdout:
        sys.stdout.buffer.write(res.stdout.encode("utf-8", errors="replace"))
        sys.stdout.buffer.write(b"\n")
    if res.stderr.strip():
        sys.stderr.buffer.write(res.stderr.encode("utf-8", errors="replace"))
        sys.stderr.buffer.write(b"\n")
    return res.returncode or 0


if __name__ == "__main__":
    raise SystemExit(main())
