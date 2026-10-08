#!/usr/bin/env bash
# publish.sh -- puts today's VFG installer online and prints the one link to send players.
#
#   installer/publish.sh
#
# Run it each time the box has a new IP (every launch, every stop/start), after VFG_SSH_HOST in
# ~/.vfg/config is the new IP. It prints two lines, the link and when it expires, and nothing else.
#
#   1. reads the server address (VFG_SSH_HOST) and, over SSH, the client token from /opt/vfg/.env;
#   2. takes the newest client-mod/build/libs/vfg-client-*.jar, the Fabric Loader profile JSON
#      from Fabric's meta server and the pinned Fabric API jar, checked against its Modrinth sha512;
#   3. uploads those to a private S3 bucket under a new random prefix and presigns them for 7 days;
#   4. builds the installers with the address, the token and those links baked in, uploads and
#      presigns them; then the page, with the installers' links in it; uploads and presigns it.
#
# Needs the AWS CLI (aws) with credentials that can create the bucket once and put objects in it,
# python3, curl and ssh. Reads from ~/.vfg/config, the environment winning:
#   VFG_SSH_HOST, VFG_SSH_USER, VFG_SSH_KEY   the box, as the deploy scripts use them
#   VFG_AWS_REGION        default us-east-2
#   VFG_INSTALL_BUCKET    the bucket; made (private) and written back here on the first run
#   VFG_INSTALL_ADDRESS   the address players join, if it is not VFG_SSH_HOST
#   VFG_CLIENT_JAR_DIR    default <repo>/client-mod/build/libs
set -euo pipefail
shopt -s inherit_errexit nullglob

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$HERE/.." && pwd)
# shellcheck source=../bridge/deploy/lib.sh
. "$REPO/bridge/deploy/lib.sh"

[[ $# -eq 0 ]] || { printf 'usage: %s\n' "${0##*/}" >&2; exit 2; }

MC_VERSION=26.3
LOADER_VERSION=0.19.5
# Fabric API 0.162.0+26.3, as Modrinth lists it (project P7dR8mSH); the sha512 is Modrinth's.
FABRIC_API_NAME=fabric-api-0.162.0+26.3.jar
FABRIC_API_URL=${VFG_FABRIC_API_URL:-https://cdn.modrinth.com/data/P7dR8mSH/versions/v2j28coa/fabric-api-0.162.0%2B26.3.jar}
FABRIC_API_SHA512=${VFG_FABRIC_API_SHA512:-5ab70908952f1d2346d16b122ca31327f4055db59c279a1bc3c3c581ce359bb541cba15730f55b0727be7c7c4debc4a4412a183c228e0d4dea81c734f7b412ce}
FABRIC_META=${VFG_FABRIC_META_URL:-https://meta.fabricmc.net}
PROFILE_URL=$FABRIC_META/v2/versions/loader/$MC_VERSION/$LOADER_VERSION/profile/json
EXPIRES_IN=604800 # 7 days, the most a presigned link can last

REGION=$(vfg_config VFG_AWS_REGION us-east-2)
JAR_DIR=$(vfg_config VFG_CLIENT_JAR_DIR "$REPO/client-mod/build/libs")
CACHE=${XDG_CACHE_HOME:-$HOME/.cache}/vfg-installer

for tool in aws python3 curl ssh; do
  command -v "$tool" >/dev/null || die "$tool is not on PATH"
done

sha() { python3 -c 'import hashlib, sys
h = hashlib.new(sys.argv[1])
with open(sys.argv[2], "rb") as f:
    for chunk in iter(lambda: f.read(1 << 16), b""):
        h.update(chunk)
print(h.hexdigest())' "$1" "$2"; }

random_hex() { od -An -N"$1" -tx1 /dev/urandom | tr -d ' \n'; }

# --- 1. the address and the token ---

ADDRESS=$(vfg_config VFG_INSTALL_ADDRESS "$(vfg_config VFG_SSH_HOST)")
[[ -n $ADDRESS ]] || die "VFG_SSH_HOST is not set: put the box's IP in ~/.vfg/config"
[[ $ADDRESS =~ ^[A-Za-z0-9.-]+(:[0-9]{1,5})?$ ]] || die "the server address \"$ADDRESS\" is not a host name or IP"

ssh_setup
# The token is read into this shell only: never echoed, never in argv, never in a log.
TOKEN=$(retry 3 remote "sed -n 's/^[[:space:]]*VFG_CLIENT_TOKEN[[:space:]]*=[[:space:]]*//p' /opt/vfg/.env | tail -n 1") \
  || die "could not read VFG_CLIENT_TOKEN from /opt/vfg/.env on $SSH_HOST over SSH"
TOKEN=${TOKEN%$'\r'}
if [[ ${#TOKEN} -ge 2 && $TOKEN == \"*\" ]]; then
  TOKEN=${TOKEN:1:${#TOKEN}-2}
  TOKEN=${TOKEN//\\\"/\"}
  TOKEN=${TOKEN//\\\\/\\}
elif [[ ${#TOKEN} -ge 2 && $TOKEN == \'*\' ]]; then
  TOKEN=${TOKEN:1:${#TOKEN}-2}
fi
[[ -n $TOKEN ]] || die "VFG_CLIENT_TOKEN is empty on the box: set it with bridge/deploy/set-secret.sh VFG_CLIENT_TOKEN"
[[ $TOKEN =~ ^[[:graph:]]+$ ]] || die "VFG_CLIENT_TOKEN on the box has spaces or characters the installers cannot carry"

# --- 2. the files players get ---

JAR=""
for f in "$JAR_DIR"/vfg-client-*.jar; do
  case ${f##*/} in *-sources.jar | *-dev.jar | *-javadoc.jar) continue ;; esac
  if [[ -z $JAR || $f -nt $JAR ]]; then JAR=$f; fi
done
[[ -n $JAR ]] || die "no vfg-client-*.jar in $JAR_DIR: build the mod first (cd client-mod && ./gradlew build)"
[[ $(head -c 2 "$JAR") == PK ]] || die "$JAR is not a jar"
JAR_NAME=${JAR##*/}

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/files" "$WORK/out" "$CACHE"

cp "$JAR" "$WORK/files/$JAR_NAME"
JAR_SHA256=$(sha sha256 "$WORK/files/$JAR_NAME")

PROFILE_NAME=fabric-loader-$LOADER_VERSION-$MC_VERSION.json
retry 3 curl -fsSL --connect-timeout 20 -o "$WORK/files/$PROFILE_NAME" "$PROFILE_URL" \
  || die "could not fetch the Fabric Loader profile from $PROFILE_URL"
python3 - "$WORK/files/$PROFILE_NAME" "fabric-loader-$LOADER_VERSION-$MC_VERSION" "$MC_VERSION" <<'PY' \
  || die "the Fabric Loader profile from $PROFILE_URL is not the one expected"
import json, sys
p = json.load(open(sys.argv[1], encoding="utf-8"))
assert p["id"] == sys.argv[2] and p["inheritsFrom"] == sys.argv[3] and p["mainClass"] and p["libraries"]
PY
PROFILE_SHA256=$(sha sha256 "$WORK/files/$PROFILE_NAME")

if [[ ! -f $CACHE/$FABRIC_API_NAME || $(sha sha512 "$CACHE/$FABRIC_API_NAME") != "$FABRIC_API_SHA512" ]]; then
  retry 3 curl -fsSL --connect-timeout 20 -o "$CACHE/$FABRIC_API_NAME.part" "$FABRIC_API_URL" \
    || die "could not download Fabric API from $FABRIC_API_URL"
  if [[ $(sha sha512 "$CACHE/$FABRIC_API_NAME.part") != "$FABRIC_API_SHA512" ]]; then
    rm -f "$CACHE/$FABRIC_API_NAME.part"
    die "Fabric API from $FABRIC_API_URL does not match Modrinth's sha512; nothing was uploaded"
  fi
  mv -f "$CACHE/$FABRIC_API_NAME.part" "$CACHE/$FABRIC_API_NAME"
fi
cp "$CACHE/$FABRIC_API_NAME" "$WORK/files/$FABRIC_API_NAME"

# --- 3. the bucket: private, Block Public Access on (S3's default), objects gone after 8 days ---

s3api() { aws s3api --region "$REGION" "$@"; }

make_bucket() {
  local name=$1 err
  if ! err=$(s3api create-bucket --bucket "$name" \
      --create-bucket-configuration "LocationConstraint=$REGION" 2>&1 >/dev/null); then
    [[ $err == *BucketAlreadyExists* ]] && return 3
    die "could not create the bucket $name: $err"
  fi
  # Links last 7 days, so nothing in the bucket is any use after 8: let S3 delete it.
  s3api put-bucket-lifecycle-configuration --bucket "$name" --lifecycle-configuration \
    '{"Rules":[{"ID":"vfg-installer-expire","Status":"Enabled","Filter":{"Prefix":""},"Expiration":{"Days":8}}]}' \
    >/dev/null 2>&1 || log "note: could not set the bucket to delete old installers after 8 days"
}

remember_bucket() {
  local file=$VFG_CONFIG_FILE
  mkdir -p "$(dirname "$file")"
  if [[ -s $file && $(tail -c 1 "$file") != "" ]]; then printf '\n' >> "$file"; fi
  (umask 077; printf 'VFG_INSTALL_BUCKET=%s\n' "$1" >> "$file")
}

BUCKET=$(vfg_config VFG_INSTALL_BUCKET)
if [[ -z $BUCKET ]]; then
  for _ in 1 2 3; do
    BUCKET=vfg-installer-$(random_hex 4)
    rc=0
    make_bucket "$BUCKET" || rc=$?
    [[ $rc -eq 3 ]] && continue
    remember_bucket "$BUCKET"
    break
  done
  [[ $rc -eq 0 ]] || die "could not find a free bucket name"
elif ! err=$(s3api head-bucket --bucket "$BUCKET" 2>&1 >/dev/null); then
  if [[ $err == *404* || $err == *"Not Found"* || $err == *NoSuchBucket* ]]; then
    rc=0
    make_bucket "$BUCKET" || rc=$?
    [[ $rc -eq 0 ]] || die "the bucket name $BUCKET (VFG_INSTALL_BUCKET) is taken by someone else: remove it from ~/.vfg/config"
  else
    die "cannot use the bucket $BUCKET (VFG_INSTALL_BUCKET): $err"
  fi
fi

PREFIX=$(date -u +%Y%m%d)-$(random_hex 12)

# upload FILE KEY CONTENT_TYPE [DOWNLOAD_NAME] -- prints the presigned link
upload() {
  local file=$1 key=$PREFIX/$2 type=$3 name=${4-} args=()
  args=(--content-type "$type" --cache-control "private, max-age=600" --only-show-errors --no-progress)
  [[ -z $name ]] || args+=(--content-disposition "attachment; filename=\"$name\"")
  retry 3 aws s3 cp --region "$REGION" "${args[@]}" "$file" "s3://$BUCKET/$key" >/dev/null \
    || die "could not upload $2 to s3://$BUCKET"
  aws s3 presign --region "$REGION" --expires-in "$EXPIRES_IN" "s3://$BUCKET/$key" \
    || die "could not presign $2"
}

NOW=$(date +%s)
JAR_URL=$(upload "$WORK/files/$JAR_NAME" "files/$JAR_NAME" application/java-archive "$JAR_NAME")
FABRIC_URL=$(upload "$WORK/files/$FABRIC_API_NAME" "files/$FABRIC_API_NAME" application/java-archive "$FABRIC_API_NAME")
PROFILE_LINK=$(upload "$WORK/files/$PROFILE_NAME" "files/$PROFILE_NAME" application/json)

# --- 4. the installers, then the page that links to them ---

printf '%s\n' "$TOKEN" | python3 "$HERE/build.py" installers --out "$WORK/out" \
  --address "$ADDRESS" --mc-version "$MC_VERSION" --loader-version "$LOADER_VERSION" \
  --jar-name "$JAR_NAME" --jar-url "$JAR_URL" --jar-sha256 "$JAR_SHA256" \
  --fabric-api-name "$FABRIC_API_NAME" --fabric-api-url "$FABRIC_URL" --fabric-api-sha512 "$FABRIC_API_SHA512" \
  --profile-url "$PROFILE_LINK" --profile-sha256 "$PROFILE_SHA256"
unset TOKEN

SH_URL=$(upload "$WORK/out/vfg-install.sh" vfg-install.sh application/octet-stream vfg-install.sh)
CMD_URL=$(upload "$WORK/out/vfg-install.cmd" vfg-install.cmd application/octet-stream vfg-install.cmd)
PS1_URL=$(upload "$WORK/out/vfg-install.ps1" vfg-install.ps1 "text/plain; charset=utf-8")

python3 "$HERE/build.py" page --out "$WORK/out" --address "$ADDRESS" --mc-version "$MC_VERSION" \
  --sh-url "$SH_URL" --cmd-url "$CMD_URL" --ps1-url "$PS1_URL" --expires "$((NOW + EXPIRES_IN))"
PAGE_URL=$(upload "$WORK/out/index.html" index.html "text/html; charset=utf-8")

if [[ $PAGE_URL == *X-Amz-Security-Token* ]]; then
  log "warning: these AWS credentials are temporary (SSO or an assumed role); the links stop working"
  log "when they expire, which may be well before 7 days. An IAM user's access keys give the full 7 days."
fi
printf '%s\n' "$PAGE_URL"
printf 'expires %s\n' "$(date -d "@$((NOW + EXPIRES_IN))" '+%a %d %b %Y %H:%M %Z')"
