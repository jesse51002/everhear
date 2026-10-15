#!/usr/bin/env bash
# deploy.sh -- from the owner's PC, to the vfg-mc box over SSH (VFG_SSH_HOST in ~/.vfg/config).
#
#   deploy.sh provision [--restore]   the host, Python 3.12 in /opt/vfg/python (python.sh) and
#                                     Minecraft: runs provision.sh on the box as root.
#                                     --restore first sends mc-backup.tgz (and vfg-backup.tgz, if
#                                     there is one) from VFG_BACKUP_DIR. A new world needs
#                                     VFG_ACCEPT_EULA=true: you accept Minecraft's EULA
#                                     (https://aka.ms/MinecraftEULA) by setting it.
#                                     Then it sends the box's secrets, as env does.
#   deploy.sh testserver [--fresh]    the test server beside the live one, for implementers' tests:
#                                     /opt/mc-test (RCON 127.0.0.1:25576), /opt/vfg-test, the unit
#                                     minecraft-test (testserver.sh, as provision does on a new box);
#                                     --fresh makes its template world again. Plain deploy.sh keeps
#                                     it current too. It never restarts the live server.
#   deploy.sh env                     the box's secrets: the names env.example lists, from the file
#                                     VFG_BOX_ENV names (default ~/.vfg/box.env), into /opt/vfg/.env;
#                                     a client token made on the box if there is still none; restarts
#                                     the vfg units that are installed.
#   deploy.sh                         the bridge: vfg_feed.py, vfg_bridge/, tests/, the three vfg units;
#                                     the game engine, as `engine` does; applies the schema and
#                                     restarts vfg-engine and vfg-bridge. When Jev is on the box, it
#                                     is stopped before the schema and deployed from this checkout
#                                     after (jev/deploy.sh): the CHECK on Jev's classes is the
#                                     bridge's, so the two never run apart. Then the test server is
#                                     kept current, as `testserver` does.
#   deploy.sh engine                  the game engine alone: engine/vfg_engine/, engine/library/ (the
#                                     server's base content) and engine/examples/ to
#                                     /opt/vfg/engine/, its venv (/opt/vfg/engine/.venv, pydantic
#                                     pinned by hash) and its datapack (into /opt/mc/world/datapacks/,
#                                     loaded over RCON when it changed) by engine/deploy/install.sh,
#                                     and vfg-engine.service; restarts vfg-engine, which saves its
#                                     state as it stops. A test server whose world lacks that
#                                     datapack gets a fresh world with it before its next test.
#
# Only provision and env touch /opt/vfg/.env; none touches the database (beyond the schema), the
# feed, watch.json, lookout.md, menu.md, the engine's state or /opt/mc (beyond what provision.sh
# does and the engine's datapack). The Jev filter's program has its own jev/deploy.sh, which this
# one runs when Jev is on the box.
set -euo pipefail
shopt -s inherit_errexit

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BRIDGE=$(dirname "$HERE")
ENGINE=$(dirname "$BRIDGE")/engine
# shellcheck source=lib.sh
. "$HERE/lib.sh"

usage() {
  printf 'usage: %s [provision [--restore] | env | engine | testserver [--fresh]]\n' "${0##*/}" >&2
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

# send_testserver [--fresh] -- testserver.sh, testserver.py and minecraft-test.service to
# /tmp/vfg-testserver/ on the box, then testserver.sh there as root.
send_testserver() {
  remote 'rm -rf /tmp/vfg-testserver && mkdir -m 700 /tmp/vfg-testserver' \
    && push --chmod=F644 "$HERE/testserver.sh" "$HERE/testserver.py" "$HERE/systemd/minecraft-test.service" \
      "$SSH_USER@$SSH_HOST:/tmp/vfg-testserver/" \
    && remote "sudo bash /tmp/vfg-testserver/testserver.sh $*; rc=\$?; rm -rf /tmp/vfg-testserver; exit \$rc"
}

cmd_testserver() {
  case "${1-}" in
    --fresh|"") ;;
    *) usage ;;
  esac
  [[ $# -le 1 ]] || usage
  ssh_setup
  log "the test server: sending testserver.sh, testserver.py, minecraft-test.service"
  send_testserver "$@" || die "the test server is not set up (above)"
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
    "$HERE/env.example" "$HERE/testserver.sh" "$HERE/testserver.py" "$HERE/systemd/minecraft-test.service" \
    "$SSH_USER@$SSH_HOST:/tmp/vfg-provision/" \
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
if [ -f /etc/systemd/system/vfg-engine.service ]; then
  sudo systemctl restart vfg-engine
fi
if [ -f /etc/systemd/system/vfg-bridge.service ]; then
  sudo systemctl restart vfg-bridge
fi
if [ -f /opt/vfg/jev/jev_filter.py ]; then
  sudo systemctl restart vfg-jev
fi
sleep 2
for unit in minecraft vfg-engine vfg-bridge vfg-jev; do
  printf '%-11s %s\n' "$unit" "$(systemctl is-active "$unit" || true)"
done
REMOTE
}

# send_engine -- the engine's package, library, examples, datapack and install pieces to
# /opt/vfg/engine/, then engine/deploy/install.sh there as ec2-user: the venv, and the datapack
# into the world. The library is the first of the unit's content folders (VFG_ENGINE_CONTENT).
send_engine() {
  [[ -d $ENGINE/vfg_engine && -d $ENGINE/datapack/everhear && -f $ENGINE/deploy/requirements.txt ]] \
    || die "run it from the repo: no engine in $ENGINE"
  remote 'test -x /opt/vfg/uv/uv && mkdir -p /opt/vfg/engine/datapack /opt/vfg/engine/deploy' \
    || die "the box has no /opt/vfg/uv/uv, which makes the engine's venv: deploy.sh provision first"
  log "sending engine/vfg_engine/, library/, examples/, datapack/everhear/ and deploy/ to /opt/vfg/engine/"
  local folders=("$ENGINE/vfg_engine") folder
  for folder in library examples; do
    if [[ -d $ENGINE/$folder ]]; then folders+=("$ENGINE/$folder"); fi
  done
  push --recursive --delete --exclude='__pycache__/' --exclude='*.pyc' --chmod=D755,F644 \
    "${folders[@]}" "$SSH_USER@$SSH_HOST:/opt/vfg/engine/" || die "could not send the engine"
  push --recursive --delete --chmod=D755,F644 "$ENGINE/datapack/everhear" \
    "$SSH_USER@$SSH_HOST:/opt/vfg/engine/datapack/" || die "could not send the datapack"
  push --chmod=F644 "$ENGINE/deploy/install.sh" "$ENGINE/deploy/requirements.txt" \
    "$SSH_USER@$SSH_HOST:/opt/vfg/engine/deploy/" || die "could not send the engine's install pieces"
  log "the engine's venv and datapack"
  remote 'bash /opt/vfg/engine/deploy/install.sh' || die "the engine's install failed (above)"
  # A test server's world copies the live world's datapacks when it is made (its reset.sh, at
  # every start): one made before this install lacks the datapack, or has the last one. Its
  # freshness token taken away, the next vfg test restarts it for a fresh world first (under the
  # test server's lock, so never in the middle of a test).
  remote 'bash -s' <<'REMOTE' || log "the test server's world was not checked against the datapack (above)"
set -euo pipefail
if [ -d /opt/mc-test/world ] && ! diff -rq /opt/mc/world/datapacks/everhear /opt/mc-test/world/datapacks/everhear \
    > /dev/null 2>&1; then
  rm -f /opt/mc-test/world/vfg-fresh
  echo "engine: the test server's world has another datapack: its next test gets a fresh world with this one"
fi
REMOTE
}

cmd_engine() {
  ssh_setup
  send_engine
  remote 'rm -rf /tmp/vfg-units && mkdir -m 700 /tmp/vfg-units'
  push --chmod=F644 "$ENGINE/deploy/vfg-engine.service" "$SSH_USER@$SSH_HOST:/tmp/vfg-units/" \
    || die "could not send vfg-engine.service"
  log "installing vfg-engine.service, restarting the engine"
  remote 'bash -s' <<'REMOTE'
set -euo pipefail
sudo install -m 644 -o root -g root /tmp/vfg-units/vfg-engine.service /etc/systemd/system/
rm -rf /tmp/vfg-units
sudo systemctl daemon-reload
sudo systemctl enable vfg-engine
sudo systemctl restart vfg-engine
sleep 2
for unit in minecraft vfg-engine; do
  printf '%-11s %s\n' "$unit" "$(systemctl is-active "$unit" || true)"
done
REMOTE
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
  # Every cast is the engine's, through its socket: the engine goes with the bridge.
  send_engine
  remote 'rm -rf /tmp/vfg-units && mkdir -m 700 /tmp/vfg-units'
  push --chmod=F644 "$HERE/systemd/vfg-bridge.service" "$HERE/systemd/vfg-jev.service" \
    "$ENGINE/deploy/vfg-engine.service" "$SSH_USER@$SSH_HOST:/tmp/vfg-units/" \
    || die "could not send the units"

  # Jev's classes are the CHECK the bridge's schema sets: a Jev on the box is stopped before the
  # schema changes and deployed from this checkout after it, never left running against it.
  local jev=0 rc=0
  remote 'test -f /opt/vfg/jev/jev_filter.py' || rc=$?
  case $rc in 0) jev=1 ;; 1) ;; *) die "could not reach the box to look for Jev" ;; esac

  log "installing the units, applying the schema, restarting"
  remote "JEV=$jev bash -s" <<'REMOTE'
set -euo pipefail
sudo install -m 644 -o root -g root /tmp/vfg-units/vfg-bridge.service /tmp/vfg-units/vfg-jev.service \
  /tmp/vfg-units/vfg-engine.service /etc/systemd/system/
rm -rf /tmp/vfg-units
sudo systemctl daemon-reload
sudo systemctl enable vfg-engine vfg-bridge vfg-jev
if [ "$JEV" = 1 ]; then
  sudo systemctl stop vfg-jev
fi
cd /opt/vfg
PYTHONPATH=/opt/vfg/lib:/opt/vfg/bridge /opt/vfg/python/bin/python3 -m vfg_bridge --init-db
sudo systemctl restart vfg-engine
sudo systemctl restart vfg-bridge
sleep 2
for unit in minecraft vfg-engine vfg-bridge; do
  printf '%-11s %s\n' "$unit" "$(systemctl is-active "$unit" || true)"
done
REMOTE
  if (( jev )); then
    log "deploying Jev from this checkout with the bridge (jev/deploy.sh)"
    VFG_SSH_HOST=$SSH_HOST VFG_SSH_USER=$SSH_USER VFG_SSH_KEY=$SSH_KEY "$BRIDGE/../jev/deploy.sh" \
      || die "Jev did not deploy, and vfg-jev is stopped: run jev/deploy.sh"
  fi
  # The test server stays current (testserver.sh changes only what changed, and makes it if missing).
  log "the test server: keeping it current"
  send_testserver || log "the test server is not current (above); the live side is deployed: deploy.sh testserver"
}

case "${1-}" in
  testserver) shift; cmd_testserver "$@" ;;
  provision) shift; cmd_provision "$@" ;;
  env) shift; [[ $# -eq 0 ]] || usage; cmd_env ;;
  engine) shift; [[ $# -eq 0 ]] || usage; cmd_engine ;;
  "") cmd_deploy ;;
  -h|--help|*) usage ;;
esac
