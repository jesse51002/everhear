#!/usr/bin/env bash
# One command: the real bridge, Jev filter and vfg toolkit (plus the client mod's HTTP client and
# the brain's reader of the feed) run together on this PC, in a temp folder that stands in for the
# box's /opt/vfg and /opt/mc, driven the way the brain drives them (integration/harness.py; the
# paths are in integration/README.md). Prints PASS/FAIL per check and per path; exits non-zero if
# anything failed. --keep keeps the temp folder and its logs.
#
#   integration/run.sh [--keep]
#
# Python 3.12 (the box's, /opt/vfg/python) runs every program that runs on the box; set
# VFG_EC2_PYTHON to choose another. Path 8 needs a JDK (under ~/.local/share/jdk, or VFG_JAVA) and
# the mod's classes (cd client-mod && ./gradlew compileJava). No Minecraft, no game engine, no Jev
# account, no model; nothing touches ~/.vfg, ~/.ssh or any remote host.
set -euo pipefail
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"

box_py="${VFG_EC2_PYTHON:-}"
if [ -z "$box_py" ] && [ -x "$HOME/.local/bin/uv" ]; then
  box_py="$("$HOME/.local/bin/uv" python find 3.12 2>/dev/null || true)"
fi
if [ -z "$box_py" ]; then
  box_py="$(command -v python3)"
  echo "run.sh: no Python 3.12 found; the box's side runs on $("$box_py" --version)" >&2
fi

exec /usr/bin/env python3 "$HERE/harness.py" --ec2-python "$box_py" "$@"
