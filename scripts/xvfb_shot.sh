#!/usr/bin/env bash
# Take a screenshot of the app on a private Xvfb, for a machine or sandbox where
# no display is reachable.
#
#   scripts/xvfb_shot.sh --focus BOARD --out shots/board.png
#   scripts/xvfb_shot.sh --out shots/x.png --argv --fake --layout focus
#   scripts/xvfb_shot.sh -- .venv/bin/python scripts/verify_fonts.py
#
# Arguments go to scripts/screenshot.py unchanged. After a leading `--` they are
# instead a whole command to run against the display, for a verify script that
# opens its own window. Paths are relative to the repository root, and output
# has to land under it: see the tmpfs note below.
#
# Each link is here because the plainer one fails:
#
#   unshare -rm    Xvfb wants to write a lock file in /tmp and a sandbox may not
#                  let it. A user+mount namespace gives it a tmpfs of its own.
#                  That tmpfs shadows the caller's /tmp, $TMPDIR included, so a
#                  PNG written there vanishes with the namespace.
#   TCP, not unix  A sandbox that refuses socket(AF_UNIX) leaves Xvfb nothing to
#                  listen on, which is also why `xvfb-run` hangs there. The
#                  display is named with a host, which Xlib reads as TCP port
#                  6000+N.
#   a probed N     The network is NOT unshared, so two of these running at once
#                  share the port space. `-displayfd` would pick a free number
#                  but insists on the unix transport, so the number is chosen
#                  here: skip a port that already answers, and move on when the
#                  server dies at bind. XVFB_SHOT_DISPLAY=N sets where the
#                  search starts.
#   HOME=/tmp/...  hello_imgui restores window geometry and docking from the
#                  user config folder; a throwaway HOME makes every shot start
#                  from the app's defaults and keeps the operator's settings out
#                  of the picture. PPTMSTR_SHOT_HOME=dir keeps one instead.
#   llvmpipe       Xvfb has no GPU; Mesa's software rasteriser gives the 3.3
#                  core context, on the 24-bit screen asked for below.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [ "${1:-}" = "--" ]; then
  shift
  [ $# -gt 0 ] || { echo "usage: $0 -- COMMAND [ARG...]" >&2; exit 2; }
else
  set -- "$ROOT/.venv/bin/python" "$ROOT/scripts/screenshot.py" "$@"
fi

for tool in unshare Xvfb; do
  command -v "$tool" >/dev/null 2>&1 || { echo "$0: $tool is not installed" >&2; exit 1; }
done

export XVFB_SHOT_ROOT="$ROOT"
export XVFB_SHOT_SCREEN="${XVFB_SHOT_SCREEN:-1600x1000x24}"
export XVFB_SHOT_HOME="${PPTMSTR_SHOT_HOME:-/tmp/home}"
export XVFB_SHOT_DISPLAY="${XVFB_SHOT_DISPLAY:-}"

# shellcheck disable=SC2016  # expanded by the inner shell, inside the namespace
exec unshare -rm bash -c '
set -euo pipefail
mount -t tmpfs tmpfs /tmp
mkdir -p /tmp/.X11-unix /tmp/home "$XVFB_SHOT_HOME"

answers() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

XVFB=""
trap '"'"'[ -z "$XVFB" ] || kill "$XVFB" 2>/dev/null || true'"'"' EXIT

# Start from a number derived from the pid so concurrent runs rarely meet.
first="${XVFB_SHOT_DISPLAY:-$((100 + $$ % 400))}"
display=""
for n in $(seq "$first" $((first + 20))); do
  port=$((6000 + n))
  answers "$port" && continue
  Xvfb ":$n" -screen 0 "$XVFB_SHOT_SCREEN" -listen tcp -nolisten unix >"/tmp/xvfb.$n.log" 2>&1 &
  XVFB=$!
  for _ in $(seq 200); do
    kill -0 "$XVFB" 2>/dev/null || break
    if answers "$port"; then
      # The port answering is not proof it is ours: a concurrent run may have
      # bound it first, in which case this server is about to exit.
      sleep 0.2
      kill -0 "$XVFB" 2>/dev/null && display="$n"
      break
    fi
    sleep 0.05
  done
  [ -n "$display" ] && break
  kill "$XVFB" 2>/dev/null || true
  XVFB=""
done
if [ -z "$display" ]; then
  echo "xvfb_shot: no Xvfb came up on displays $first..$((first + 20)):" >&2
  cat /tmp/xvfb.*.log >&2 2>/dev/null || true
  exit 1
fi

export DISPLAY="127.0.0.1:$display" TMPDIR=/tmp HOME="$XVFB_SHOT_HOME"
export XDG_CACHE_HOME="$HOME/.cache" XDG_CONFIG_HOME="$HOME/.config"
export LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe
cd "$XVFB_SHOT_ROOT"
status=0
"$@" || status=$?
kill -0 "$XVFB" 2>/dev/null || { echo "xvfb_shot: Xvfb died during the run:" >&2; cat "/tmp/xvfb.$display.log" >&2; }
exit $status
' xvfb_shot "$@"
