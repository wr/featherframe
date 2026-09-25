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
#   site/scripts/screens.sh [wall|hero|all]   (needs server/.venv and the
#       plates; FF_SERVER=<a checkout's server/> to use another one)
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
server="$(cd "${FF_SERVER:-$here/../server}" && pwd)"
out="$here/public/models/screens"
render="$server/../test_output"
which="${1:-all}"
HERO=(
  "nighthawk|Common Nighthawk"
  "cardinal|Northern Cardinal"
  "blue-jay|Blue Jay"
  "goldfinch|American Goldfinch"
)
SPECIES=(
  "flamingo|American Flamingo"
  "blue-jay|Blue Jay"
  "common-kingfisher|Common Kingfisher"
  "cardinal|Northern Cardinal"
  "kookaburra|Laughing Kookaburra"
  "bee-eater|European Bee-eater"
  "snowy-owl|Snowy Owl"
  "lorikeet|Rainbow Lorikeet"
  "carolina-parakeet|Carolina Parakeet"
  "hoopoe|Eurasian Hoopoe"
  "roller|European Roller"
  "baltimore-oriole|Baltimore Oriole"
)
export FEATHERFRAME_NO_MDNS=1
render_one() { # slug, name, output prefix
  local slug="$1" name="$2" prefix="$3"
  local file; file="$(echo "$name" | tr 'A-Z ' 'a-z_').png"
  for size in 10 13; do
    panel=ee03; [ "$size" = 13 ] && panel=ee02
    (cd "$server" && ./.venv/bin/python -m featherframe.preview --species "$name" --panel "$panel" >/dev/null)
    "$server/.venv/bin/python" - "$render/$file" "$out/$prefix$size-$slug.jpg" "$size" <<'PY'
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
  for entry in "${SPECIES[@]}"; do render_one "${entry%%|*}" "${entry#*|}" wall-; done
fi
if [ "$which" != wall ]; then
  for entry in "${HERO[@]}"; do render_one "${entry%%|*}" "${entry#*|}" ""; done
fi
