"""The style guide's mechanical rules hold on the webapp (docs/STYLE.md)."""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_copy.py"
spec = importlib.util.spec_from_file_location("check_copy", SCRIPT)
check_copy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_copy)


def rules(text, **kw):
    kw = {"webapp": True, "dev_page": False, **kw}
    return [r for r, _ in check_copy.check_line(text, **kw)]


def test_webapp_copy_follows_the_style_guide():
    findings = []
    for page in sorted(check_copy.TEMPLATES.glob("*.html")):
        findings += check_copy.check_template(page)
    assert findings == [], "\n".join(findings)


def test_casual_bird_is_caught_but_names_are_not():
    assert rules("A new bird shows within seconds.") == ["bird"]
    assert rules("BirdNET-Pi keeps birds.db; leave Bird detections ticked.") == []
    assert rules("Europe · Gould's Birds of Europe") == []


def test_retired_names_and_emoji_are_caught():
    assert rules("The day in review is drawn at night.") == ["retired"]
    assert rules("Generated plates") == ["retired"]
    assert rules("Saved ✅") == ["emoji"]
    assert rules("Saved ✓ ⋯ ⟳ ✦") == []


def test_wiki_allows_plates_and_birds_but_not_jargon_on_user_pages():
    wiki = {"webapp": False}
    assert rules("Audubon's plates, and the birds you hear.", **wiki) == []
    assert rules("The frame sends its ETag.", **wiki) == ["jargon"]
    assert rules("The frame sends its ETag.", webapp=False, dev_page=True) == []


def test_script_strings_are_read_as_copy():
    page = "<script>\nvar a = 'Rendering the plate';\nvar b = '#frames .row';\n</script>"
    lines = check_copy.visible_lines(page)
    assert (2, "Rendering the plate") in lines
    assert not any("#frames" in t for _, t in lines)
