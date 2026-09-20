# PlatformIO pre-script for a -DFF_GENERIC_PANEL env (W-819): bake the env's
# own screens header if it is not there yet. The header is generated, not
# committed — `custom_bake` in the env is the bake_screens.py command line.
#
# The bake draws with the server's typography, so it needs the server's
# Python (numpy, Pillow): server/.venv if there is one, else FF_BAKE_PYTHON,
# else whatever `python3` is.
import os
import subprocess
import sys

Import("env")  # noqa: F821  (PlatformIO injects it)

firmware = env.subst("$PROJECT_DIR")  # noqa: F821
repo = os.path.dirname(firmware)
args = env.GetProjectOption("custom_bake", "").split()  # noqa: F821
if "--out" not in args:
    sys.exit("bake_generic.py: the env needs custom_bake = --size WxH ... --out src/<header>")
out = os.path.join(firmware, args[args.index("--out") + 1])
args[args.index("--out") + 1] = out

if not os.path.exists(out):
    venv = os.path.join(repo, "server", ".venv", "bin", "python")
    python = os.environ.get("FF_BAKE_PYTHON") or (venv if os.path.exists(venv) else "python3")
    bake = os.path.join(firmware, "tools", "screens", "bake_screens.py")
    print("baking", os.path.relpath(out, firmware), "with", python)
    if subprocess.call([python, bake] + args) != 0:
        sys.exit("bake_generic.py: the bake failed (it needs the server's Python: "
                 "`make venv`, or point FF_BAKE_PYTHON at one with numpy and Pillow)")
