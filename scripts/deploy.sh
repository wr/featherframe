#!/usr/bin/env bash
# Deploy the server to a self-hosted install in a Proxmox container.
#
#   scripts/deploy.sh [ref]      (default HEAD)
#
# Set, in the environment:
#   FEATHERFRAME_DEPLOY_HOST  the Proxmox host, as ssh reaches it
#   FEATHERFRAME_DEPLOY_CT    the container's id
#   FEATHERFRAME_DEPLOY_DIR   the install directory in it (default /opt/featherframe)
#   FEATHERFRAME_DEPLOY_PORT  the server's port, for the status check (default 8181)
# `make ota` reads the same variables.
#
# Ships ONLY committed, tracked files via `git archive` — never the working
# tree. A working-tree tar once swept the gitignored server/data/ (a dev
# database and stale frame) over a live install's state, clobbering the config
# and API key; it had to be restored from a backup. Data, plates, and the venv
# on the server are never touched by this script.
set -euo pipefail

HOST="${FEATHERFRAME_DEPLOY_HOST:?set FEATHERFRAME_DEPLOY_HOST to the Proxmox host (as ssh reaches it)}"
CT="${FEATHERFRAME_DEPLOY_CT:?set FEATHERFRAME_DEPLOY_CT to the container id}"
DIR="${FEATHERFRAME_DEPLOY_DIR:-/opt/featherframe}"
PORT="${FEATHERFRAME_DEPLOY_PORT:-8181}"

REPO="$(cd "$(dirname "$0")/.." && pwd)"
REF="${1:-HEAD}"
ARCHIVE="$(mktemp -t ff-server-XXXX).tar.gz"
trap 'rm -f "$ARCHIVE"' EXIT

if ! git -C "$REPO" diff --quiet -- server/; then
  echo "note: server/ has uncommitted changes — deploying $REF, not the working tree" >&2
fi

git -C "$REPO" archive --format=tar.gz -o "$ARCHIVE" "$REF:server"
echo "archived $(git -C "$REPO" rev-parse --short "$REF"):server ($(du -h "$ARCHIVE" | cut -f1))"

scp -q "$ARCHIVE" "$HOST:/tmp/ff-server.tar.gz"
ssh "$HOST" "pct push $CT /tmp/ff-server.tar.gz /root/ff-server.tar.gz \
  && pct exec $CT -- bash -c 'tar -xzf /root/ff-server.tar.gz -C $DIR \
       && rm /root/ff-server.tar.gz && systemctl restart featherframe \
       && sleep 3 && systemctl is-active featherframe \
       && if systemctl is-enabled -q featherframe1 2>/dev/null; then \
            systemctl restart featherframe1 && sleep 3 && systemctl is-active featherframe1; fi' \
  && rm /tmp/ff-server.tar.gz"

ssh "$HOST" "pct exec $CT -- curl -s http://localhost:$PORT/api/status" | head -c 300
echo
