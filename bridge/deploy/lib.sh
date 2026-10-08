# shellcheck shell=bash
# Shared by deploy.sh and ec2.sh, on the owner's PC: settings, ssh, rsync, retries.
#
# Settings come from ~/.vfg/config, the toolkit's file: KEY=value lines, # comments,
# optional quotes, ~ expanded; an environment variable of the same name wins.

VFG_CONFIG_FILE=${VFG_CONFIG_FILE:-$HOME/.vfg/config}

log() { printf '%s: %s\n' "${0##*/}" "$*" >&2; }
die() { log "$*"; exit 1; }

_expand_tilde() {
  local v=$1
  # shellcheck disable=SC2088  # matching a literal ~ on purpose
  if [[ $v == "~" ]]; then v=$HOME; elif [[ $v == "~/"* ]]; then v=$HOME/${v:2}; fi
  printf '%s' "$v"
}

# vfg_config KEY [DEFAULT] -- the environment, then ~/.vfg/config, then DEFAULT.
vfg_config() {
  local key=$1 default=${2-} line k v found="" have=0
  if [[ -n ${!key-} ]]; then
    _expand_tilde "${!key}"
    return
  fi
  if [[ -r $VFG_CONFIG_FILE ]]; then
    while IFS= read -r line || [[ -n $line ]]; do
      line=${line%$'\r'}
      line=${line#"${line%%[![:space:]]*}"}
      [[ -z $line || $line == \#* || $line != *=* ]] && continue
      k=${line%%=*}
      k=${k%"${k##*[![:space:]]}"}
      [[ $k == "$key" ]] || continue
      v=${line#*=}
      v=${v#"${v%%[![:space:]]*}"}
      v=${v%"${v##*[![:space:]]}"}
      if [[ ${#v} -ge 2 && ( ( $v == \"*\" ) || ( $v == \'*\' ) ) ]]; then
        v=${v:1:${#v}-2}
      fi
      found=$v
      have=1
    done < "$VFG_CONFIG_FILE"
  fi
  if (( have )) && [[ -n $found ]]; then
    _expand_tilde "$found"
  else
    _expand_tilde "$default"
  fi
}

# Where ec2.sh retire keeps the backups and deploy.sh provision --restore finds them, and the
# EC2 key pair ec2.sh launch uses (its private key is VFG_SSH_KEY, by default ~/.ssh/<pair>.pem).
# shellcheck disable=SC2034  # used by deploy.sh and ec2.sh
BACKUP_DIR=$(vfg_config VFG_BACKUP_DIR "$HOME/everhear/backups")
KEY_PAIR=$(vfg_config VFG_KEY_PAIR everhear)

# ssh_setup [HOST] -- SSH_HOST (VFG_SSH_HOST unless given), SSH_USER, SSH_KEY, SSH_OPTS, RSYNC_RSH.
ssh_setup() {
  SSH_HOST=${1:-$(vfg_config VFG_SSH_HOST)}
  [[ -n $SSH_HOST ]] || die "VFG_SSH_HOST is not set: put the box's IP in ~/.vfg/config"
  SSH_USER=$(vfg_config VFG_SSH_USER ec2-user)
  SSH_KEY=$(vfg_config VFG_SSH_KEY "$HOME/.ssh/$KEY_PAIR.pem")
  [[ -r $SSH_KEY ]] || die "cannot read the SSH key $SSH_KEY (VFG_SSH_KEY)"
  SSH_OPTS=(-i "$SSH_KEY" -o IdentitiesOnly=yes -o BatchMode=yes
            -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15
            -o ServerAliveInterval=15 -o ServerAliveCountMax=4)
  # rsync splits -e on spaces and honours single quotes, not backslashes.
  local arg
  RSYNC_RSH=ssh
  for arg in "${SSH_OPTS[@]}"; do
    [[ $arg != *"'"* ]] || die "a single quote in an SSH option is not supported: $arg"
    RSYNC_RSH+=" '$arg'"
  done
}

# shellcheck disable=SC2029  # remote commands are passed as one string, expanded here on purpose
remote() { ssh "${SSH_OPTS[@]}" "$SSH_USER@$SSH_HOST" "$@"; }

# retry N CMD... -- run CMD up to N times, 5 s apart; the home link is lossy.
retry() {
  local tries=$1 n=1
  shift
  until "$@"; do
    (( n >= tries )) && return 1
    log "attempt $n of $tries failed; retrying in 5 s"
    n=$((n + 1))
    sleep 5
  done
}

# push SRC... DEST -- rsync to the box, resumable and retried.
push() { retry 10 rsync --partial --timeout=120 --perms --times -e "$RSYNC_RSH" "$@"; }

# pull REMOTE_PATH LOCAL_PATH -- rsync from the box, resumable and retried.
pull() { retry 10 rsync --partial --timeout=120 --times -e "$RSYNC_RSH" "$SSH_USER@$SSH_HOST:$1" "$2"; }
