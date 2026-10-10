#!/usr/bin/env bash
# deploy.sh -- from the owner's PC, to the vfg-mc box over SSH (VFG_SSH_HOST in ~/.vfg/config).
#
#   deploy.sh provision [--restore]   the host, Python 3.12 in /opt/vfg/python (python.sh) and
#                                     Minecraft: runs provision.sh on the box as root.
#                                     --restore first sends mc-backup.tgz (and vfg-backup.tgz, if
#                                     there is one) from VFG_BACKUP_DIR. A new world needs
#                                     VFG_ACCEPT_EULA=true: you accept Minecraft's EULA
#                                     (https://aka.ms/MinecraftEULA) by setting it.
#   deploy.sh                         the bridge: vfg_feed.py, vfg_bridge/, tests/, the two vfg units;
#                                     applies the schema and restarts vfg-bridge (and vfg-jev if deployed).
#
# It never touches /opt/vfg/.env, the database, the feed, watch.json, lookout.md or /opt/mc
# (beyond what provision.sh does). The Jev filter's program has its own jev/deploy.sh.
set -euo pipefail
shopt -s inherit_errexit

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BRIDGE=$(dirname "$HERE")
# shellcheck source=lib.sh
. "$HERE/lib.sh"

usage() {
  printf 'usage: %s [provision [--restore]]\n' "${0##*/}" >&2
  exit 2
}

cmd_provision() {
  local restore=0
  case "${1-}" in
    --restore) restore=1 ;;
    "") ;;
    *) usage ;;
  esac
  [[ $# -le 1 ]] || usage
  ssh_setup

  if (( restore )); then
    [[ -f $BACKUP_DIR/mc-backup.tgz ]] || die "no $BACKUP_DIR/mc-backup.tgz to restore"
    log "sending mc-backup.tgz"
    push --chmod=F600 "$BACKUP_DIR/mc-backup.tgz" "$SSH_USER@$SSH_HOST:/tmp/mc-backup.tgz" \
      || die "could not send mc-backup.tgz"
    if [[ -f $BACKUP_DIR/vfg-backup.tgz ]]; then
      log "sending vfg-backup.tgz"
      push --chmod=F600 "$BACKUP_DIR/vfg-backup.tgz" "$SSH_USER@$SSH_HOST:/tmp/vfg-backup.tgz" \
        || die "could not send vfg-backup.tgz"
    fi
  fi

  log "sending provision.sh, python.sh, minecraft.service, rcon.py, env.example"
  remote 'rm -rf /tmp/vfg-provision && mkdir -m 700 /tmp/vfg-provision'
  push --chmod=F644 "$HERE/provision.sh" "$HERE/python.sh" "$HERE/systemd/minecraft.service" "$HERE/rcon.py" \
    "$HERE/env.example" "$SSH_USER@$SSH_HOST:/tmp/vfg-provision/" \
    || die "could not send the provisioning files"
  local args=""
  if (( restore )); then args+=" --restore"; fi
  if [[ $(vfg_config VFG_ACCEPT_EULA) == true ]]; then args+=" --accept-eula"; fi
  remote "sudo bash /tmp/vfg-provision/provision.sh$args"
}

cmd_deploy() {
  ssh_setup
  [[ -f $BRIDGE/vfg_feed.py && -d $BRIDGE/vfg_bridge ]] || die "run it from the repo: $BRIDGE"
  remote 'test -x /opt/vfg/python/bin/python3' \
    || die "the box has no /opt/vfg/python/bin/python3, which the units run on: deploy.sh provision first"

  log "sending vfg_feed.py to /opt/vfg/lib/"
  push --chmod=F644 "$BRIDGE/vfg_feed.py" "$SSH_USER@$SSH_HOST:/opt/vfg/lib/vfg_feed.py" \
    || die "could not send vfg_feed.py"
  log "sending vfg_bridge/ and tests/ to /opt/vfg/bridge/"
  push --recursive --delete --exclude='__pycache__/' --exclude='*.pyc' --chmod=D755,F644 \
    "$BRIDGE/vfg_bridge" "$BRIDGE/tests" "$SSH_USER@$SSH_HOST:/opt/vfg/bridge/" \
    || die "could not send the bridge"
  remote 'rm -rf /tmp/vfg-units && mkdir -m 700 /tmp/vfg-units'
  push --chmod=F644 "$HERE/systemd/vfg-bridge.service" "$HERE/systemd/vfg-jev.service" \
    "$SSH_USER@$SSH_HOST:/tmp/vfg-units/" || die "could not send the units"

  log "installing the units, applying the schema, restarting"
  remote 'bash -s' <<'REMOTE'
set -euo pipefail
sudo install -m 644 -o root -g root /tmp/vfg-units/vfg-bridge.service /tmp/vfg-units/vfg-jev.service \
  /etc/systemd/system/
rm -rf /tmp/vfg-units
sudo systemctl daemon-reload
sudo systemctl enable vfg-bridge vfg-jev
cd /opt/vfg
PYTHONPATH=/opt/vfg/lib:/opt/vfg/bridge /opt/vfg/python/bin/python3 -m vfg_bridge --init-db
sudo systemctl restart vfg-bridge
if [ -f /opt/vfg/jev/jev_filter.py ]; then
  sudo systemctl restart vfg-jev
fi
sleep 2
for unit in minecraft vfg-bridge vfg-jev; do
  printf '%-11s %s\n' "$unit" "$(systemctl is-active "$unit" || true)"
done
REMOTE
}

case "${1-}" in
  provision) shift; cmd_provision "$@" ;;
  "") cmd_deploy ;;
  -h|--help|*) usage ;;
esac
