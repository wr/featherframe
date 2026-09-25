"""Check copy against the style guide's mechanical rules (docs/STYLE.md).

    python scripts/check_copy.py                   # the webapp's templates
    python scripts/check_copy.py --wiki DIR        # a checkout of the wiki too

Only what a machine can judge is checked here: retired names, emoji,
the casual "bird" in the webapp, jargon on the wiki's user pages. The rest
of the guide (voice, length, hints that add nothing) is a reviewer's job.
Exit status 1 when anything is found; each finding is one line,
`file:line: rule: text`.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent
TEMPLATES = SERVER / "templates"

# Emoji, not symbols: the page's own ⋯ ✓ ✕ ⟳ ✦ and the arrows stay.
EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF✅❌❎⭐⚠⚡✨️]"
)

# Names that are gone, everywhere. Each maps to what is said instead.
RETIRED = {
    r"\bday in review\b": "collage",
    r"\bdark mode\b": "(gone; say nothing)",
    r"\bprimary frame\b": "a frame (there is no primary)",
    r"\bactive frame\b": "a frame (there is no active one)",
    r"\bviewers?\b": "frame, or screen",
    r"\bweb app\b": "webapp",
}
# And in the webapp, where an owner reads them as labels. The wiki may
# name BirdNET-Pi's dashboard, or the books' plates once it has said what
# a plate is.
RETIRED_WEBAPP = {
    r"\bdashboard\b": "webapp, or page",
    r"\bplates?\b": "illustration",
}

# The casual "bird". Names and titles that contain the word are fine.
BIRD = re.compile(r"\bbirds?\b", re.I)
BIRD_OK = re.compile(
    r"BirdNET|BirdWeather|birds\.db|Birds of [A-Z]|Bird detections"
    r"|Birds \(|historical-bird-plates",
)

# Words a reader who is not a developer should never meet.
JARGON = re.compile(
    r"\b(ETag|mDNS|venv|dither(?:ed|ing)?|framebuffer|FFF|kv store|NVS"
    r"|payload|endpoint|webhook payload)\b"
)
# Wiki pages written for developers, where jargon is the point.
WIKI_DEV = {"How-it-works", "Porting-to-another-panel", "Development",
            "How-AI-illustrations-are-made", "Sibling-projects"}

TAG = re.compile(r"<[^>]+>")
JINJA = re.compile(r"\{#.*?#\}|\{%.*?%\}|\{\{.*?\}\}", re.S)
BLOCK = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)
ATTR = re.compile(r'\b(?:placeholder|title|aria-label|alt)="([^"]*)"')
# A script's string literal that reads as a sentence: two words, and none of
# the characters a selector, a URL or an expression is made of.
JS_STRING = re.compile(r"'((?:[^'\\\n]|\\.)*)'")
SENTENCE = re.compile(r"[A-Za-z]{2,} [A-Za-z]{2,}")
NOT_COPY = re.compile(r"[#=${}_/]|\.[a-z]|^[a-z-]+ [a-z-]+$")
CODE = re.compile(r"`[^`]*`|<code>.*?</code>")
LINK_TARGET = re.compile(r"\]\([^)]*\)")


def visible_lines(source: str) -> list[tuple[int, str]]:
    """The page's words as an owner reads them, with their source lines.

    Scripts, styles and template logic are blanked, keeping the line
    count, so a finding points at the line in the template.
    """
    def blank(m: re.Match) -> str:
        return re.sub(r"[^\n]", " ", m.group(0))

    out = []
    for block in BLOCK.finditer(source):
        if not block.group(1).lower() == "script":
            continue
        first = source.count("\n", 0, block.start()) + 1
        for n, line in enumerate(block.group(0).split("\n"), first):
            for lit in JS_STRING.findall(line):
                if SENTENCE.search(lit) and not NOT_COPY.search(lit):
                    out.append((n, lit.strip()))
    text = BLOCK.sub(blank, source)
    text = JINJA.sub(blank, text)
    for n, line in enumerate(text.split("\n"), 1):
        attrs = " ".join(ATTR.findall(line))
        words = html.unescape(TAG.sub(" ", CODE.sub(" ", line)) + " " + attrs)
        words = " ".join(words.split())
        if words:
            out.append((n, words))
    return sorted(out)


def check_line(text: str, *, webapp: bool, dev_page: bool) -> list[tuple[str, str]]:
    found = []
    if EMOJI.search(text):
        found.append(("emoji", "no emoji; write Yes/No or the word"))
    retired = {**RETIRED, **(RETIRED_WEBAPP if webapp else {})}
    for pat, instead in retired.items():
        m = re.search(pat, text, re.I)
        if m:
            found.append(("retired", f"{m.group(0)!r} -> {instead}"))
    if webapp:
        stripped = BIRD_OK.sub("", text)
        if BIRD.search(stripped):
            found.append(("bird", "say species, detection or update"))
    if not webapp and not dev_page and JARGON.search(text):
        found.append(("jargon", f"{JARGON.search(text).group(0)!r} on a user page"))
    return found


def check_template(path: Path) -> list[str]:
    out = []
    for n, text in visible_lines(path.read_text()):
        for rule, why in check_line(text, webapp=True, dev_page=False):
            out.append(f"{path}:{n}: {rule}: {why}: {text[:90]}")
    return out


def check_wiki_page(path: Path) -> list[str]:
    out = []
    dev = path.stem in WIKI_DEV
    fenced = False
    for n, line in enumerate(path.read_text().split("\n"), 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        text = LINK_TARGET.sub("]", CODE.sub(" ", line))
        for rule, why in check_line(text, webapp=False, dev_page=dev):
            out.append(f"{path}:{n}: {rule}: {why}: {line.strip()[:90]}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--wiki", type=Path, help="a checkout of featherframe.wiki")
    args = ap.parse_args(argv)
    findings = []
    for page in sorted(TEMPLATES.glob("*.html")):
        findings += check_template(page)
    if args.wiki:
        for page in sorted(args.wiki.glob("*.md")):
            if page.stem == "Style-guide":  # it quotes what it bans
                continue
            findings += check_wiki_page(page)
    for f in findings:
        print(f)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
