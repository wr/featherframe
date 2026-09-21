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
	@echo "  make serve            run the server locally on :8080"
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

serve:
	cd server && FEATHERFRAME_DEV=1 ../$(PY) -m featherframe

test:
	cd server && ../$(PY) -m pytest

# Build the firmware and host it on the server box; the frame flashes itself on
# its next check-in (right after a boot fetch, then every 15 min). No USB needed.
# The box is an LXC on Proxmox: copy to the host, then `pct push` into the
# container. Override BOX_HOST / BOX_CT / BOX_DATA for another install.
BOX_HOST ?= pve
BOX_CT   ?= 113
BOX_DATA ?= /opt/featherframe/data
BOX_DATA_EE02 ?= /opt/featherframe1/data
ota:
	cd firmware && pio run -e xiao_ee03
	# Push to a temp name and rename: a device fetching mid-copy must never see a torn image.
	scp -q firmware/.pio/build/xiao_ee03/firmware.bin $(BOX_HOST):/tmp/ff-firmware.bin
	ssh $(BOX_HOST) 'pct push $(BOX_CT) /tmp/ff-firmware.bin $(BOX_DATA)/firmware.bin.tmp \
	  && pct exec $(BOX_CT) -- mv $(BOX_DATA)/firmware.bin.tmp $(BOX_DATA)/firmware.bin'
	@echo "Hosted. The frame updates itself on its next check-in."

# The EE02 colour frame has its own server instance (featherframe1, :8082,
# data in /opt/featherframe1/data) and its own image: never cross the two.
# The server refuses to serve an image to the wrong board (X-Board) anyway.
ota-ee02:
	cd firmware && pio run -e ee02
	scp -q firmware/.pio/build/ee02/firmware.bin $(BOX_HOST):/tmp/ff1-firmware.bin
	ssh $(BOX_HOST) 'pct push $(BOX_CT) /tmp/ff1-firmware.bin $(BOX_DATA_EE02)/firmware.bin.tmp \
	  && pct exec $(BOX_CT) -- mv $(BOX_DATA_EE02)/firmware.bin.tmp $(BOX_DATA_EE02)/firmware.bin'
	@echo "Hosted on the EE02 instance. The frame updates itself on its next check-in."

clean:
	rm -rf server/.venv server/data test_output/*.png test_output/*.fff
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
