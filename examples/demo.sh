#!/bin/sh
# Draw examples/slime.lua into a file one step at a time, so you can watch it.
#   ./examples/demo.sh [out.aseprite] [seconds between steps]
out="$(realpath -m "${1:-slime.aseprite}")"
pause="${2:-0.8}"
aseprite="${ASEPRITE_PATH:-aseprite}"
lua="$(dirname "$(readlink -f "$0")")/slime.lua"
for step in 1 2 3 4 5 6 7 8 9 10; do
  "$aseprite" -b --script-param out="$out" --script-param step=$step --script "$lua" || exit 1
  echo "step $step -> $out"
  sleep "$pause"
done
