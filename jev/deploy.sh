#!/usr/bin/env bash
# Deploy vfg-jev from the owner's PC to EC2: stream this folder as a tar over SSH
# into /opt/vfg/jev/ (without tests/ and __pycache__/), restart the unit, and
# print whether it is active.
#
#   jev/deploy.sh
#
# Reads VFG_SSH_HOST, VFG_SSH_USER (default ec2-user) and VFG_SSH_KEY from ~/.vfg/config
# (KEY=value lines); an environment variable of the same name wins (§8).
# The systemd unit vfg-jev itself is written by the server-and-bridge spec.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${VFG_CONFIG:-$HOME/.vfg/config}"

die() { echo "deploy: $*" >&2; exit 1; }

from_config() {   # from_config NAME -> the value in $CONFIG, or nothing
  [ -r "$CONFIG" ] || return 0
  local line name value
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line#"${line%%[![:space:]]*}"}"
    case "$line" in ''|'#'*) continue ;; esac
    line="${line#export }"
    name="${line%%=*}"
    [ "$name" = "$1" ] || continue
    value="${line#*=}"
    value="${value%"${value##*[![:space:]]}"}"
    case "$value" in \"*\"|\'*\') value="${value:1:${#value}-2}" ;; esac
    printf '%s' "$value"
    return 0
  done < "$CONFIG"
}

HOST="${VFG_SSH_HOST:-$(from_config VFG_SSH_HOST)}"
USER_="${VFG_SSH_USER:-$(from_config VFG_SSH_USER)}"
USER_="${USER_:-ec2-user}"
KEY="${VFG_SSH_KEY:-$(from_config VFG_SSH_KEY)}"
KEY="${KEY/#\~/$HOME}"

[ -n "$HOST" ]  || die "VFG_SSH_HOST is not set (in $CONFIG or the environment)"
[ -n "$KEY" ]   || die "VFG_SSH_KEY is not set (in $CONFIG or the environment)"
[ -r "$KEY" ]   || die "cannot read the SSH key named by VFG_SSH_KEY"
[ -f "$HERE/jev_filter.py" ] || die "run this from the jev folder's own copy"

SSH=(ssh -i "$KEY" -o BatchMode=yes -o IdentitiesOnly=yes -o ConnectTimeout=10
     -o StrictHostKeyChecking=accept-new "$USER_@$HOST")

# Unpack beside the live copy, then swap, so a deploy cut off halfway leaves
# the old copy running and no file from an older deploy lingers.
REMOTE='set -e
mkdir -p /opt/vfg
rm -rf /opt/vfg/jev.new /opt/vfg/jev.old
mkdir /opt/vfg/jev.new
tar -xzf - -C /opt/vfg/jev.new
if [ -d /opt/vfg/jev ]; then mv /opt/vfg/jev /opt/vfg/jev.old; fi
mv /opt/vfg/jev.new /opt/vfg/jev
rm -rf /opt/vfg/jev.old
echo "copied to /opt/vfg/jev"
sudo systemctl restart vfg-jev || { echo "restart failed: is the vfg-jev unit installed?" >&2; exit 1; }
sleep 2
systemctl is-active vfg-jev'

echo "deploy: $HERE -> $USER_@$HOST:/opt/vfg/jev"
tar -C "$HERE" --exclude=./tests --exclude=__pycache__ --exclude='*.pyc' -czf - . \
  | "${SSH[@]}" "$REMOTE"
