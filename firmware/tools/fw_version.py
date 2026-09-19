# PlatformIO pre-build hook: stamp the sketch with a git-derived build identity.
#
# Injects -D FF_FW_VERSION="YYYY.MM.DD+<shortsha>[+dirty]" — or, on a clean
# checkout of a release tag (v1.2.0), just "1.2.0" — so the running frame
# can report exactly which build is on the wall (spec: docs/firmware-device-stats.md
# §1). Computed from git here rather than from ${sysenv.FF_FW_VERSION} in
# platformio.ini because an unset sysenv var expands to an empty string and would
# *define* FF_FW_VERSION as "" — silently defeating the #ifndef fallback in
# ff_config.h. Deriving it from git means a bare `pio run` is always stamped, with
# no env coordination; the ff_config.h fallback ("dev") only ever applies when git
# is unavailable.
Import("env")  # noqa: F821  (injected by PlatformIO/SCons)
import os
import subprocess


def _git(*args):
    try:
        return subprocess.check_output(
            ["git", *args], stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        return ""


sha = _git("rev-parse", "--short", "HEAD")
date = _git("show", "-s", "--format=%cd", "--date=format:%Y.%m.%d", "HEAD")

if sha:
    dirty = "" if _git("status", "--porcelain") == "" else "+dirty"
    tag = _git("describe", "--tags", "--exact-match", "--match", "v[0-9]*", "HEAD")
    if tag and not dirty:
        version = tag[1:]
    else:
        version = f"{date}+{sha}{dirty}" if date else f"{sha}{dirty}"
else:
    version = "dev"

env.Append(CPPDEFINES=[("FF_FW_VERSION", env.StringifyMacro(version))])
print(f"FF_FW_VERSION = {version}")

# A per-unit battery trim for a bench build (see VBAT_TRIM in ff_config.h).
# Never set for a release: one binary ships to every unit.
trim = os.environ.get("FF_VBAT_TRIM", "").strip()
if trim:
    env.Append(CPPDEFINES=[("VBAT_TRIM", f"{float(trim):.4f}f")])
    print(f"VBAT_TRIM = {float(trim):.4f}")
