#!/bin/sh
# Run scripts/bundle193_probe.py against imgui-bundle 1.93.0 and 1.92.900, each
# in its own scratch venv under $DIR, never this checkout's .venv.
#
#   scripts/bundle193_run.sh [DIR]      (default /tmp/claude-1000/b193)
#
# Needs network (pip) and xvfb-run, so it is an operator-run step. Writes only
# under DIR: venvs v193/ and v192/, out-v193.jsonl, out-v192.jsonl,
# mermaid.jsonl, and PNGs under out-v193/ and out-v192/.
set -u

ROOT=$(cd "$(dirname "$0")/.." && pwd)
DIR=${1:-/tmp/claude-1000/b193}
PROBE="$ROOT/scripts/bundle193_probe.py"
XVFB="xvfb-run -a -s"
SCREEN="-screen 0 1600x1200x24"
mkdir -p "$DIR"

for pair in v193:1.93.0 v192:1.92.900; do
    v=${pair%%:*}
    ver=${pair#*:}
    [ -x "$DIR/$v/bin/python" ] || /usr/bin/python3.11 -m venv "$DIR/$v"
    "$DIR/$v/bin/pip" install -q --no-cache-dir "imgui-bundle==$ver" numpy
    "$DIR/$v/bin/python" "$PROBE" all --out "$DIR/out-$v" \
        --scan "$ROOT/pptmstr" "$HOME/Source/imscribe/imscribe" >"$DIR/out-$v.jsonl" 2>&1
    $XVFB "$SCREEN" "$DIR/$v/bin/python" "$PROBE" shots --out "$DIR/out-$v" >>"$DIR/out-$v.jsonl" 2>&1
done

$XVFB "$SCREEN" "$DIR/v193/bin/python" "$PROBE" mermaid --out "$DIR/out-v193" >"$DIR/mermaid.jsonl" 2>&1

ls "$DIR/out-v193" "$DIR/out-v192"
grep -c '^@@' "$DIR/out-v193.jsonl" "$DIR/out-v192.jsonl" "$DIR/mermaid.jsonl"
grep -h 'Traceback\|Error' "$DIR"/*.jsonl | head -20
