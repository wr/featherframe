#!/usr/bin/env bash
#
# Featherframe server installer for a BirdNET-Pi.
#
# Creates a self-contained venv, installs deps, downloads the Audubon plates,
# and installs + enables a systemd service — living alongside BirdNET-Pi the
# same bare-metal way it does. Run it from the repo:
#
#     cd featherframe/server && ./install.sh
#
# Options:
#   --skip-plates   don't download plates now (run scripts/fetch_plates.py later)
#   --port N        listen port (default 8080)
#   --no-service    set up the venv only; don't touch systemd
#
set -euo pipefail

SERVER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${FEATHERFRAME_DATA_DIR:-$SERVER_DIR/data}"
PORT=8080
DO_PLATES=1
DO_SERVICE=1
# The service should run as the human user who owns BirdNET-Pi, not root.
RUN_USER="${SUDO_USER:-$(id -un)}"

while [ $# -gt 0 ]; do
  case "$1" in
    --skip-plates) DO_PLATES=0 ;;
    --no-service)  DO_SERVICE=0 ;;
    --port)        PORT="$2"; shift ;;
    *) echo "unknown option: $1"; exit 1 ;;
  esac
  shift
done

echo "==> Featherframe install"
echo "    server dir : $SERVER_DIR"
echo "    data dir   : $DATA_DIR"
echo "    run as     : $RUN_USER"
echo "    port       : $PORT"

# --- system libs ---------------------------------------------------------
# libraqm gives Pillow real OpenType shaping (swash italics + true small caps
# on the plate caption). Without it the caption degrades to faux small caps —
# still fine, just plainer. Best-effort; never fail the install over it.
if command -v apt-get >/dev/null 2>&1; then
  echo "==> Ensuring libraqm (for OpenType caption shaping)…"
  sudo apt-get install -y libraqm0 >/dev/null 2>&1 || \
    echo "    libraqm install skipped — caption will use faux small caps."
fi

# --- venv + deps ---------------------------------------------------------
echo "==> Creating venv and installing dependencies…"
python3 -m venv "$SERVER_DIR/.venv"
"$SERVER_DIR/.venv/bin/pip" install --upgrade pip >/dev/null
# piwheels (default on Raspberry Pi OS) provides prebuilt numpy/Pillow wheels,
# so this stays quick even on a Pi Zero.
"$SERVER_DIR/.venv/bin/pip" install -r "$SERVER_DIR/requirements.txt"

mkdir -p "$DATA_DIR"

# --- plates --------------------------------------------------------------
if [ "$DO_PLATES" -eq 1 ]; then
  echo "==> Downloading Audubon plates (~220 MB, one time)…"
  FEATHERFRAME_DATA_DIR="$DATA_DIR" "$SERVER_DIR/.venv/bin/python" \
    "$SERVER_DIR/scripts/fetch_plates.py" || {
      echo "    plate download had issues — re-run scripts/fetch_plates.py later."; }
else
  echo "==> Skipping plate download (run scripts/fetch_plates.py before first use)."
fi

# --- systemd -------------------------------------------------------------
if [ "$DO_SERVICE" -eq 1 ]; then
  UNIT=/etc/systemd/system/featherframe.service
  echo "==> Installing systemd unit at $UNIT (needs sudo)…"
  sudo tee "$UNIT" >/dev/null <<EOF
[Unit]
Description=Featherframe e-paper bird frame
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$SERVER_DIR
Environment=FEATHERFRAME_DATA_DIR=$DATA_DIR
Environment=FEATHERFRAME_PLATES_DIR=$SERVER_DIR/plates
Environment=FEATHERFRAME_PORT=$PORT
ExecStart=$SERVER_DIR/.venv/bin/python -m featherframe
Restart=on-failure
RestartSec=5
# Be a gentle tenant next to BirdNET's continuous analyzer.
Nice=10
IOSchedulingClass=idle

[Install]
WantedBy=multi-user.target
EOF

  sudo systemctl daemon-reload
  sudo systemctl enable --now featherframe.service
  echo "==> Service enabled and started."
  sleep 1
  sudo systemctl --no-pager --lines=8 status featherframe.service || true
fi

HOST="$(hostname).local"
echo ""
echo "==> Done. Open the config page:"
echo "      http://$HOST:$PORT/    (or http://<pi-ip>:$PORT/)"
echo "    Point the frame's firmware at that URL during Wi-Fi setup."
