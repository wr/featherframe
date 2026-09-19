#!/usr/bin/env bash
#
# Featherframe server installer — and upgrader. Safe to re-run.
#
# Creates a self-contained venv, installs deps, downloads the Audubon plates,
# and installs + enables a systemd service. Run it from the repo:
#
#     cd featherframe/server && ./install.sh
#
# On a BirdNET-Pi that is all: it lives alongside BirdNET-Pi the same
# bare-metal way and reads its birds.db. With BirdNET-Go the server can run on
# any Linux box on the LAN; name the source once:
#
#     ./install.sh --source birdnet-go --url http://<birdnet-go-host>:8080
#
# Re-running is the upgrade path (`git pull && ./install.sh`): the venv is
# reused, plates already on disk are kept, the unit file is rewritten only if
# it changed, and the service is restarted so the new code is live. The port,
# data dir, and run-as user of an existing install are read back from its
# unit file, so an upgrade never silently resets them. data/ (config DB,
# frames, generated plates, hosted firmware) is never touched.
#
# Options:
#   --skip-plates   don't download plates now (run scripts/fetch_plates.py later)
#   --all-plates    cache the whole Havell edition (~2.9 GB) instead of just the curated species
#   --port N        listen port (default 8080, or the existing install's port)
#   --source NAME   birdnet-pi (the default: read BirdNET-Pi's birds.db) or
#                   birdnet-go; written to the config once, and only when given,
#                   so an upgrade never resets what the page has set
#   --url URL       BirdNET-Go's address, with --source birdnet-go
#   --no-service    set up the venv only; don't touch systemd
#   --check         report what would change and exit; touch nothing
#
set -euo pipefail

SERVER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT="${FEATHERFRAME_UNIT:-/etc/systemd/system/featherframe.service}"
PORT=""
DATA_DIR="${FEATHERFRAME_DATA_DIR:-}"
DO_PLATES=1
ALL_PLATES=0
DO_SERVICE=1
CHECK=0
# The service should run as the human user who owns BirdNET-Pi, not root.
RUN_USER=""
SOURCE=""
SOURCE_URL=""
# Root (a container, say) has no sudo and needs none.
SUDO=""; [ "$(id -u)" -eq 0 ] || SUDO=sudo

while [ $# -gt 0 ]; do
  case "$1" in
    --skip-plates) DO_PLATES=0 ;;
    --all-plates)  ALL_PLATES=1 ;;
    --no-service)  DO_SERVICE=0 ;;
    --check)       CHECK=1 ;;
    --port)        PORT="$2"; shift ;;
    --source)      SOURCE="$2"; shift ;;
    --url)         SOURCE_URL="$2"; shift ;;
    *) echo "unknown option: $1"; exit 1 ;;
  esac
  shift
done

case "$SOURCE" in
  ""|birdnet-pi) ;;
  birdnet-go)
    case "$SOURCE_URL" in
      http://*|https://*) ;;
      *) echo "--source birdnet-go needs --url http://<birdnet-go-host>:8080"; exit 1 ;;
    esac ;;
  *) echo "unknown --source: $SOURCE (birdnet-pi or birdnet-go)"; exit 1 ;;
esac

# --- read back an existing install ---------------------------------------
# A re-run without flags must keep what the box already runs with.
unit_env() {  # unit_env NAME -> value of Environment=NAME=... in the existing unit
  sed -n "s/^Environment=$1=//p" "$UNIT" 2>/dev/null | head -n1
}
if [ -r "$UNIT" ]; then
  EXISTING=1
  [ -n "$PORT" ]     || PORT="$(unit_env FEATHERFRAME_PORT)"
  [ -n "$DATA_DIR" ] || DATA_DIR="$(unit_env FEATHERFRAME_DATA_DIR)"
  RUN_USER="$(sed -n 's/^User=//p' "$UNIT" | head -n1)"
else
  EXISTING=0
fi
[ -n "$PORT" ]     || PORT=8080
[ -n "$DATA_DIR" ] || DATA_DIR="$SERVER_DIR/data"
[ -n "$RUN_USER" ] || RUN_USER="${SUDO_USER:-$(id -un)}"

if [ "$CHECK" -eq 1 ]; then
  echo "==> Featherframe install --check (nothing will be changed)"
else
  echo "==> Featherframe $([ "$EXISTING" -eq 1 ] && echo upgrade || echo install)"
fi
echo "    server dir : $SERVER_DIR"
echo "    data dir   : $DATA_DIR"
echo "    run as     : $RUN_USER"
echo "    port       : $PORT"

# A change is something the run would do; --check prints it and moves on.
CHANGES=0
plan() { CHANGES=$((CHANGES + 1)); echo "  * $1"; }

# --- system libs ---------------------------------------------------------
# libraqm gives Pillow real OpenType shaping (swash italics + true small caps
# on the plate caption). Without it the caption degrades to faux small caps —
# still fine, just plainer. Best-effort; never fail the install over it.
if command -v apt-get >/dev/null 2>&1; then
  if dpkg -s libraqm0 >/dev/null 2>&1; then
    echo "==> libraqm present."
  elif [ "$CHECK" -eq 1 ]; then
    plan "install libraqm0 (apt)"
  else
    echo "==> Ensuring libraqm (for OpenType caption shaping)…"
    $SUDO apt-get install -y libraqm0 >/dev/null 2>&1 || \
      echo "    libraqm install skipped — caption will use faux small caps."
  fi
fi

# --- venv + deps ---------------------------------------------------------
VENV="$SERVER_DIR/.venv"
if [ -x "$VENV/bin/python" ] && "$VENV/bin/python" -c 'import sys' >/dev/null 2>&1; then
  echo "==> Reusing venv at $VENV."
else
  if [ "$CHECK" -eq 1 ]; then
    plan "create venv at $VENV"
  else
    echo "==> Creating venv…"
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install --upgrade pip >/dev/null
  fi
fi
if [ "$CHECK" -eq 1 ]; then
  # pip's own dry run says whether requirements.txt would change anything.
  if [ -x "$VENV/bin/pip" ]; then
    if "$VENV/bin/pip" install --dry-run -q -r "$SERVER_DIR/requirements.txt" 2>/dev/null \
        | grep -q '^Would install'; then
      plan "install/upgrade Python dependencies"
    else
      echo "==> Python dependencies up to date."
    fi
  else
    plan "install Python dependencies"
  fi
else
  echo "==> Installing dependencies…"
  # piwheels (default on Raspberry Pi OS) provides prebuilt numpy/Pillow wheels,
  # so this stays quick even on a Pi Zero. pip is idempotent: nothing already
  # satisfied is touched.
  "$VENV/bin/pip" install -r "$SERVER_DIR/requirements.txt"
fi

[ "$CHECK" -eq 1 ] || mkdir -p "$DATA_DIR"

# --- detection source ----------------------------------------------------
# Only when asked: the page owns this setting on every later run.
if [ -n "$SOURCE" ]; then
  if [ "$CHECK" -eq 1 ]; then
    plan "set the detection source to $SOURCE${SOURCE_URL:+ ($SOURCE_URL)}"
  else
    SOURCE_ARGS=(--source "$SOURCE")
    [ -n "$SOURCE_URL" ] && SOURCE_ARGS+=(--url "$SOURCE_URL")
    FEATHERFRAME_DATA_DIR="$DATA_DIR" "$VENV/bin/python" \
      "$SERVER_DIR/scripts/set_source.py" "${SOURCE_ARGS[@]}" | sed 's/^/==> /'
  fi
fi

# --- plates --------------------------------------------------------------
# fetch_plates.py skips plates already on disk, so on an upgrade it only pulls
# what a new species.yaml entry needs.
if [ "$DO_PLATES" -eq 1 ]; then
  PLATE_ARGS=()
  [ "$ALL_PLATES" -eq 1 ] && PLATE_ARGS+=(--all)
  if [ "$CHECK" -eq 1 ]; then
    if [ -r "$SERVER_DIR/plates/index.json" ]; then
      echo "==> Plates present; a run would fetch only what species.yaml newly needs."
    else
      plan "download Audubon plates ($([ "$ALL_PLATES" -eq 1 ] && echo '~2.9 GB' || echo '~2.9 GB'))"
    fi
  else
    if [ "$ALL_PLATES" -eq 1 ]; then
      echo "==> Caching the whole Havell edition (~2.9 GB, one time)…"
    elif [ -r "$SERVER_DIR/plates/index.json" ]; then
      echo "==> Checking plates (only new species are fetched)…"
    else
      echo "==> Downloading Audubon plates (~2.9 GB, one time)…"
    fi
    FEATHERFRAME_DATA_DIR="$DATA_DIR" "$VENV/bin/python" \
      "$SERVER_DIR/scripts/fetch_plates.py" ${PLATE_ARGS[@]+"${PLATE_ARGS[@]}"} || {
        echo "    plate download had issues — re-run scripts/fetch_plates.py later."; }
  fi
else
  echo "==> Skipping plate download (run scripts/fetch_plates.py before first use)."
fi

# --- systemd -------------------------------------------------------------
render_unit() {
  cat <<EOF
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
ExecStart=$VENV/bin/python -m featherframe
Restart=on-failure
RestartSec=5
# Be a gentle tenant next to BirdNET's continuous analyzer.
Nice=10
IOSchedulingClass=idle

[Install]
WantedBy=multi-user.target
EOF
}

if [ "$DO_SERVICE" -eq 1 ]; then
  WANT_UNIT="$(render_unit)"
  UNIT_CHANGED=1
  if [ -r "$UNIT" ] && [ "$(cat "$UNIT")" = "$WANT_UNIT" ]; then
    UNIT_CHANGED=0
  fi

  if [ "$CHECK" -eq 1 ]; then
    if [ "$UNIT_CHANGED" -eq 1 ]; then
      plan "write systemd unit $UNIT$([ "$EXISTING" -eq 1 ] && echo ' (changed)')"
      if [ -r "$UNIT" ]; then
        diff -u "$UNIT" <(printf '%s\n' "$WANT_UNIT") | sed 's/^/      /' || true
      fi
    else
      echo "==> systemd unit unchanged."
    fi
    plan "restart featherframe.service so the current code is live"
  else
    if [ "$UNIT_CHANGED" -eq 1 ]; then
      echo "==> Writing systemd unit at $UNIT (needs sudo)…"
      printf '%s\n' "$WANT_UNIT" | $SUDO tee "$UNIT" >/dev/null
      $SUDO systemctl daemon-reload
    else
      echo "==> systemd unit unchanged."
    fi
    # enable --now leaves a running service on its old code; restart is the
    # point of an upgrade.
    $SUDO systemctl enable featherframe.service >/dev/null 2>&1 || true
    $SUDO systemctl restart featherframe.service
    echo "==> Service enabled and $([ "$EXISTING" -eq 1 ] && echo restarted || echo started)."
    sleep 1
    $SUDO systemctl --no-pager --lines=8 status featherframe.service || true
  fi
fi

if [ "$CHECK" -eq 1 ]; then
  echo ""
  if [ "$CHANGES" -eq 0 ]; then
    echo "==> Nothing to do."
  else
    echo "==> $CHANGES change(s) pending. Run without --check to apply."
  fi
  exit 0
fi

HOST="$(hostname -s 2>/dev/null || hostname).local"
echo ""
echo "==> Done. Open the config page:"
echo "      http://$HOST:$PORT/    (or http://<this-machine's-ip>:$PORT/)"
echo "    The frame finds this server by mDNS; type the URL into its setup"
echo "    portal only if your network blocks multicast."
