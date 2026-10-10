#!/usr/bin/env bash
# provision.sh [--restore] [--accept-eula] -- on the vfg-mc box, as root: the host, Python 3.12
# for vfg (python.sh) and Minecraft.
#
# Idempotent: run again, it changes nothing that is already right. deploy.sh provision copies it
# beside python.sh, minecraft.service, rcon.py and env.example in /tmp/vfg-provision/ and runs it
# with sudo.
# With --restore, and only when /opt/mc/world does not exist, it extracts /tmp/mc-backup.tgz
# (and /tmp/vfg-backup.tgz if it was sent) into /opt. It never prints the RCON password.
# --accept-eula writes eula=true for a server that has no eula.txt yet: deploy.sh passes it when
# VFG_ACCEPT_EULA=true, the owner's acceptance of Minecraft's EULA (https://aka.ms/MinecraftEULA).
set -euo pipefail
shopt -s inherit_errexit

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
OWNER=ec2-user
MC=/opt/mc
VFG=/opt/vfg
MC_VERSION=26.3
MANIFEST=https://piston-meta.mojang.com/mc/game/version_manifest_v2.json

log() { printf 'provision: %s\n' "$*"; }
die() { printf 'provision: %s\n' "$*" >&2; exit 1; }

RESTORE=0
ACCEPT_EULA=0
for arg in "$@"; do
  case $arg in
    --restore) RESTORE=1 ;;
    --accept-eula) ACCEPT_EULA=1 ;;
    *) printf 'usage: provision.sh [--restore] [--accept-eula]\n' >&2; exit 2 ;;
  esac
done
[[ $(id -u) -eq 0 ]] || die "run it as root (deploy.sh provision does)"
for f in python.sh minecraft.service rcon.py env.example; do
  [[ -f $HERE/$f ]] || die "$f is missing beside provision.sh"
done

# install_if_changed SRC DEST MODE -- returns 0 when DEST changed
install_if_changed() {
  if [[ -f $2 ]] && cmp -s "$1" "$2"; then
    return 1
  fi
  install -m "$3" -o root -g root "$1" "$2"
}

RESTART_MC=0

# 1. Packages and the clock.
log "packages"
dnf install -y java-25-amazon-corretto-headless sqlite rsync tar nftables
timedatectl set-timezone UTC

# 2. Folders, all ec2-user's.
install -d -o "$OWNER" -g "$OWNER" -m 755 "$MC" "$VFG" \
  "$VFG/lib" "$VFG/bridge" "$VFG/jev" "$VFG/scripts" "$VFG/run" "$VFG/state"

# 3. Python 3.12 in /opt/vfg, by uv: what the scripts, vfg's remote programs and the units run on.
bash "$HERE/python.sh" "$VFG"

# 4. Restore, never over an existing world.
if (( RESTORE )); then
  if [[ -e $MC/world ]]; then
    log "restore: $MC/world exists; leaving it alone"
  else
    [[ -f /tmp/mc-backup.tgz ]] || die "restore: /tmp/mc-backup.tgz is missing"
    log "restore: extracting mc-backup.tgz into /opt"
    tar xzf /tmp/mc-backup.tgz -C /opt
    if [[ -f /tmp/vfg-backup.tgz ]]; then
      log "restore: extracting vfg-backup.tgz into /opt"
      tar xzf /tmp/vfg-backup.tgz -C /opt
    fi
    chown -R "$OWNER:$OWNER" "$MC" "$VFG"
    rm -f /tmp/mc-backup.tgz /tmp/vfg-backup.tgz  # /tmp is RAM, and vfg-backup holds .env
  fi
fi

# 5. The server jar: Mojang's, checked against its sha1. It unpacks libraries/ and versions/ itself.
if [[ ! -f $MC/server.jar ]]; then
  log "downloading the $MC_VERSION server jar"
  python3 - "$MANIFEST" "$MC_VERSION" "$MC/server.jar" <<'PY'
import hashlib, json, os, sys, urllib.request

manifest_url, version, dest = sys.argv[1:4]

def get(url):
    with urllib.request.urlopen(url, timeout=120) as resp:
        return resp.read()

manifest = json.loads(get(manifest_url))
entry = next((v for v in manifest["versions"] if v["id"] == version), None)
if entry is None:
    sys.exit("version %s is not in Mojang's manifest" % version)
server = json.loads(get(entry["url"]))["downloads"]["server"]
data = get(server["url"])
if hashlib.sha1(data).hexdigest() != server["sha1"]:
    sys.exit("server.jar does not match its sha1")
with open(dest + ".part", "wb") as f:
    f.write(data)
os.replace(dest + ".part", dest)
PY
  chown "$OWNER:$OWNER" "$MC/server.jar"
fi

# 6. The EULA: kept from a restored world, or written when the owner accepted it (VFG_ACCEPT_EULA).
if [[ ! -f $MC/eula.txt ]]; then
  (( ACCEPT_EULA )) || die "Minecraft's EULA (https://aka.ms/MinecraftEULA) is not accepted: read it, then set VFG_ACCEPT_EULA=true in ~/.vfg/config and run deploy.sh provision again"
  echo "eula=true" > "$MC/eula.txt"
  chown "$OWNER:$OWNER" "$MC/eula.txt"
fi

# 7. server.properties: our keys replaced or added, every other line alone; the password kept or made.
props_result=$(python3 - "$MC/server.properties" <<'PY'
import os, secrets, sys

path = sys.argv[1]
want = [("enable-rcon", "true"), ("rcon.port", "25575"), ("broadcast-rcon-to-ops", "false"),
        ("broadcast-console-to-ops", "false"), ("log-ips", "false"), ("server-port", "25565"),
        ("enable-query", "false"), ("management-server-enabled", "false")]
try:
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
except FileNotFoundError:
    lines = []

def find(key):
    for i, line in enumerate(lines):
        if line.split("=", 1)[0].strip() == key and "=" in line:
            return i
    return None

changed = False
for key, value in want:
    i, line = find(key), "%s=%s" % (key, value)
    if i is None:
        lines.append(line)
        changed = True
    elif lines[i] != line:
        lines[i] = line
        changed = True
i = find("rcon.password")
if i is None or not lines[i].split("=", 1)[1].strip():
    line = "rcon.password=" + secrets.token_urlsafe(24)
    if i is None:
        lines.append(line)
    else:
        lines[i] = line
    changed = True
if changed:
    fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(path + ".tmp", path)
print("changed" if changed else "unchanged")
PY
)
chown "$OWNER:$OWNER" "$MC/server.properties"
chmod 600 "$MC/server.properties"
log "server.properties: $props_result"
[[ $props_result == changed ]] && RESTART_MC=1

# 8. rcon.py (reads the password from the server.properties beside it), and console.log.
if [[ ! -f $MC/rcon.py ]]; then
  install -m 644 -o "$OWNER" -g "$OWNER" "$HERE/rcon.py" "$MC/rcon.py"
fi
if [[ ! -f $MC/console.log ]]; then
  install -m 644 -o "$OWNER" -g "$OWNER" /dev/null "$MC/console.log"
fi
chown "$OWNER:$OWNER" "$MC/console.log"

# 9. /opt/vfg/.env, from env.example: names, no values.
if [[ ! -f $VFG/.env ]]; then
  install -m 600 -o "$OWNER" -g "$OWNER" "$HERE/env.example" "$VFG/.env"
fi
chown "$OWNER:$OWNER" "$VFG/.env"
chmod 600 "$VFG/.env"
empty=$(sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=[[:space:]]*$/\1/p' "$VFG/.env" | tr '\n' ' ')
if [[ -n ${empty// /} ]]; then
  log "fill in $VFG/.env: ${empty% }"
fi

# 10. RCON stays local: vanilla binds it on every interface.
install -d -m 755 /etc/nftables
nft_new=$(mktemp)
cat > "$nft_new" <<'NFT'
# vfg: RCON (25575) answers on loopback only. Declared, deleted and declared again so that
# loading this file twice leaves one copy of the rule.
table inet vfg
delete table inet vfg
table inet vfg {
  chain input {
    type filter hook input priority 0; policy accept;
    tcp dport 25575 iifname != "lo" drop;
  }
}
NFT
if install_if_changed "$nft_new" /etc/nftables/vfg.nft 644; then
  log "wrote /etc/nftables/vfg.nft"
fi
rm -f "$nft_new"
include='include "/etc/nftables/vfg.nft"'
touch /etc/sysconfig/nftables.conf
grep -qxF "$include" /etc/sysconfig/nftables.conf || echo "$include" >> /etc/sysconfig/nftables.conf
systemctl enable --now nftables
nft -f /etc/nftables/vfg.nft
nft list table inet vfg > /dev/null || die "the nftables rule did not load"

# 11. The heap: memory in whole GiB (rounded up: an m7i.xlarge reports a little under 16) minus 4.
mem_kib=$(awk '/^MemTotal:/ { print $2 }' /proc/meminfo)
heap=$(( (mem_kib + 1048575) / 1048576 - 4 ))
(( heap >= 2 )) || heap=2
install -d -m 755 /etc/systemd/system/minecraft.service.d
heap_new=$(mktemp)
printf '[Service]\nEnvironment=MC_HEAP=%dG\n' "$heap" > "$heap_new"
if install_if_changed "$heap_new" /etc/systemd/system/minecraft.service.d/heap.conf 644; then
  log "heap: ${heap}G"
  RESTART_MC=1
fi
rm -f "$heap_new"

# 12. The unit.
if install_if_changed "$HERE/minecraft.service" /etc/systemd/system/minecraft.service 644; then
  log "installed minecraft.service"
  RESTART_MC=1
fi
systemctl daemon-reload
log_start=$(stat -c %s "$MC/console.log")
if systemctl is-active --quiet minecraft; then
  if (( RESTART_MC )); then
    log "restarting minecraft for the new settings"
    systemctl restart minecraft
    STARTED=1
  else
    STARTED=0
  fi
  systemctl enable minecraft
else
  log "starting minecraft"
  systemctl enable --now minecraft
  STARTED=1
fi

# 13. Once RCON is up, stop admin commands being echoed into the console. The world keeps the rule.
if (( STARTED )); then
  log "waiting up to 5 minutes for RCON"
  ready=0
  for _ in $(seq 1 300); do
    if tail -c +"$((log_start + 1))" "$MC/console.log" | grep -q 'RCON running on'; then
      ready=1
      break
    fi
    sleep 1
  done
  (( ready )) || die "RCON did not come up within 5 minutes; see $MC/console.log"
fi
for attempt in $(seq 1 30); do
  if runuser -u "$OWNER" -- python3 "$MC/rcon.py" gamerule log_admin_commands false; then
    break
  fi
  (( attempt < 30 )) || die "could not set log_admin_commands over RCON"
  sleep 2
done
log "done"
