# Featherframe — dev tasks. The important one is `make preview`: it runs the
# whole art pipeline end-to-end against a fake detection with no hardware.

PY := server/.venv/bin/python
PIP := server/.venv/bin/pip

.PHONY: help venv plates plates-all preview preview-all preview-collage preview-ee02 preview-views preview-fallback serve test clean ota ota-ee02 deploy-target

help:
	@echo "Featherframe targets:"
	@echo "  make venv             create the server venv and install deps"
	@echo "  make plates           download the folios' plates (scripts/folios/)"
	@echo "  make plates-all       cache every Havell plate (~2.9 GB, idempotent)"
	@echo "  make preview          render a fake Northern Cardinal -> PNG + .fff in test_output/"
	@echo "  make preview-all      render every curated species"
	@echo "  make preview-collage  render a daily collage"
	@echo "  make preview-ee02     render the Cardinal for the EE02 colour panel"
	@echo "  make preview-views    the Cardinal as viewers get it (TRMNL, e-readers, a tablet)"
	@echo "  make preview-fallback render the typographic fallback plate"
	@echo "  make serve            run the server locally on :8181 (FEATHERFRAME_NO_MDNS=1 for a dev copy)"
	@echo "  make test             run the unit tests"
	@echo "  make ota              build the EE03 firmware and host it on your server (ota-ee02: the EE02)"
	@echo "                        needs FEATHERFRAME_DEPLOY_HOST and FEATHERFRAME_DEPLOY_CT; see the Makefile"
	@echo "  make clean            remove venv, previews, and runtime state"

venv:
	cd server && python3 -m venv .venv && ./.venv/bin/pip install --upgrade pip && ./.venv/bin/pip install -r requirements.txt && (./.venv/bin/pip install -r requirements-optional.txt || true)

plates:
	cd server && ../$(PY) scripts/fetch_plates.py

# The whole edition, so a new folios/havell.yaml entry never needs the network.
plates-all:
	cd server && ../$(PY) scripts/fetch_plates.py --all

# The headline deliverable: end-to-end, no hardware.
preview:
	cd server && ../$(PY) -m featherframe.preview --species "Northern Cardinal"
	@echo "-> see test_output/northern_cardinal.png (+ .fff framebuffer)"

preview-all:
	cd server && ../$(PY) -m featherframe.preview --all

preview-collage:
	cd server && ../$(PY) -m featherframe.preview --collage 6

preview-ee02:
	cd server && ./.venv/bin/python -m featherframe.preview --panel ee02

preview-views:
	cd server && ./.venv/bin/python -m featherframe.preview --views --mat-inset 0

preview-fallback:
	cd server && ../$(PY) -m featherframe.preview --fallback

# Advertises _featherframe._tcp over mDNS, as a real install does: a frame on
# the LAN whose own server is down WILL adopt this one. For a dev copy beside
# a live frame: FEATHERFRAME_NO_MDNS=1 make serve
serve:
	cd server && ../$(PY) -m featherframe

test:
	cd server && ../$(PY) -m pytest

# Build the firmware and host it on a self-hosted server; the frame flashes
# itself on its next check-in (right after a boot fetch, then every 15 min). No
# USB needed. The server runs in a Proxmox container: the image is copied to the
# Proxmox host, then `pct push`ed into the container. Set, in the environment:
#   FEATHERFRAME_DEPLOY_HOST  the Proxmox host, as ssh reaches it
#   FEATHERFRAME_DEPLOY_CT    the container's id
#   FEATHERFRAME_DEPLOY_DIR   the install directory in it (default /opt/featherframe)
# scripts/deploy.sh reads the same variables.
FEATHERFRAME_DEPLOY_DIR ?= /opt/featherframe
DEPLOY_HOST = $(FEATHERFRAME_DEPLOY_HOST)
DEPLOY_CT   = $(FEATHERFRAME_DEPLOY_CT)
DEPLOY_DATA = $(FEATHERFRAME_DEPLOY_DIR)/data

deploy-target:
	@if [ -z "$(DEPLOY_HOST)" ] || [ -z "$(DEPLOY_CT)" ]; then \
	  echo "Set FEATHERFRAME_DEPLOY_HOST (the Proxmox host) and FEATHERFRAME_DEPLOY_CT (the container id) first." >&2; \
	  exit 1; fi

ota: deploy-target
	cd firmware && pio run -e xiao_ee03
	# Push to a temp name and rename: a device fetching mid-copy must never see a torn image.
	scp -q firmware/.pio/build/xiao_ee03/firmware.bin $(DEPLOY_HOST):/tmp/ff-firmware.bin
	ssh $(DEPLOY_HOST) 'pct push $(DEPLOY_CT) /tmp/ff-firmware.bin $(DEPLOY_DATA)/firmware.bin.tmp \
	  && pct exec $(DEPLOY_CT) -- mv $(DEPLOY_DATA)/firmware.bin.tmp $(DEPLOY_DATA)/firmware.bin'
	@echo "Hosted. The frame updates itself on its next check-in."

# The EE02 colour frame is a second kit on the same server (W-832): its image
# sits beside the EE03's as firmware-ee02.bin, and the server hands each board
# its own (it matches the board string inside the image, and refuses the rest).
ota-ee02: deploy-target
	cd firmware && pio run -e ee02
	scp -q firmware/.pio/build/ee02/firmware.bin $(DEPLOY_HOST):/tmp/ff-firmware-ee02.bin
	ssh $(DEPLOY_HOST) 'pct push $(DEPLOY_CT) /tmp/ff-firmware-ee02.bin $(DEPLOY_DATA)/firmware-ee02.bin.tmp \
	  && pct exec $(DEPLOY_CT) -- mv $(DEPLOY_DATA)/firmware-ee02.bin.tmp $(DEPLOY_DATA)/firmware-ee02.bin'
	@echo "Hosted. The EE02 updates itself on its next check-in."

clean:
	rm -rf server/.venv server/data test_output/*.png test_output/*.fff
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

# featherframe.app, the marketing site (site/)
site:
	cd site && npm install && npm run build
site-test:
	cd site && npm install && npm test
site-deploy:
	cd site && npm install && npm run deploy
.PHONY: site site-test site-deploy
