#!/usr/bin/env bash
# Deploy the server to the birdnet-go LXC (pve CT 113, /opt/featherframe).
#
# Ships ONLY committed, tracked files via `git archive` — never the working
# tree. A working-tree tar once swept the gitignored server/data/ (a dev
# database and stale frame) over the box's live state, clobbering the config
# and API key; the box had to be restored from its PBS backup. Data, plates,
# and the venv on the box are never touched by this script.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
REF="${1:-HEAD}"
ARCHIVE="$(mktemp -t ff-server-XXXX).tar.gz"
trap 'rm -f "$ARCHIVE"' EXIT

if ! git -C "$REPO" diff --quiet -- server/; then
  echo "note: server/ has uncommitted changes — deploying $REF, not the working tree" >&2
fi

git -C "$REPO" archive --format=tar.gz -o "$ARCHIVE" "$REF:server"
echo "archived $(git -C "$REPO" rev-parse --short "$REF"):server ($(du -h "$ARCHIVE" | cut -f1))"

scp -q "$ARCHIVE" pve:/tmp/ff-server.tar.gz
ssh pve "pct push 113 /tmp/ff-server.tar.gz /root/ff-server.tar.gz \
  && pct exec 113 -- bash -c 'tar -xzf /root/ff-server.tar.gz -C /opt/featherframe \
       && rm /root/ff-server.tar.gz && systemctl restart featherframe \
       && sleep 3 && systemctl is-active featherframe' \
  && rm /tmp/ff-server.tar.gz"

ssh pve "pct exec 113 -- curl -s http://localhost:8081/api/status" | head -c 300
echo
