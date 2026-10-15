#!/bin/sh
# Run a command against an X display where the session has none.
#
#   scripts/with_display.sh python scripts/screenshot.py --out dist/shots/x.png --argv --fake
#
# A sandboxed session is refused socket(AF_UNIX), so the operator's DISPLAY does
# not answer and an ordinary Xvfb cannot listen. This starts one that listens on
# TCP only, inside a user and mount namespace: the tmpfs over /tmp is where the
# server keeps its lock file, and Mesa's software rasteriser draws.
#
# The tmpfs shadows the whole of /tmp, $TMPDIR included, and vanishes with the
# namespace. Write what is to be kept under the checkout; dist/ is ignored.
#
# The recipe is imgui-adv-text's (scripts/mouse_probe.py, xvfb_command).
set -eu

if [ "$#" -eq 0 ]; then
    echo "usage: $0 command [args...]" >&2
    exit 2
fi

DISPLAY_NUMBER="${WITH_DISPLAY_NUMBER:-77}"
SCREEN="${WITH_DISPLAY_SCREEN:-1400x1000x24}"
export DISPLAY_NUMBER SCREEN

exec unshare -rm sh -eu -c '
mount -t tmpfs tmpfs /tmp
mkdir -p /tmp/.X11-unix /tmp/cache
Xvfb ":$DISPLAY_NUMBER" -screen 0 "$SCREEN" -listen tcp -nolisten unix >/tmp/xvfb.log 2>&1 &
XVFB=$!
trap "kill $XVFB 2>/dev/null || true" EXIT
export DISPLAY="127.0.0.1:$DISPLAY_NUMBER" TMPDIR=/tmp
export LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe
export XDG_CACHE_HOME=/tmp/cache XDG_CONFIG_HOME=/tmp/cache
unset WAYLAND_DISPLAY XAUTHORITY
port=$((6000 + DISPLAY_NUMBER))
tries=0
until python3 -c "import socket,sys; socket.create_connection((\"127.0.0.1\", $port), 0.2)" 2>/dev/null; do
    tries=$((tries + 1))
    if [ "$tries" -gt 50 ] || ! kill -0 $XVFB 2>/dev/null; then
        echo "Xvfb did not answer on 127.0.0.1:$port:" >&2
        cat /tmp/xvfb.log >&2
        exit 1
    fi
    sleep 0.1
done
STATUS=0
"$@" || STATUS=$?
exit $STATUS
' with_display "$@"
