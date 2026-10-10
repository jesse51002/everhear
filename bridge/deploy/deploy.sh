#!/usr/bin/env bash
# deploy.sh -- from the owner's PC, to the vfg-mc box over SSH (VFG_SSH_HOST in ~/.vfg/config).
#
#   deploy.sh provision [--restore]   the host and Minecraft: runs provision.sh on the box as root.
#                                     --restore first sends mc-backup.tgz (and vfg-backup.tgz, if
#                                     there is one) from VFG_BACKUP_DIR. A new world needs
#                                     VFG_ACCEPT_EULA=true: you accept Minecraft's EULA
#                                     (https://aka.ms/MinecraftEULA) by setting it.
#                                     Then it sends the box's secrets, as env does.
#   deploy.sh env                     the box's secrets: the names env.example lists, from the file
#                                     VFG_BOX_ENV names (default ~/.vfg/box.env), into /opt/vfg/.env;
#                                     a client token made on the box if there is still none; restarts
#                                     the vfg units that are installed.
#   deploy.sh                         the bridge: vfg_feed.py, vfg_bridge/, tests/, the two vfg units;
#                                     applies the schema and restarts vfg-bridge (and vfg-jev if deployed).
#
# Only provision and env touch /opt/vfg/.env; none touches the database, the feed, watch.json,
# lookout.md or /opt/mc (beyond what provision.sh does). The Jev filter's program has its own
# jev/deploy.sh.
set -euo pipefail
shopt -s inherit_errexit

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BRIDGE=$(dirname "$HERE")
# shellcheck source=lib.sh
. "$HERE/lib.sh"

usage() {
  printf 'usage: %s [provision [--restore] | env]\n' "${0##*/}" >&2
  exit 2
}

# Runs on the box: KEY=value lines on stdin, the names to take in argv. Merges every non-empty
# value into /opt/vfg/.env (mode 600), makes VFG_CLIENT_TOKEN if it is still empty, and prints
# names only, never a value.
read -r -d '' MERGE_ENV <<'PY' || true
import os, re, secrets, sys
names = sys.argv[1:]
path = "/opt/vfg/.env"
line_re = re.compile(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")

def unquote(v):
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v

vals = {}
for raw in sys.stdin.read().splitlines():
    m = line_re.match(raw)
    if m and m.group(1) in names and unquote(m.group(2)):
        vals[m.group(1)] = unquote(m.group(2))
try:
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
except FileNotFoundError:
    lines = []
have = {}
for old in lines:
    m = line_re.match(old)
    if m:
        have[m.group(1)] = unquote(m.group(2))
vals = {k: v for k, v in vals.items() if have.get(k) != v}
if "VFG_CLIENT_TOKEN" in names and not have.get("VFG_CLIENT_TOKEN") and "VFG_CLIENT_TOKEN" not in vals:
    vals["VFG_CLIENT_TOKEN"] = secrets.token_urlsafe(32)
    print("box-env: made a new VFG_CLIENT_TOKEN")

def entry(name, value):
    if re.fullmatch(r"[A-Za-z0-9._~+/=:@%,-]+", value):
        return "%s=%s" % (name, value)
    # systemd EnvironmentFile: double quotes, with \ and " escaped
    return '%s="%s"' % (name, value.replace("\\", "\\\\").replace('"', '\\"'))

out, done = [], set()
for old in lines:
    m = line_re.match(old)
    if m and m.group(1) in vals:
        if m.group(1) not in done:
            out.append(entry(m.group(1), vals[m.group(1)]))
            done.add(m.group(1))
        continue
    out.append(old)
out += [entry(k, v) for k, v in vals.items() if k not in done]
if vals:
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
print("box-env: %s" % ("set " + " ".join(sorted(vals)) if vals else "nothing new"))
have.update(vals)
empty = [n for n in names if not have.get(n)]
if empty:
    print("box-env: still empty: %s" % " ".join(empty))
PY

# send_env -- the names env.example lists, from VFG_BOX_ENV, into /opt/vfg/.env on the box. The
# file may be any .env: its other keys never leave this PC. Values go over SSH on stdin.
send_env() {
  local file names alt
  file=$(vfg_config VFG_BOX_ENV "$HOME/.vfg/box.env")
  names=$(sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' "$HERE/env.example" | tr '\n' ' ')
  alt=${names% }
  alt=${alt// /|}
  if [[ -f $file ]]; then
    [[ -r $file ]] || die "cannot read $file (VFG_BOX_ENV)"
    log "sending ${names% } from $file, if set there"
  else
    log "no $file (VFG_BOX_ENV): only a client token, if the box has none"
    file=/dev/null
  fi
  { grep -E "^[[:space:]]*(export[[:space:]]+)?($alt)[[:space:]]*=" "$file" || true; } \
    | remote "python3 -c $(printf '%q' "$MERGE_ENV") $names"
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

  log "sending provision.sh, minecraft.service, rcon.py, env.example"
  remote 'rm -rf /tmp/vfg-provision && mkdir -m 700 /tmp/vfg-provision'
  push --chmod=F644 "$HERE/provision.sh" "$HERE/systemd/minecraft.service" "$HERE/rcon.py" \
    "$HERE/env.example" "$SSH_USER@$SSH_HOST:/tmp/vfg-provision/" \
    || die "could not send the provisioning files"
  local args=""
  if (( restore )); then args+=" --restore"; fi
  if [[ $(vfg_config VFG_ACCEPT_EULA) == true ]]; then args+=" --accept-eula"; fi
  remote "sudo bash /tmp/vfg-provision/provision.sh$args"
  send_env
}

cmd_env() {
  ssh_setup
  send_env
  remote 'bash -s' <<'REMOTE'
set -euo pipefail
if [ -f /etc/systemd/system/vfg-bridge.service ]; then
  sudo systemctl restart vfg-bridge
fi
if [ -f /opt/vfg/jev/jev_filter.py ]; then
  sudo systemctl restart vfg-jev
fi
sleep 2
for unit in minecraft vfg-bridge vfg-jev; do
  printf '%-11s %s\n' "$unit" "$(systemctl is-active "$unit" || true)"
done
REMOTE
}

cmd_deploy() {
  ssh_setup
  [[ -f $BRIDGE/vfg_feed.py && -d $BRIDGE/vfg_bridge ]] || die "run it from the repo: $BRIDGE"

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
PYTHONPATH=/opt/vfg/lib:/opt/vfg/bridge python3 -m vfg_bridge --init-db
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
  env) shift; [[ $# -eq 0 ]] || usage; cmd_env ;;
  "") cmd_deploy ;;
  -h|--help|*) usage ;;
esac
