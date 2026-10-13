#!/usr/bin/env bash
# testserver.sh [--fresh] -- on the vfg-mc box, as root: the test server beside the live one, for
# implementers' tests (vfg test): /opt/mc-test (game 127.0.0.1:25566, RCON 25576) and /opt/vfg-test,
# unit minecraft-test, a fresh world at every start.
#
# Idempotent: run again, it changes only what is not right yet, and restarts the test server only
# when something it reads changed. --fresh makes its template world again. provision.sh runs it as
# a step; deploy.sh testserver copies it beside testserver.py and minecraft-test.service into
# /tmp/vfg-testserver/ and runs it with sudo (and plain deploy.sh keeps it current).
#
#  1. The system user and group vfgtest (no login, no home), which vfg test runs a sandboxed test
#     as, so that it is not ec2-user, the live services' uid: a process of the same uid can read
#     the live server's files through /proc/<pid>/cwd and the bridge's secrets in /proc/<pid>/environ.
#     ec2-user is in the group (it gives the test's folders to it).
#  2. /opt/mc-test and /opt/vfg-test, ec2-user's; testserver.py lays them out as ec2-user (the
#     same code realmc runs on the PC), reading /opt/mc and /opt/vfg and never writing them, with
#     --test-user vfgtest: the test may write /opt/vfg-test/run and /opt/vfg-test/state, and read
#     /opt/mc-test/server.properties (640, group vfgtest), nothing else.
#  3. nftables (table inet vfg_test, /etc/nftables/vfg-test.nft): 25566 and 25576 dropped off lo;
#     and from user vfgtest nothing goes out but TCP to the test server's RCON on lo: not the live
#     server (25565, 25575), not the bridge (8787), not the internet.
#  4. The live heap: memory - 4 GiB - minecraft-test's MemoryMax, in the live unit's heap.conf.
#     It applies at the live server's next restart: this never restarts the live server.
#  5. minecraft-test.service installed, enabled, (re)started when needed; then RCON is waited for.
set -euo pipefail
shopt -s inherit_errexit

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
OWNER=ec2-user
LIVE_MC=/opt/mc
LIVE_VFG=/opt/vfg
MC=/opt/mc-test
VFG=/opt/vfg-test
PORT=25566
RCON_PORT=25576
UNIT=minecraft-test
TEST_USER=vfgtest
PY=$LIVE_VFG/python/bin/python3

log() { printf 'testserver: %s\n' "$*"; }
die() { printf 'testserver: %s\n' "$*" >&2; exit 1; }

FRESH=()
for arg in "$@"; do
  case $arg in
    --fresh) FRESH=(--fresh) ;;
    *) printf 'usage: testserver.sh [--fresh]\n' >&2; exit 2 ;;
  esac
done
[[ $(id -u) -eq 0 ]] || die "run it as root (deploy.sh testserver does)"
for f in testserver.py minecraft-test.service; do
  [[ -f $HERE/$f ]] || die "$f is missing beside testserver.sh"
done
[[ -x $PY ]] || die "no $PY (python.sh): deploy.sh provision first"
[[ -f $LIVE_MC/server.jar && -f $LIVE_MC/server.properties ]] || die "no live server in $LIVE_MC: deploy.sh provision first"

# install_if_changed SRC DEST MODE -- returns 0 when DEST changed
install_if_changed() {
  if [[ -f $2 ]] && cmp -s "$1" "$2"; then
    return 1
  fi
  install -m "$3" -o root -g root "$1" "$2"
}

# nft_rules PORT RCON_PORT TEST_UID TEST_USER -- /etc/nftables/vfg-test.nft. Declared, deleted and
# declared again so that loading the file twice leaves one copy of each rule.
nft_rules() {
  cat <<NFT
# vfg: the test server (testserver.sh), game $1 and RCON $2, answers on loopback only; and its
# sandboxed tests, run as $4 (uid $3), reach the test server's RCON on loopback and nothing else:
# not the live server (25565, 25575), not the bridge (8787), not the internet. Refused at once
# (a reset or an ICMP error), so a test that tries fails fast instead of hanging.
table inet vfg_test
delete table inet vfg_test
table inet vfg_test {
  chain input {
    type filter hook input priority 0; policy accept;
    tcp dport { $1, $2 } iifname != "lo" drop;
  }
  chain output {
    type filter hook output priority 0; policy accept;
    meta skuid $3 oifname "lo" tcp dport $2 accept;
    meta skuid $3 meta l4proto tcp reject with tcp reset;
    meta skuid $3 reject;
  }
}
NFT
}

# unit_value KEY -- KEY=value's value in minecraft-test.service ([Service] or Environment=)
unit_value() { sed -n "s/^\(Environment=.*\)\?$1=\([0-9A-Za-z]*\).*/\2/p" "$HERE/minecraft-test.service" | head -n 1; }
HEAP=$(unit_value MC_TEST_HEAP)
TEST_MAX=$(unit_value MemoryMax)
TEST_CPU=$(sed -n 's/^CPUQuota=\([0-9]*%\)$/\1/p' "$HERE/minecraft-test.service")
[[ $HEAP =~ ^[0-9]+[MG]$ && $TEST_MAX =~ ^[0-9]+M$ && -n $TEST_CPU ]] \
  || die "minecraft-test.service has no MC_TEST_HEAP, MemoryMax=<n>M or CPUQuota=<n>%"

# 1. The test user: a system user and group, no login, no home.
if ! getent group "$TEST_USER" > /dev/null; then
  groupadd --system "$TEST_USER"
  log "made the group $TEST_USER"
fi
if ! id -u "$TEST_USER" > /dev/null 2>&1; then
  useradd --system --gid "$TEST_USER" --no-create-home --home-dir /nonexistent \
    --shell "$(command -v nologin || echo /sbin/nologin)" --comment "EverHear implementers' sandboxed tests" "$TEST_USER"
  log "made the user $TEST_USER"
fi
if ! id -nG "$OWNER" | tr ' ' '\n' | grep -qx "$TEST_USER"; then
  usermod -aG "$TEST_USER" "$OWNER"
  log "$OWNER is in the group $TEST_USER (from its next login)"
fi
TEST_UID=$(id -u "$TEST_USER")
[[ $TEST_UID != "$(id -u "$OWNER")" ]] || die "$TEST_USER has $OWNER's uid"

# 2. The folders, then the layout, the template and the AOT cache, as ec2-user, held to the unit's
# limits (both are made by starting the test server outside its unit). testserver.py sets the
# folders' modes and groups; a folder is only made here when it is missing.
for d in "$MC" "$VFG"; do
  [[ -d $d ]] || install -d -o "$OWNER" -g "$OWNER" -m 755 "$d"
done
setup() {
  systemd-run --quiet --scope -p MemoryMax="$TEST_MAX" -p CPUWeight=20 -p CPUQuota="$TEST_CPU" -p IOWeight=20 \
    -- runuser -u "$OWNER" -- "$PY" "$HERE/testserver.py" --live-mc "$LIVE_MC" --live-vfg "$LIVE_VFG" \
    --mc "$MC" --vfg "$VFG" --port "$PORT" --rcon-port "$RCON_PORT" --heap "$HEAP" --user "$OWNER" \
    --test-user "$TEST_USER" --java /usr/bin/java "${FRESH[@]}"
}
rc=0
setup || rc=$?
if (( rc == 11 )); then
  log "stopping $UNIT to make its template or its AOT cache"
  systemctl stop "$UNIT"
  rc=0
  setup || rc=$?
fi
case $rc in
  0) RESTART=0 ;;
  10) RESTART=1 ;;
  *) die "testserver.py failed (exit $rc)" ;;
esac

# 3. The firewall: like RCON's 25575 (provision.sh), the test ports never answer off lo; and the
# sandboxed test (user vfgtest) reaches the test server's RCON on lo and nothing else.
install -d -m 755 /etc/nftables
nft_new=$(mktemp)
nft_rules "$PORT" "$RCON_PORT" "$TEST_UID" "$TEST_USER" > "$nft_new"
if install_if_changed "$nft_new" /etc/nftables/vfg-test.nft 644; then
  log "wrote /etc/nftables/vfg-test.nft"
fi
rm -f "$nft_new"
include='include "/etc/nftables/vfg-test.nft"'
touch /etc/sysconfig/nftables.conf
grep -qxF "$include" /etc/sysconfig/nftables.conf || echo "$include" >> /etc/sysconfig/nftables.conf
systemctl enable --now nftables
nft -f /etc/nftables/vfg-test.nft
nft list table inet vfg_test > /dev/null || die "the nftables rule did not load"

# 4. The live heap leaves room for the test server: memory in whole GiB (rounded up) - 4 - the
# test server's MemoryMax in GiB (rounded up). provision.sh's step 12 is the same rule.
mem_kib=$(awk '/^MemTotal:/ { print $2 }' /proc/meminfo)
reserve=$(( (${TEST_MAX%M} + 1023) / 1024 ))
heap=$(( (mem_kib + 1048575) / 1048576 - 4 - reserve ))
(( heap >= 2 )) || heap=2
install -d -m 755 /etc/systemd/system/minecraft.service.d
heap_new=$(mktemp)
printf '[Service]\nEnvironment=MC_HEAP=%dG\n' "$heap" > "$heap_new"
if install_if_changed "$heap_new" /etc/systemd/system/minecraft.service.d/heap.conf 644; then
  log "live heap: ${heap}G from the live server's next restart (sudo systemctl restart minecraft when" \
    "nobody plays); it was not restarted"
fi
rm -f "$heap_new"
running=$(tr '\0' ' ' 2> /dev/null < "/proc/$(systemctl show -p MainPID --value minecraft)/cmdline" \
  | grep -o -- '-Xmx[0-9]*[MG]' || true)
if [[ -n $running && $running != "-Xmx${heap}G" ]]; then
  log "the live server still runs with $running; ${heap}G applies at its next restart"
fi

# 5. The unit.
UNIT_CHANGED=0
if install_if_changed "$HERE/minecraft-test.service" "/etc/systemd/system/$UNIT.service" 644; then
  log "installed $UNIT.service"
  UNIT_CHANGED=1
fi
systemctl daemon-reload
systemctl enable --quiet "$UNIT"
if (( RESTART || UNIT_CHANGED )) || ! systemctl is-active --quiet "$UNIT"; then
  log "(re)starting $UNIT: a fresh world from its template"
  systemctl reset-failed "$UNIT" 2> /dev/null || true
  systemctl restart "$UNIT"
fi
runuser -u "$OWNER" -- "$PY" "$HERE/testserver.py" --mc "$MC" --wait 180 \
  || die "$UNIT's RCON did not answer; see $MC/console.log and journalctl -u $UNIT"
log "$UNIT $(systemctl is-active "$UNIT"): game 127.0.0.1:$PORT, RCON 127.0.0.1:$RCON_PORT, $MC and $VFG"
