#!/usr/bin/env bash
# python.sh [VFG_DIR] -- on the vfg-mc box, as root: Python 3.12 for everything vfg runs there.
#
# uv, a pinned release checked against its sha256, into VFG_DIR/uv/; CPython 3.12, installed by
# uv, into VFG_DIR/uv/python/; and VFG_DIR/python a link to it, so VFG_DIR/python/bin/python3 is
# the one interpreter vfg's remote programs, the bridge and the Jev filter run on, and the
# game engine's venv is made from, the same version as the owner's PC. Amazon Linux's own
# python3 (3.9) stays for the host's tools. VFG_DIR defaults to /opt/vfg; provision.sh runs this as a step.
# Idempotent: run again, it changes nothing that is already right.
set -euo pipefail
shopt -s inherit_errexit

VFG=${1:-/opt/vfg}
UV_VERSION=0.11.17
PYTHON=3.12
# From the release's own uv-<arch>-unknown-linux-gnu.tar.gz.sha256 files,
# https://github.com/astral-sh/uv/releases/tag/0.11.17
declare -A UV_SHA256=(
  [x86_64]=0017ccecaeb4d431d7f93b583ebff0c5c38e00eb734fcf13d05f72ca419125fe
  [aarch64]=de008880a903ac2c5654647dc19a75c0d6652313c977a2bc5ce05e1e3a93429e
)

log() { printf 'python: %s\n' "$*"; }
die() { printf 'python: %s\n' "$*" >&2; exit 1; }

UV=$VFG/uv/uv
export UV_PYTHON_INSTALL_DIR=$VFG/uv/python UV_CACHE_DIR=$VFG/uv/cache UV_NO_CONFIG=1
install -d -m 755 "$VFG/uv"

# 1. uv.
if [[ $("$UV" --version 2>/dev/null || true) != "uv $UV_VERSION"* ]]; then
  arch=$(uname -m)
  sum=${UV_SHA256[$arch]:-}
  [[ -n $sum ]] || die "no uv build for $arch"
  name=uv-$arch-unknown-linux-gnu
  tmp=$(mktemp -d)
  trap 'rm -rf "$tmp"' EXIT
  log "downloading uv $UV_VERSION"
  curl -fsSL --retry 3 -o "$tmp/uv.tar.gz" \
    "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/$name.tar.gz"
  echo "$sum  $tmp/uv.tar.gz" | sha256sum -c --quiet - || die "uv's download does not match its sha256"
  tar xzf "$tmp/uv.tar.gz" -C "$tmp"
  install -m 755 "$tmp/$name/uv" "$UV.new"
  mv -f "$UV.new" "$UV"
fi

# 2. CPython 3.12, uv's build (its download checked by uv); nothing put on root's PATH. Readable and
#    runnable by every user: the engine's venv (ec2-user's) and the test server's sandboxed tests
#    (vfgtest's) run on it.
"$UV" python install --no-bin --quiet "$PYTHON"
chmod -R u+rwX,go+rX,go-w "$VFG/uv/python"
py=$("$UV" python find --managed-python --no-python-downloads "$PYTHON")
home=$(dirname "$(dirname "$py")")

# 3. The stable path: VFG_DIR/python, swapped in whole so no run ever sees it half made.
[[ -L $VFG/python || ! -e $VFG/python ]] || die "$VFG/python is there and is not a link"
if [[ $(readlink "$VFG/python" || true) != "$home" ]]; then
  ln -sfn "$home" "$VFG/python.new"
  mv -fT "$VFG/python.new" "$VFG/python"
  log "$VFG/python -> $home"
fi
"$VFG/python/bin/python3" -c 'import sys; sys.exit(sys.version_info[:2] != (3, 12))' \
  || die "$VFG/python/bin/python3 is not Python 3.12"
log "$("$VFG/python/bin/python3" --version) at $VFG/python/bin/python3"
