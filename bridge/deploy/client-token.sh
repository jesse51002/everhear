#!/usr/bin/env bash
# client-token.sh -- print VFG_CLIENT_TOKEN from /opt/vfg/.env on the box, to this terminal only,
# for the owner to paste into the client mod's settings. Run it yourself; never pipe it into a log.
# Reads VFG_SSH_HOST, VFG_SSH_USER and VFG_SSH_KEY from ~/.vfg/config.
set -euo pipefail
shopt -s inherit_errexit

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib.sh
. "$HERE/lib.sh"

[[ $# -eq 0 ]] || { printf 'usage: %s\n' "${0##*/}" >&2; exit 2; }
[[ -t 1 ]] || die "stdout is not a terminal; refusing to print the token anywhere else"
ssh_setup

token=$(remote "sed -n 's/^[[:space:]]*VFG_CLIENT_TOKEN[[:space:]]*=[[:space:]]*//p' /opt/vfg/.env | tail -n 1")
token=${token%\"}
token=${token#\"}
[[ -n $token ]] || die "VFG_CLIENT_TOKEN is empty on the box"
printf '%s\n' "$token"
