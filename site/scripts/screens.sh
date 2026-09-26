#!/usr/bin/env bash
# The gallery wall's screen textures: each species as the frame itself draws
# it, rendered by the Featherframe server's own preview (16 grays for the
# 10-inch, the six-ink EE02 render for the 13-inch), then scaled to the
# model's screen texture (13: 1543 × 2072, 10: 1179 × 1572) the way
# public/models/screens/*.jpg are: fitted by width, paper above and below.
#
# The hero's four (models/screens/{10,13}-<slug>.jpg) come the same way, so
# every screen on the page carries the same corner mark.
#
#   site/scripts/screens.sh [wall|hero|all] [slug]   (a slug renders that one alone; needs server/.venv and the
#       plates; FF_SERVER=<a checkout's server/> to use another one, FF_PYTHON=<a python with
#       its requirements> when that checkout has no .venv, FEATHERFRAME_PLATES_DIR=<plates/> when
#       it has no plates)
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
server="$(cd "${FF_SERVER:-$here/../server}" && pwd)"
out="$here/public/models/screens"
render="$server/../test_output"
py="${FF_PYTHON:-$server/.venv/bin/python}"
which="${1:-all}"
only="${2:-}"
HERO=(
  "nighthawk|Common Nighthawk"
  "cardinal|Northern Cardinal"
  "eastern-bluebird|Eastern Bluebird"
  "goldfinch|American Goldfinch"
)
# slug|name, or slug|name|latin|scan|Havell plate for a species the server's
# plate index does not carry (drawn from its scan by scan_screen.py)
SPECIES=(
  "wild-turkey|Wild Turkey"
  "blue-jay|Blue Jay"
  "great-horned-owl|Great Horned Owl"
  "cedar-waxwing|Cedar Waxwing"
  "green-breasted-mango|Green-breasted Mango|Anthracothorax prevostii|plate-184-mango-hummingbird.jpg|184"
  "tufted-titmouse|Tufted Titmouse"
  "kookaburra|Laughing Kookaburra"
  "hermit-thrush|Hermit Thrush"
  "saw-whet-owl|Northern Saw-whet Owl"
  "gray-catbird|Gray Catbird"
  "swainsons-warbler|Swainson's Warbler"
  "carolina-wren|Carolina Wren"
)
export FEATHERFRAME_NO_MDNS=1
render_one() { # slug, name, output prefix[, latin, scan, plate]
  local slug="$1" name="$2" prefix="$3" latin="${4:-}" scan="${5:-}" plate="${6:-}"
  [ -n "$only" ] && [ "$only" != "$slug" ] && return 0
  local file; file="$(echo "$name" | tr 'A-Z ' 'a-z_').png"
  for size in 10 13; do
    panel=ee03; [ "$size" = 13 ] && panel=ee02
    if [ -n "$scan" ]; then
      (cd "$server" && "$py" "$here/scripts/scan_screen.py" "$name" "$latin" "$scan" "$plate" "$panel" >/dev/null)
    else
      (cd "$server" && "$py" -m featherframe.preview --species "$name" --panel "$panel" >/dev/null)
    fi
    "$py" - "$render/$file" "$out/$prefix$size-$slug.jpg" "$size" <<'PY'
import sys
from PIL import Image
src, dst, size = sys.argv[1], sys.argv[2], sys.argv[3]
w, h = (1543, 2072) if size == "13" else (1179, 1572)
im = Image.open(src).convert("RGB")
fitted = im.resize((w, round(im.height * w / im.width)), Image.LANCZOS)
sheet = Image.new("RGB", (w, h), (255, 255, 255))
sheet.paste(fitted, (0, (h - fitted.height) // 2))
sheet.save(dst, quality=84, progressive=True)
print(dst.rsplit("/", 1)[1])
PY
  done
}
if [ "$which" != hero ]; then
  for entry in "${SPECIES[@]}"; do
    IFS='|' read -r slug name latin scan plate <<<"$entry"
    render_one "$slug" "$name" wall- "$latin" "$scan" "$plate"
  done
fi
if [ "$which" != wall ]; then
  for entry in "${HERO[@]}"; do render_one "${entry%%|*}" "${entry#*|}" ""; done
fi
