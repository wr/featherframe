# Featherframe — dev tasks. The important one is `make preview`: it runs the
# whole art pipeline end-to-end against a fake detection with no hardware.

PY := server/.venv/bin/python
PIP := server/.venv/bin/pip

.PHONY: help venv plates preview preview-all preview-collage preview-fallback serve test clean

help:
	@echo "Featherframe targets:"
	@echo "  make venv             create the server venv and install deps"
	@echo "  make plates           download Audubon plates (species.yaml)"
	@echo "  make preview          render a fake Northern Cardinal -> PNG + .fff in test_output/"
	@echo "  make preview-all      render every curated species"
	@echo "  make preview-collage  render a daily collage"
	@echo "  make preview-fallback render the typographic fallback plate"
	@echo "  make serve            run the server locally on :8080"
	@echo "  make test             run the unit tests"
	@echo "  make clean            remove venv, previews, and runtime state"

venv:
	cd server && python3 -m venv .venv && ./.venv/bin/pip install --upgrade pip && ./.venv/bin/pip install -r requirements.txt

plates:
	cd server && ../$(PY) scripts/fetch_plates.py

# The headline deliverable: end-to-end, no hardware.
preview:
	cd server && ../$(PY) -m featherframe.preview --species "Northern Cardinal"
	@echo "-> see test_output/northern_cardinal.png (+ .fff framebuffer)"

preview-all:
	cd server && ../$(PY) -m featherframe.preview --all

preview-collage:
	cd server && ../$(PY) -m featherframe.preview --collage 6

preview-fallback:
	cd server && ../$(PY) -m featherframe.preview --fallback

serve:
	cd server && FEATHERFRAME_DEV=1 ../$(PY) -m featherframe

test:
	cd server && ../$(PY) -m pytest

# Build the firmware and hand it to the Pi; the frame flashes itself on its
# next wake (<= wake interval, default 15 min). No USB needed.
PI ?= wells@10.0.2.15
ota:
	cd firmware && pio run -e xiao_ee03
	scp firmware/.pio/build/xiao_ee03/firmware.bin $(PI):~/featherframe/server/data/firmware.bin
	@echo "Hosted. The frame updates itself on its next check-in."

clean:
	rm -rf server/.venv server/data test_output/*.png test_output/*.fff
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
