#!/usr/bin/env bash
# ec2.sh -- the vfg-mc EC2 box, from the owner's PC, with the AWS CLI v2 and its own credentials.
#
#   ec2.sh launch    a new instance with a public IP, and its security group (if missing); prints the IP
#   ec2.sh start     starts the instance, waits until SSH answers, prints the IP
#   ec2.sh stop      stops the three units (and the test server) over SSH so the world saves, then the instance
#   ec2.sh status    state, instance type and current public IP
#   ec2.sh ssh-ip    replaces the security group's port-22 rule with this PC's current IP; prints
#                    the instance's current public IP
#   ec2.sh retire    backs the box up into VFG_BACKUP_DIR (default ~/everhear/backups/), checks
#                    the copies, then terminates the instance (and releases any vfg-mc Elastic IP)
#
# retire writes mc-backup.tgz (/opt/mc, the world) and vfg-backup.tgz (vfg.db, feed.log,
# watch.json, lookout.md, menu.md and .env from /opt/vfg). The pair they replace becomes *.prev.tgz,
# and the pair before that is deleted. vfg-backup.tgz is also kept as vfg-backup-<UTC time>.tgz
# (vfg-backup-2026-10-11-065900.tgz), mode 600, which nothing rotates or deletes: every session's
# data survives.
#
# No Elastic IP: the zero-cost habit is launch, play, retire. The public IP is the instance's own
# and changes on every launch and every stop/start: update VFG_SSH_HOST and the players' address.
# Everything is found by the tag Name=vfg-mc in VFG_AWS_REGION (us-east-2), so nobody types an id.
# Reads VFG_AWS_REGION, VFG_INSTANCE_TYPE (m7i.xlarge), VFG_KEY_PAIR (everhear), VFG_BACKUP_DIR,
# VFG_SSH_USER and VFG_SSH_KEY (~/.ssh/<key pair>.pem) from ~/.vfg/config or the environment.
set -euo pipefail
shopt -s inherit_errexit

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib.sh
. "$HERE/lib.sh"

NAME=vfg-mc
AMI_PARAM=/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64
REGION=$(vfg_config VFG_AWS_REGION us-east-2)
INSTANCE_TYPE=$(vfg_config VFG_INSTANCE_TYPE m7i.xlarge)

usage() {
  printf 'usage: %s launch|start|stop|status|ssh-ip|retire\n' "${0##*/}" >&2
  exit 2
}

ec2() { aws ec2 --region "$REGION" --output text "$@"; }

# none VALUE -- true when the CLI printed nothing or None
none() { [[ -z ${1//[[:space:]]/} || $1 == None ]]; }

my_ip() {
  local ip
  ip=$(curl -4 -s --max-time 10 https://checkip.amazonaws.com | tr -d '[:space:]')
  [[ $ip =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || die "could not find this PC's public IP"
  printf '%s' "$ip"
}

# instance_id -- the vfg-mc instance that is not terminated, or nothing
instance_id() {
  local out ids
  out=$(ec2 describe-instances \
    --filters "Name=tag:Name,Values=$NAME" \
              "Name=instance-state-name,Values=pending,running,stopping,stopped,shutting-down" \
    --query 'Reservations[].Instances[].InstanceId')
  read -r -a ids <<< "${out//None/}"
  (( ${#ids[@]} <= 1 )) || die "more than one $NAME instance: ${ids[*]}"
  printf '%s' "${ids[0]-}"
}

require_instance() {
  local id
  id=$(instance_id)
  [[ -n $id ]] || die "there is no $NAME instance; use launch"
  printf '%s' "$id"
}

instance_state() {
  ec2 describe-instances --instance-ids "$1" --query 'Reservations[0].Instances[0].State.Name'
}

instance_ip() {
  local ip
  ip=$(ec2 describe-instances --instance-ids "$1" \
    --query 'Reservations[0].Instances[0].PublicIpAddress')
  none "$ip" && ip=""
  printf '%s' "$ip"
}

default_vpc() {
  local vpc
  vpc=$(ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId')
  none "$vpc" && die "there is no default VPC in $REGION"
  printf '%s' "$vpc"
}

group_id() {
  local sg
  sg=$(ec2 describe-security-groups \
    --filters "Name=group-name,Values=$NAME" "Name=vpc-id,Values=$(default_vpc)" \
    --query 'SecurityGroups[0].GroupId')
  none "$sg" && sg=""
  printf '%s' "$sg"
}

# set_ssh_rule GROUP -- port 22 from this PC's IP only
set_ssh_rule() {
  local sg=$1 ip cidrs cidr have=0
  ip=$(my_ip)
  # shellcheck disable=SC2016  # a JMESPath literal, not a shell expansion
  cidrs=$(ec2 describe-security-groups --group-ids "$sg" \
    --query 'SecurityGroups[0].IpPermissions[?FromPort==`22`].IpRanges[].CidrIp')
  for cidr in ${cidrs//None/}; do
    if [[ $cidr == "$ip/32" ]]; then
      have=1
    else
      ec2 revoke-security-group-ingress --group-id "$sg" --protocol tcp --port 22 \
        --cidr "$cidr" > /dev/null
      log "removed SSH from $cidr"
    fi
  done
  if (( ! have )); then
    ec2 authorize-security-group-ingress --group-id "$sg" --ip-permissions \
      "IpProtocol=tcp,FromPort=22,ToPort=22,IpRanges=[{CidrIp=$ip/32,Description=owner}]" > /dev/null
    log "SSH allowed from $ip/32"
  else
    log "SSH already allowed from $ip/32 only"
  fi
}

# ensure_group -- the vfg-mc security group, made if missing; prints its id
ensure_group() {
  local sg vpc ip
  sg=$(group_id)
  if [[ -n $sg ]]; then
    log "security group $NAME exists"
    set_ssh_rule "$sg"
  else
    vpc=$(default_vpc)
    ip=$(my_ip)
    sg=$(ec2 create-security-group --group-name "$NAME" --vpc-id "$vpc" \
      --description "VFG Minecraft: 25565 and 8787 open, SSH from the owner only" \
      --tag-specifications "ResourceType=security-group,Tags=[{Key=Name,Value=$NAME}]" \
      --query GroupId)
    ec2 authorize-security-group-ingress --group-id "$sg" --ip-permissions \
      "IpProtocol=tcp,FromPort=25565,ToPort=25565,IpRanges=[{CidrIp=0.0.0.0/0,Description=minecraft}]" \
      "IpProtocol=tcp,FromPort=8787,ToPort=8787,IpRanges=[{CidrIp=0.0.0.0/0,Description=vfg-bridge}]" \
      "IpProtocol=tcp,FromPort=22,ToPort=22,IpRanges=[{CidrIp=$ip/32,Description=owner}]" > /dev/null
    log "made security group $NAME: 25565 and 8787 from anywhere, 22 from $ip/32"
  fi
  printf '%s' "$sg"
}

wait_ssh() {
  local ip=$1 i
  ssh_setup "$ip"
  log "waiting for SSH on $ip"
  for (( i = 0; i < 60; i++ )); do
    if ssh -o ConnectTimeout=5 "${SSH_OPTS[@]}" "$SSH_USER@$SSH_HOST" true 2> /dev/null; then
      return 0
    fi
    sleep 5
  done
  die "SSH on $ip did not answer within 5 minutes"
}

cmd_launch() {
  local id sg ami new_id ip
  id=$(instance_id)
  [[ -z $id ]] || die "a $NAME instance already exists ($id): start it, or retire it first"
  sg=$(ensure_group)
  ami=$(aws ssm get-parameters --region "$REGION" --names "$AMI_PARAM" \
    --query 'Parameters[0].Value' --output text)
  [[ $ami == ami-* ]] || die "could not resolve the Amazon Linux 2023 AMI"
  new_id=$(ec2 run-instances --image-id "$ami" --instance-type "$INSTANCE_TYPE" \
    --key-name "$KEY_PAIR" --count 1 \
    --network-interfaces "[{\"DeviceIndex\":0,\"AssociatePublicIpAddress\":true,\"Groups\":[\"$sg\"]}]" \
    --block-device-mappings 'DeviceName=/dev/xvda,Ebs={VolumeSize=30,VolumeType=gp3,DeleteOnTermination=true}' \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME}]" \
                         "ResourceType=volume,Tags=[{Key=Name,Value=$NAME}]" \
    --query 'Instances[0].InstanceId')
  log "launched $new_id ($INSTANCE_TYPE, $ami); waiting for it to run"
  ec2 wait instance-running --instance-ids "$new_id"
  ip=$(instance_ip "$new_id")
  [[ -n $ip ]] || die "$new_id has no public IP"
  log "next: put VFG_SSH_HOST=$ip in ~/.vfg/config, send the players the address, then"
  log "      deploy.sh provision --restore (or without --restore for a new world)"
  printf '%s\n' "$ip"
}

cmd_start() {
  local id state ip
  id=$(require_instance)
  state=$(instance_state "$id")
  case $state in
    running|pending) ;;
    stopping)
      log "waiting for $id to finish stopping"
      ec2 wait instance-stopped --instance-ids "$id"
      ec2 start-instances --instance-ids "$id" > /dev/null ;;
    stopped) ec2 start-instances --instance-ids "$id" > /dev/null ;;
    *) die "$id is $state" ;;
  esac
  ec2 wait instance-running --instance-ids "$id"
  ip=$(instance_ip "$id")
  [[ -n $ip ]] || die "$id has no public IP"
  wait_ssh "$ip"
  log "after a stop the public IP is new: update VFG_SSH_HOST and the players' address if it changed"
  printf '%s\n' "$ip"
}

stop_units() {
  # The test server (testserver.sh), if the box has one: its world is thrown away anyway.
  if remote 'sudo systemctl stop minecraft-test 2> /dev/null'; then
    log "stopped minecraft-test"
  fi
  if remote 'sudo systemctl stop vfg-jev vfg-bridge minecraft'; then
    log "stopped vfg-jev, vfg-bridge and minecraft"
  else
    log "could not stop the units over SSH; stopping the instance stops them too"
  fi
}

cmd_stop() {
  local id state ip
  id=$(require_instance)
  state=$(instance_state "$id")
  if [[ $state == running ]]; then
    ip=$(instance_ip "$id")
    ssh_setup "$ip"
    stop_units
  fi
  if [[ $state != stopped ]]; then
    ec2 stop-instances --instance-ids "$id" > /dev/null
    log "stopping $id"
    ec2 wait instance-stopped --instance-ids "$id"
  fi
  log "$id is stopped; it keeps its disk, and gets a new public IP when it starts"
}

cmd_status() {
  local id
  id=$(instance_id)
  if [[ -z $id ]]; then
    printf 'no %s instance\n' "$NAME"
    return 0
  fi
  printf '%s %s %s\n' "$(instance_state "$id")" \
    "$(ec2 describe-instances --instance-ids "$id" --query 'Reservations[0].Instances[0].InstanceType')" \
    "$(instance_ip "$id" || true)"
}

cmd_ssh_ip() {
  local sg id
  sg=$(group_id)
  [[ -n $sg ]] || die "there is no $NAME security group; use launch"
  set_ssh_rule "$sg"
  id=$(instance_id)
  if [[ -n $id ]]; then
    printf '%s\n' "$(instance_ip "$id")"
  fi
}

cmd_retire() {
  local id state ip alloc remote_sums name local_sum remote_sum dated=""
  id=$(require_instance)
  state=$(instance_state "$id")
  if [[ $state != running ]]; then
    log "starting $id to back it up"
    cmd_start > /dev/null
  fi
  ip=$(instance_ip "$id")
  [[ -n $ip ]] || die "$id has no public IP"
  ssh_setup "$ip"
  stop_units

  log "packing /opt/mc and the transcript history on the box"
  remote_sums=$(remote 'bash -s' <<'REMOTE'
set -euo pipefail
sudo rm -f /tmp/mc-backup.tgz /tmp/vfg-backup.tgz
if [ -f /opt/vfg/vfg.db ]; then
  python3 -c 'import sqlite3; c = sqlite3.connect("/opt/vfg/vfg.db"); c.execute("PRAGMA wal_checkpoint(TRUNCATE)"); c.close()' || true
fi
sudo tar czf /tmp/mc-backup.tgz -C /opt \
  --exclude=mc/libraries --exclude=mc/versions --exclude=mc/console.log mc
files=""
for f in vfg/vfg.db vfg/feed.log vfg/watch.json vfg/lookout.md vfg/menu.md vfg/.env; do
  if [ -e "/opt/$f" ]; then files="$files $f"; fi
done
# After the checkpoint the WAL is empty; if something still held it, keep what it holds.
if [ -s /opt/vfg/vfg.db-wal ]; then files="$files vfg/vfg.db-wal"; fi
if [ -n "$files" ]; then
  # shellcheck disable=SC2086
  sudo tar czf /tmp/vfg-backup.tgz -C /opt $files
fi
sudo chown "$(id -un):" /tmp/mc-backup.tgz /tmp/vfg-backup.tgz 2>/dev/null || true
chmod 600 /tmp/mc-backup.tgz /tmp/vfg-backup.tgz 2>/dev/null || true
cd /tmp
sha256sum mc-backup.tgz
if [ -f vfg-backup.tgz ]; then sha256sum vfg-backup.tgz; fi
REMOTE
)

  mkdir -p "$BACKUP_DIR"
  for name in mc-backup.tgz vfg-backup.tgz; do
    grep -q " $name\$" <<< "$remote_sums" || continue
    log "downloading $name"
    pull "/tmp/$name" "$BACKUP_DIR/$name.part" || die "could not download $name; nothing was deleted"
    chmod 600 "$BACKUP_DIR/$name.part"
    remote_sum=$(awk -v n="$name" '$2 == n { print $1 }' <<< "$remote_sums")
    local_sum=$(sha256sum "$BACKUP_DIR/$name.part" | awk '{ print $1 }')
    [[ -n $remote_sum && $local_sum == "$remote_sum" ]] \
      || die "$name: the copy does not match the box (sha256); nothing was deleted"
    log "$name: sha256 matches"
  done
  # Only now, with both copies checked, does the old pair become *.prev.tgz.
  for name in mc-backup vfg-backup; do
    if [[ -f $BACKUP_DIR/$name.tgz.part ]]; then
      [[ -f $BACKUP_DIR/$name.tgz ]] && mv -f "$BACKUP_DIR/$name.tgz" "$BACKUP_DIR/$name.prev.tgz"
      mv -f "$BACKUP_DIR/$name.tgz.part" "$BACKUP_DIR/$name.tgz"
      if [[ $name == vfg-backup ]]; then
        dated=$BACKUP_DIR/vfg-backup-$(date -u +%Y-%m-%d-%H%M%S).tgz
      fi
    fi
  done
  # The new vfg-backup.tgz is also kept under a dated name that nothing rotates or deletes, so
  # every session's vfg.db survives for the replays and cost studies. It holds .env: mode 600.
  # The checked vfg-backup.tgz is already in place, so a missing dated copy only warns.
  if [[ -n $dated ]]; then
    if [[ -e $dated ]]; then
      log "warning: $dated already exists and was left alone;" \
        "vfg-backup.tgz still holds this session"
    elif ln "$BACKUP_DIR/vfg-backup.tgz" "$dated" 2> /dev/null \
        || cp -p "$BACKUP_DIR/vfg-backup.tgz" "$dated"; then
      chmod 600 "$dated"
      log "kept ${dated##*/}"
    else
      log "warning: could not keep vfg-backup.tgz as $dated;" \
        "vfg-backup.tgz still holds this session"
    fi
  fi
  log "backups are in $BACKUP_DIR"

  log "terminating $id"
  ec2 terminate-instances --instance-ids "$id" > /dev/null
  ec2 wait instance-terminated --instance-ids "$id"
  # launch allocates no Elastic IP; release any vfg-mc one an older launch left behind.
  for alloc in $(ec2 describe-addresses --filters "Name=tag:Name,Values=$NAME" \
      --query 'Addresses[].AllocationId'); do
    none "$alloc" && continue
    ec2 release-address --allocation-id "$alloc"
    log "released Elastic IP $alloc"
  done
  log "retired: nothing of $NAME is left running on AWS; the next launch brings a new IP"
}

command -v aws > /dev/null || die "the AWS CLI v2 is not installed"
case "${1-}" in
  launch) cmd_launch ;;
  start) cmd_start ;;
  stop) cmd_stop ;;
  status) cmd_status ;;
  ssh-ip) cmd_ssh_ip ;;
  retire) cmd_retire ;;
  *) usage ;;
esac
