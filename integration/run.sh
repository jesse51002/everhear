#!/usr/bin/env bash
# One command: the real bridge, Jev filter and vfg toolkit (plus the client mod's HTTP
# client and the conversation's hooks) run together on this PC, in a temp folder that
# stands in for EC2's /opt/vfg and /opt/mc. Prints PASS/FAIL per check and per path;
# exits non-zero if anything failed. --keep keeps the temp folder and its logs.
#
#   integration/run.sh [--keep]
#
# Python 3.9 (what Amazon Linux 2023 runs) is used for every program that runs on EC2;
# set VFG_EC2_PYTHON to choose another. The JDK for the mod path is found under
# ~/.local/share/jdk, or set VFG_JAVA. Nothing touches ~/.vfg, ~/.ssh or any remote host.
set -euo pipefail
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"

py39="${VFG_EC2_PYTHON:-}"
if [ -z "$py39" ] && [ -x "$HOME/.local/bin/uv" ]; then
  py39="$("$HOME/.local/bin/uv" python find 3.9 2>/dev/null || true)"
fi
if [ -z "$py39" ]; then
  py39="$(command -v python3)"
  echo "run.sh: no Python 3.9 found; the EC2 side runs on $("$py39" --version)" >&2
fi

exec /usr/bin/env python3 "$HERE/harness.py" --ec2-python "$py39" "$@"
