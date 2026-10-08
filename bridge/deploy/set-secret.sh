#!/usr/bin/env bash
# set-secret.sh NAME -- put NAME=value into /opt/vfg/.env on the box, for the owner to run.
#
#   deploy/set-secret.sh TYPESAFE_API_KEY
#
# The value is read silently from this terminal and sent over SSH on stdin, never in argv, so it
# shows up in no process list, no shell history and no log. On the box the line NAME=... is
# replaced or added, the file stays mode 600, and vfg-jev restarts (vfg-bridge too when NAME is
# VFG_CLIENT_TOKEN). Reads VFG_SSH_HOST, VFG_SSH_USER and VFG_SSH_KEY from ~/.vfg/config.
set -euo pipefail
shopt -s inherit_errexit

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib.sh
. "$HERE/lib.sh"

[[ $# -eq 1 && $1 =~ ^[A-Z_][A-Z0-9_]*$ ]] || {
  printf 'usage: %s NAME   (NAME like TYPESAFE_API_KEY)\n' "${0##*/}" >&2
  exit 2
}
NAME=$1
[[ -t 0 ]] || die "run it in a terminal: the value is typed, not piped"
ssh_setup

IFS= read -r -s -p "$NAME (input hidden): " value
printf '\n' >&2
[[ -n $value ]] || die "empty value; nothing changed"

# Runs on the box: the value arrives on stdin; only NAME is in argv.
read -r -d '' UPDATE <<'PY' || true
import os, re, sys
name = sys.argv[1]
value = sys.stdin.readline().rstrip("\n")
if not value or "\n" in value or "\r" in value:
    sys.exit("set-secret: empty or multi-line value; nothing changed")
if re.fullmatch(r"[A-Za-z0-9._~+/=:@%,-]+", value):
    line = "%s=%s" % (name, value)
else:  # systemd EnvironmentFile: double quotes, with \ and " escaped
    line = '%s="%s"' % (name, value.replace("\\", "\\\\").replace('"', '\\"'))
path = "/opt/vfg/.env"
try:
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
except FileNotFoundError:
    lines = []
pattern = re.compile(r"^\s*%s\s*=" % re.escape(name))
out, done = [], False
for old in lines:
    if pattern.match(old):
        if not done:
            out.append(line)
            done = True
        continue
    out.append(old)
if not done:
    out.append(line)
tmp = path + ".tmp"
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as f:
    f.write("\n".join(out) + "\n")
os.chmod(tmp, 0o600)
os.replace(tmp, path)
print("set-secret: %s %s in %s" % (name, "replaced" if done else "added", path))
PY

restart="sudo systemctl restart vfg-jev"
if [[ $NAME == VFG_CLIENT_TOKEN ]]; then
  restart="sudo systemctl restart vfg-bridge vfg-jev"
fi
printf '%s\n' "$value" \
  | remote "python3 -c $(printf '%q' "$UPDATE") $NAME && $restart && systemctl is-active vfg-bridge vfg-jev"
unset value
