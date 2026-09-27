"""print_kit.py: the kit's card and back label. No Chrome here: the pages, not the PDFs."""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "print_kit.py"


@pytest.fixture(scope="module")
def pk():
    spec = importlib.util.spec_from_file_location("print_kit", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_kit_is_its_device_id_and_model(pk):
    assert pk.parse_kit("10B41DE886D8:ee03", "") == ("10B41DE886D8", "FF-EE03")
    assert pk.parse_kit("a0:b1:c2:d3:e4:f5:EE02", "") == ("A0B1C2D3E4F5", "FF-EE02")
    assert pk.parse_kit("a0:b1:c2:d3:e4:f5", "ee03") == ("A0B1C2D3E4F5", "FF-EE03")
    with pytest.raises(argparse.ArgumentTypeError):
        pk.parse_kit("10B41DE886D8", "")          # no model
    with pytest.raises(argparse.ArgumentTypeError):
        pk.parse_kit("10B41DE886:ee03", "")       # too short


def test_labels_fill_the_sheet_in_reading_order(pk):
    cells = pk.grid(0.6, 0.833, 0.833, 0.6)
    assert len(cells) == 8
    assert cells[0] == (0.833, 0.6) and cells[1][1] == 0.6 and cells[2][0] == 0.833
    right, bottom = max(x for x, _ in cells) + pk.LABEL_W, max(y for _, y in cells) + pk.LABEL_H
    assert right <= 8.5 and bottom <= 11


def test_labels_page_carries_each_serial_and_skips(pk):
    kits = [("10B41DE886D8", "FF-EE03"), ("A0B1C2D3E4F5", "FF-EE02")]
    page = pk.labels_html(kits, pk.grid(0.6, 0.833, 0.833, 0.6), skip=7)
    assert "10B41DE886D8" in page and "A0B1C2D3E4F5" in page
    assert page.count('class="sheet"') == 2          # the 8th label, then a new sheet
    assert "Contains FCC ID: Z4T-XIAOESP32S3P" in page


def test_the_card_shows_no_real_code(pk):
    im = pk.setup_screen()
    # The example code is drawn as wireframe, never as type: nothing near black
    # remains in the code's row.
    import numpy as np
    from featherframe.render import welcome
    url = "HTTPS://CLOUD.FEATHERFRAME.APP/SETUP/ABCDEF/ABCDEFGHJK12"
    qx, qy = welcome.setup_qr_origin("EHT-AMD", "28 September, 10:32 AM", url)
    row = np.array(im)[qy - 20:, qx - 10:]
    assert row.min() >= 100
