"""The webapp's own build (W-946): a hash of the page's template and static
files. Featherframe Cloud's front door keys its cached copies of the page by
it, so a new server image never serves a page drawn by an older one."""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Optional

from . import paths

def build(templates: Optional[Path] = None, static: Optional[Path] = None) -> str:
    """The first 12 hex of the sha256 of every file under templates/ and
    static/, by path and content. Computed once per process for the real ones."""
    if templates is None and static is None:
        return _real()
    return _hash(templates or paths.templates_dir(), static or paths.static_dir())


@lru_cache(maxsize=1)
def _real() -> str:
    return _hash(paths.templates_dir(), paths.static_dir())


def _hash(*roots: Path) -> str:
    h = hashlib.sha256()
    for root in roots:
        if not root.exists():
            continue
        for p in sorted(x for x in root.rglob("*") if x.is_file()):
            h.update(p.relative_to(root).as_posix().encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:12]
