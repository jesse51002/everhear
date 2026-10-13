#!/usr/bin/env bash
# install.sh -- on the box, as ec2-user: the game engine's venv and its datapack. bridge/deploy/
# deploy.sh sends vfg_engine/, datapack/everhear/ and this folder to /opt/vfg/engine/ and runs it;
# then it installs vfg-engine.service and restarts the engine.
#
# 1. /opt/vfg/engine/.venv, on the box's Python 3.12 (/opt/vfg/python, python.sh's) with the box's
#    uv (/opt/vfg/uv/uv): made when it is missing or that Python changed under it, then synced to
#    requirements.txt exactly, every file checked against its hash, wheels only.
# 2. The datapack: /opt/vfg/engine/datapack/everhear copied over /opt/mc/world/datapacks/everhear
#    when they differ, and then loaded with /reload over RCON (/opt/mc/rcon.py, which holds the
#    box's RCON lock), and enabled if the world had it disabled. With Minecraft down, it loads when
#    the server starts. Unchanged, nothing is sent to Minecraft.
#
# Idempotent: run again, it changes nothing that is already right. VFG_DIR, MC_DIR and UV override
# /opt/vfg, /opt/mc and the uv used (a stand-in box, such as integration/realmc's). Never prints the
# RCON password.
set -euo pipefail
shopt -s inherit_errexit

VFG=${VFG_DIR:-/opt/vfg}
MC=${MC_DIR:-/opt/mc}
ENGINE=$VFG/engine
UV=${UV:-$VFG/uv/uv}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PACK=everhear

log() { printf 'engine: %s\n' "$*"; }
die() { printf 'engine: %s\n' "$*" >&2; exit 1; }

[[ -x $UV ]] || die "no uv at $UV: run deploy.sh provision first"
[[ -x $VFG/python/bin/python3 ]] || die "no $VFG/python/bin/python3: run deploy.sh provision first"
[[ -f $HERE/requirements.txt ]] || die "requirements.txt is missing beside install.sh"
[[ -d $ENGINE/datapack/$PACK ]] || die "the datapack is missing: $ENGINE/datapack/$PACK"
export UV_NO_CONFIG=1 UV_PYTHON_DOWNLOADS=never UV_CACHE_DIR=$ENGINE/.uv-cache

# 1. The venv.
python_home=$(readlink -f "$VFG/python")
if [[ ! -x $ENGINE/.venv/bin/python || $(cat "$ENGINE/.venv/.vfg-python" 2>/dev/null || true) != "$python_home" ]]; then
  log "making $ENGINE/.venv on $("$VFG/python/bin/python3" --version)"
  rm -rf "$ENGINE/.venv"
  "$UV" venv --quiet --python "$VFG/python/bin/python3" "$ENGINE/.venv"
  printf '%s\n' "$python_home" > "$ENGINE/.venv/.vfg-python"
fi
"$UV" pip sync --quiet --python "$ENGINE/.venv/bin/python" --require-hashes --no-build "$HERE/requirements.txt"
"$ENGINE/.venv/bin/python" -c 'import sys, pydantic; sys.exit(sys.version_info[:2] != (3, 12))' \
  || die "$ENGINE/.venv is not Python 3.12 with pydantic"
log "venv: $("$ENGINE/.venv/bin/python" -c 'import pydantic; print("pydantic", pydantic.VERSION)')"

# 2. The datapack.
rcon() { python3 "$MC/rcon.py" "$@"; }
dest=$MC/world/datapacks/$PACK
if diff -rq "$ENGINE/datapack/$PACK" "$dest" > /dev/null 2>&1; then
  log "datapack: $dest is up to date"
else
  mkdir -p "$MC/world/datapacks"
  rsync --recursive --delete --checksum --chmod=D755,F644 "$ENGINE/datapack/$PACK/" "$dest/"
  log "datapack: copied to $dest"
  if ! reply=$(rcon reload 2>&1); then
    log "datapack: Minecraft is not answering RCON ($(tail -n 1 <<< "$reply")); it loads when the server starts"
  else
    enabled=$(rcon datapack list enabled 2>&1 || true)
    if [[ $enabled != *"file/$PACK"* ]]; then
      rcon "datapack enable \"file/$PACK\"" > /dev/null 2>&1 || true
      enabled=$(rcon datapack list enabled 2>&1 || true)
    fi
    if [[ $enabled == *"file/$PACK"* ]]; then
      log "datapack: reloaded, file/$PACK enabled"
    else
      die "datapack: reloaded, but file/$PACK is not enabled: $enabled"
    fi
  fi
fi
