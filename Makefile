# Featherframe — dev tasks. The important one is `make preview`: it runs the
# whole art pipeline end-to-end against a fake detection with no hardware.

PY := server/.venv/bin/python
PIP := server/.venv/bin/pip

.PHONY: help venv plates plates-all plates-pack preview preview-all preview-collage preview-ee02 preview-views preview-fallback serve test clean

help:
	@echo "Featherframe targets:"
	@echo "  make venv             create the server venv and install deps"
	@echo "  make plates           download Audubon plates (species.yaml)"
	@echo "  make plates-all       cache every Havell plate (~2.9 GB, idempotent)"
	@echo "  make plates-pack      pack the cached edition into release tarballs (dist/plates)"
	@echo "  make preview          render a fake Northern Cardinal -> PNG + .fff in test_output/"
	@echo "  make preview-all      render every curated species"
	@echo "  make preview-collage  render a daily collage"
	@echo "  make preview-ee02     render the Cardinal for the EE02 colour panel"
	@echo "  make preview-views    the Cardinal as viewers get it (TRMNL, e-readers, a tablet)"
	@echo "  make preview-fallback render the typographic fallback plate"
	@echo "  make serve            run the server locally on :8080 (FEATHERFRAME_NO_MDNS=1 for a dev copy)"
	@echo "  make test             run the unit tests"
	@echo "  make clean            remove venv, previews, and runtime state"

venv:
	cd server && python3 -m venv .venv && ./.venv/bin/pip install --upgrade pip && ./.venv/bin/pip install -r requirements.txt

plates:
	cd server && ../$(PY) scripts/fetch_plates.py

# The whole edition, so a new species.yaml entry never needs the network.
plates-all:
	cd server && ../$(PY) scripts/fetch_plates.py --all

# Release assets for the plates-v1 GitHub release (dist/plates; add --publish by hand).
plates-pack:
	cd server && ../$(PY) scripts/pack_plates.py

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

# Build the firmware and host it on the server box; the frame flashes itself on
# its next check-in (right after a boot fetch, then every 15 min). No USB needed.
# The box is an LXC on Proxmox: copy to the host, then `pct push` into the
# container. Override BOX_HOST / BOX_CT / BOX_DATA for another install.
BOX_HOST ?= pve
BOX_CT   ?= 113
BOX_DATA ?= /opt/featherframe/data
ota:
	cd firmware && pio run -e xiao_ee03
	# Push to a temp name and rename: a device fetching mid-copy must never see a torn image.
	scp -q firmware/.pio/build/xiao_ee03/firmware.bin $(BOX_HOST):/tmp/ff-firmware.bin
	ssh $(BOX_HOST) 'pct push $(BOX_CT) /tmp/ff-firmware.bin $(BOX_DATA)/firmware.bin.tmp \
	  && pct exec $(BOX_CT) -- mv $(BOX_DATA)/firmware.bin.tmp $(BOX_DATA)/firmware.bin'
	@echo "Hosted. The frame updates itself on its next check-in."

# The EE02 colour frame is a second kit on the same server (W-832): its image
# sits beside the EE03's as firmware-ee02.bin, and the server hands each board
# its own (it matches the board string inside the image, and refuses the rest).
ota-ee02:
	cd firmware && pio run -e ee02
	scp -q firmware/.pio/build/ee02/firmware.bin $(BOX_HOST):/tmp/ff-firmware-ee02.bin
	ssh $(BOX_HOST) 'pct push $(BOX_CT) /tmp/ff-firmware-ee02.bin $(BOX_DATA)/firmware-ee02.bin.tmp \
	  && pct exec $(BOX_CT) -- mv $(BOX_DATA)/firmware-ee02.bin.tmp $(BOX_DATA)/firmware-ee02.bin'
	@echo "Hosted. The EE02 updates itself on its next check-in."

clean:
	rm -rf server/.venv server/data test_output/*.png test_output/*.fff
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
