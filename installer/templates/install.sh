#!/usr/bin/env bash
# VFG voice for Minecraft Java @@VFG_MC_VERSION@@ -- the installer for Linux and macOS.
#
#   bash vfg-install.sh
#
# Built by publish.sh on @@VFG_BUILT_AT@@. Safe to run again: it updates the server address,
# the client token and the mod, and leaves everything else as it was. What it does:
#   - adds Fabric Loader @@VFG_LOADER_VERSION@@ to the launcher's versions (the launcher fetches the rest),
#   - makes its own game folder, <minecraft>/instances/vfg-@@VFG_MC_VERSION@@/, so your other
#     installations and their mods are not touched,
#   - puts Fabric API and the VFG mod in that folder's mods/, each checked by its checksum,
#   - sets the server address and the client token in its config/vfg-client.json, keeping the rest,
#   - adds the server to that folder's server list,
#   - adds a "VFG @@VFG_MC_VERSION@@" installation to the launcher, backing up its list first.
# It needs python3 or perl (one is on almost every Linux and on every Mac) and curl or wget.

if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
# Written for bash 3.2 as well (macOS): no associative arrays, no mapfile.
set -eu
set -o pipefail

SERVER_ADDRESS=@@VFG_SERVER_ADDRESS@@
CLIENT_TOKEN=@@VFG_CLIENT_TOKEN@@
MC_VERSION=@@VFG_MC_VERSION@@
LOADER_VERSION=@@VFG_LOADER_VERSION@@
VFG_JAR_NAME=@@VFG_JAR_NAME@@
VFG_JAR_URL=@@VFG_JAR_URL@@
VFG_JAR_SHA256=@@VFG_JAR_SHA256@@
FABRIC_API_NAME=@@VFG_FABRIC_API_NAME@@
FABRIC_API_URL=@@VFG_FABRIC_API_URL@@
FABRIC_API_SHA512=@@VFG_FABRIC_API_SHA512@@
PROFILE_JSON_URL=@@VFG_PROFILE_JSON_URL@@
PROFILE_JSON_SHA256=@@VFG_PROFILE_JSON_SHA256@@

VERSION_ID="fabric-loader-$LOADER_VERSION-$MC_VERSION"
PROFILE_KEY="vfg-$MC_VERSION"
PROFILE_NAME="VFG $MC_VERSION"
INSTANCE="vfg-$MC_VERSION"
SERVER_NAME="VFG"

say() { printf 'VFG: %s\n' "$*"; }
fail() { printf '\nVFG: %s\n' "$*" >&2; exit 1; }

if [ "$(id -u)" = 0 ]; then
  fail "Run this as yourself, not with sudo: Minecraft lives in your own home folder."
fi

case "$(uname -s)" in
  Darwin) OS=mac ;;
  Linux) OS=linux ;;
  *) fail "This installer is for Linux and macOS. On Windows, use the Windows download on the same page." ;;
esac

# --- 1. The Minecraft folder: the one(s) the launcher has already set up ---

if [ "$OS" = mac ]; then
  set -- "$HOME/Library/Application Support/minecraft"
else
  # The launcher from minecraft.net, then the Flatpak one (Flathub, com.mojang.Minecraft).
  set -- "$HOME/.minecraft" "$HOME/.var/app/com.mojang.Minecraft/.minecraft"
fi
GAME_DIRS=()
looked=""
seen="|"
for d in "$@"; do
  looked="$looked
    $d"
  [ -f "$d/launcher_profiles.json" ] || [ -f "$d/launcher_profiles_microsoft_store.json" ] || continue
  real=$(cd -P "$d" && pwd -P)
  case "$seen" in *"|$real|"*) continue ;; esac
  seen="$seen$real|"
  GAME_DIRS+=("$d")
done
if [ "${#GAME_DIRS[@]}" -eq 0 ]; then
  fail "No Minecraft folder with a launcher in it here. Looked in:$looked
Install and run the Minecraft launcher once first (open it and sign in), then run this again."
fi

WORK=$(mktemp -d "${TMPDIR:-/tmp}/vfg-install.XXXXXX")
trap 'rm -rf "$WORK"' EXIT
trap 'exit 130' INT TERM

# --- the JSON, checksum and server-list work: python3, else perl ---

cat > "$WORK/helper.py" <<'PY'
import hashlib, json, os, struct, sys

def die(msg):
    sys.stderr.write("\nVFG: %s\n" % msg)
    sys.exit(1)

def read_json(path):
    with open(path, "rb") as f:
        data = f.read()
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return json.loads(data.decode("utf-8"))

def dump(obj):
    return (json.dumps(obj, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

def write_atomic(path, data, private=False):
    tmp = path + ".vfg-tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if private else 0o644)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    if private:
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)

def u16(b, i):
    return struct.unpack(">H", b[i:i + 2])[0]

def i32(b, i):
    return max(0, struct.unpack(">i", b[i:i + 4])[0])

def skip(b, i, t):
    """The index just after a payload of NBT tag type t that starts at i."""
    if t == 1: return i + 1
    if t == 2: return i + 2
    if t in (3, 5): return i + 4
    if t in (4, 6): return i + 8
    if t == 7: return i + 4 + i32(b, i)
    if t == 8: return i + 2 + u16(b, i)
    if t == 9:
        et, n = b[i], i32(b, i + 1)
        i += 5
        for _ in range(n):
            i = skip(b, i, et)
        return i
    if t == 10:
        while True:
            tt = b[i]
            i += 1
            if tt == 0:
                return i
            i = skip(b, i + 2 + u16(b, i), tt)
    if t == 11: return i + 4 + 4 * i32(b, i)
    if t == 12: return i + 4 + 8 * i32(b, i)
    raise ValueError("unknown NBT tag %d" % t)

def nbt_name(s):
    raw = s.encode("utf-8")
    return struct.pack(">H", len(raw)) + raw

def entry_name(e):
    """The "name" string of one server compound's payload, or None."""
    i = 0
    while e[i] != 0:
        t = e[i]
        n = u16(e, i + 1)
        key = e[i + 3:i + 3 + n]
        j = i + 3 + n
        if t == 8 and key == b"name":
            return e[j + 2:j + 2 + u16(e, j)].decode("utf-8", "replace")
        i = skip(e, j, t)
    return None

def servers(path, name, ip):
    """Puts {name, ip} first in servers.dat (uncompressed NBT), keeping every other entry and tag."""
    head, others, entries = b"\x0a\x00\x00", [], []
    if os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path, "rb") as f:
            b = f.read()
        if b[0] != 10:
            raise ValueError("servers.dat does not start with a compound")
        i = 3 + u16(b, 1)
        head = b[:i]
        while b[i] != 0:
            t, start = b[i], i
            n = u16(b, i + 1)
            key = b[i + 3:i + 3 + n]
            j = i + 3 + n
            if t == 9 and key == b"servers":
                et, count = b[j], i32(b, j + 1)
                k = j + 5
                if count and et != 10:
                    raise ValueError("servers is not a list of compounds")
                for _ in range(count):
                    s = k
                    k = skip(b, k, 10)
                    entries.append(b[s:k])
                i = k
            else:
                i = skip(b, j, t)
                others.append(b[start:i])
    ours = b"\x08" + nbt_name("name") + nbt_name(name) + b"\x08" + nbt_name("ip") + nbt_name(ip) + b"\x00"
    entries = [ours] + [e for e in entries if entry_name(e) != name]
    out = head + b"".join(others) + b"\x09" + nbt_name("servers") + b"\x0a" + struct.pack(">i", len(entries))
    out += b"".join(entries) + b"\x00"
    write_atomic(path, out)

def main():
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "hash":
        h = hashlib.new("sha" + args[0])
        with open(args[1], "rb") as f:
            for chunk in iter(lambda: f.read(1 << 16), b""):
                h.update(chunk)
        print(h.hexdigest())
    elif cmd == "checkprofiles":
        for path in args:
            try:
                lp = read_json(path)
            except (ValueError, UnicodeDecodeError):
                die("%s is not valid JSON, so it was left alone. Open the Minecraft launcher once "
                    "(it repairs it), close it, and run this again." % path)
            if not isinstance(lp, dict) or not isinstance(lp.get("profiles", {}), (dict, type(None))):
                die("%s does not look like the launcher's list of installations, so it was left alone." % path)
    elif cmd == "config":
        path = args[0]
        cfg = {}
        if os.path.exists(path):
            try:
                cfg = read_json(path)
                if not isinstance(cfg, dict):
                    raise ValueError("not an object")
            except (ValueError, UnicodeDecodeError):
                os.replace(path, path + ".bad")
                cfg = {}
                print("VFG: the old vfg-client.json did not read as JSON; it is kept as vfg-client.json.bad")
        cfg["server_address"] = os.environ["VFG_ADDR"]
        cfg["client_token"] = os.environ["VFG_TOKEN"]
        write_atomic(path, dump(cfg), private=True)
        bridge = cfg.get("bridge_url")
        if isinstance(bridge, str) and bridge.strip():
            print("VFG: note: bridge_url in %s is set to %s, so your voice goes there. "
                  "Leave it empty unless the server owner told you to set it." % (path, bridge))
    elif cmd == "profile":
        path, key, name, version_id, game_dir, now = args
        lp = read_json(path)
        if not isinstance(lp.get("profiles"), dict):
            lp["profiles"] = {}
        p = lp["profiles"].get(key)
        if not isinstance(p, dict):
            p = {"name": name, "type": "custom", "created": now, "icon": "Crafting_Table"}
            lp["profiles"][key] = p
        p.update({"name": name, "type": "custom", "lastVersionId": version_id, "gameDir": game_dir,
                  "lastUsed": now})
        out = dump(lp)
        json.loads(out.decode("utf-8"))
        write_atomic(path, out)
    elif cmd == "servers":
        servers(args[0], args[1], args[2])
    else:
        die("unknown helper command " + cmd)

main()
PY

cat > "$WORK/helper.pl" <<'PL'
use strict;
use warnings;
use JSON::PP ();
use Digest::SHA ();
use Fcntl qw(O_WRONLY O_CREAT O_TRUNC);

my $JSON = JSON::PP->new->utf8->pretty->indent_length(2)->canonical;

sub die_msg { print STDERR "\nVFG: $_[0]\n"; exit 1; }

sub slurp {
    my ($path) = @_;
    open(my $fh, '<:raw', $path) or die "cannot read $path: $!\n";
    local $/;
    my $data = <$fh>;
    close $fh;
    $data =~ s/^\xEF\xBB\xBF//;
    return $data;
}

sub read_json { return $JSON->decode(slurp($_[0])); }

sub write_atomic {
    my ($path, $data, $private) = @_;
    my $tmp = "$path.vfg-tmp";
    unlink $tmp;
    sysopen(my $fh, $tmp, O_WRONLY | O_CREAT | O_TRUNC, $private ? 0600 : 0644)
      or die "cannot write $tmp: $!\n";
    binmode $fh;
    print {$fh} $data or die "cannot write $tmp: $!\n";
    close $fh or die "cannot write $tmp: $!\n";
    chmod 0600, $tmp if $private;
    rename $tmp, $path or die "cannot replace $path: $!\n";
}

sub u16 { return unpack('n', substr($_[0], $_[1], 2)); }
sub i32 { my $v = unpack('l>', substr($_[0], $_[1], 4)); return $v < 0 ? 0 : $v; }
sub byte_at { return ord(substr($_[0], $_[1], 1)); }

# The index just after a payload of NBT tag type $t that starts at $i.
sub skip {
    my ($b, $i, $t) = @_;
    return $i + 1 if $t == 1;
    return $i + 2 if $t == 2;
    return $i + 4 if $t == 3 || $t == 5;
    return $i + 8 if $t == 4 || $t == 6;
    return $i + 4 + i32($b, $i) if $t == 7;
    return $i + 2 + u16($b, $i) if $t == 8;
    if ($t == 9) {
        my ($et, $n) = (byte_at($b, $i), i32($b, $i + 1));
        $i += 5;
        $i = skip($b, $i, $et) for 1 .. $n;
        return $i;
    }
    if ($t == 10) {
        while (1) {
            my $tt = byte_at($b, $i);
            $i += 1;
            return $i if $tt == 0;
            $i = skip($b, $i + 2 + u16($b, $i), $tt);
        }
    }
    return $i + 4 + 4 * i32($b, $i) if $t == 11;
    return $i + 4 + 8 * i32($b, $i) if $t == 12;
    die "unknown NBT tag $t\n";
}

sub nbt_name { my $s = $_[0]; return pack('n', length $s) . $s; }

sub entry_name {
    my ($e) = @_;
    my $i = 0;
    while (byte_at($e, $i) != 0) {
        my $t = byte_at($e, $i);
        my $n = u16($e, $i + 1);
        my $key = substr($e, $i + 3, $n);
        my $j = $i + 3 + $n;
        return substr($e, $j + 2, u16($e, $j)) if $t == 8 && $key eq 'name';
        $i = skip($e, $j, $t);
    }
    return undef;
}

sub servers {
    my ($path, $name, $ip) = @_;
    my ($head, @others, @entries) = ("\x0a\x00\x00");
    if (-e $path && -s $path) {
        my $b = slurp($path);
        die "servers.dat does not start with a compound\n" unless byte_at($b, 0) == 10;
        my $i = 3 + u16($b, 1);
        $head = substr($b, 0, $i);
        while (byte_at($b, $i) != 0) {
            my ($t, $start) = (byte_at($b, $i), $i);
            my $n = u16($b, $i + 1);
            my $key = substr($b, $i + 3, $n);
            my $j = $i + 3 + $n;
            if ($t == 9 && $key eq 'servers') {
                my ($et, $count) = (byte_at($b, $j), i32($b, $j + 1));
                my $k = $j + 5;
                die "servers is not a list of compounds\n" if $count && $et != 10;
                for (1 .. $count) {
                    my $s = $k;
                    $k = skip($b, $k, 10);
                    push @entries, substr($b, $s, $k - $s);
                }
                $i = $k;
            } else {
                $i = skip($b, $j, $t);
                push @others, substr($b, $start, $i - $start);
            }
        }
    }
    my $ours = "\x08" . nbt_name('name') . nbt_name($name) . "\x08" . nbt_name('ip') . nbt_name($ip) . "\x00";
    @entries = ($ours, grep { my $n = entry_name($_); !defined $n || $n ne $name } @entries);
    my $out = $head . join('', @others) . "\x09" . nbt_name('servers') . "\x0a" . pack('l>', scalar @entries)
      . join('', @entries) . "\x00";
    write_atomic($path, $out, 0);
}

my ($cmd, @args) = @ARGV;
if ($cmd eq 'hash') {
    my $sha = Digest::SHA->new($args[0]);
    $sha->addfile($args[1], 'b');
    print $sha->hexdigest, "\n";
} elsif ($cmd eq 'checkprofiles') {
    for my $path (@args) {
        my $lp = eval { read_json($path) };
        die_msg("$path is not valid JSON, so it was left alone. Open the Minecraft launcher once "
            . "(it repairs it), close it, and run this again.") unless defined $lp;
        die_msg("$path does not look like the launcher's list of installations, so it was left alone.")
          unless ref $lp eq 'HASH' && (!defined $lp->{profiles} || ref $lp->{profiles} eq 'HASH');
    }
} elsif ($cmd eq 'config') {
    my $path = $args[0];
    my $cfg = {};
    if (-e $path) {
        $cfg = eval { read_json($path) };
        unless (ref $cfg eq 'HASH') {
            rename $path, "$path.bad" or die "cannot rename $path: $!\n";
            $cfg = {};
            print "VFG: the old vfg-client.json did not read as JSON; it is kept as vfg-client.json.bad\n";
        }
    }
    $cfg->{server_address} = $ENV{VFG_ADDR};
    $cfg->{client_token} = $ENV{VFG_TOKEN};
    write_atomic($path, $JSON->encode($cfg), 1);
    my $bridge = $cfg->{bridge_url};
    if (defined $bridge && !ref $bridge && $bridge =~ /\S/) {
        print "VFG: note: bridge_url in $path is set to $bridge, so your voice goes there. "
          . "Leave it empty unless the server owner told you to set it.\n";
    }
} elsif ($cmd eq 'profile') {
    my ($path, $key, $name, $version_id, $game_dir, $now) = @args;
    my $lp = read_json($path);
    $lp->{profiles} = {} unless ref $lp->{profiles} eq 'HASH';
    my $p = $lp->{profiles}{$key};
    unless (ref $p eq 'HASH') {
        $p = { name => $name, type => 'custom', created => $now, icon => 'Crafting_Table' };
        $lp->{profiles}{$key} = $p;
    }
    @$p{qw(name type lastVersionId gameDir lastUsed)} = ($name, 'custom', $version_id, $game_dir, $now);
    my $out = $JSON->encode($lp);
    $JSON->decode($out);
    write_atomic($path, $out, 0);
} elsif ($cmd eq 'servers') {
    servers(@args);
} else {
    die_msg("unknown helper command $cmd");
}
PL

pick_helper() {
  local order h
  if [ -n "${VFG_INSTALL_HELPER:-}" ]; then
    HELPER=$VFG_INSTALL_HELPER
    return
  fi
  # On a Mac, python3 may be Apple's stub that asks to install developer tools; perl is always there.
  if [ "$OS" = mac ]; then order="perl python3"; else order="python3 perl"; fi
  for h in $order; do
    case $h in
      python3)
        if command -v python3 >/dev/null 2>&1 && python3 -c 'import json, hashlib, struct' >/dev/null 2>&1; then
          HELPER=python3
          return
        fi ;;
      perl)
        if command -v perl >/dev/null 2>&1 && perl -MJSON::PP -MDigest::SHA -e 1 >/dev/null 2>&1; then
          HELPER=perl
          return
        fi ;;
    esac
  done
  fail "This installer needs python3 or perl, and neither works on this computer.
Install python3 (your package manager has it), then run this again."
}

helper() {
  case $HELPER in
    python3) python3 "$WORK/helper.py" "$@" ;;
    perl) perl "$WORK/helper.pl" "$@" ;;
    *) fail "unknown helper $HELPER" ;;
  esac
}

pick_helper

# --- 2. The launcher rewrites its list of installations when it closes: wait for it ---

launcher_running() {
  # The launcher, and a game it started (its command line names the launcher).
  pgrep -f 'minecraft-launcher|Minecraft\.app/Contents/MacOS' >/dev/null 2>&1
}
if command -v pgrep >/dev/null 2>&1 && launcher_running; then
  say "Minecraft is open. Quit the game and close the Minecraft launcher:"
  say "it rewrites its list of installations when it closes. Waiting for it... (Ctrl+C to stop)"
  while launcher_running; do sleep 2; done
  say "Closed. Carrying on."
fi

PROFILE_FILES=()
for g in "${GAME_DIRS[@]}"; do
  for p in launcher_profiles.json launcher_profiles_microsoft_store.json; do
    if [ -f "$g/$p" ]; then PROFILE_FILES+=("$g/$p"); fi
  done
done
helper checkprofiles "${PROFILE_FILES[@]}"

# --- 3 and 4. The downloads, each checked before anything is installed ---

fetch() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL --retry 3 --retry-delay 2 --connect-timeout 20 -o "$2" "$1"
  elif command -v wget >/dev/null 2>&1; then
    wget -q --tries=3 --timeout=60 -O "$2" "$1"
  else
    fail "This installer needs curl or wget to download the mod. Install curl, then run this again."
  fi
}

get() {
  local url=$1 file=$2 alg=$3 want=$4 label=$5 got
  say "Downloading $label"
  fetch "$url" "$WORK/$file" || fail "Could not download $label. Check your internet connection and run this again.
If it keeps failing, the link has probably expired: ask the server owner for today's link."
  got=$(helper hash "$alg" "$WORK/$file")
  [ "$got" = "$want" ] || fail "$label failed its checksum: the download was damaged or changed, so nothing was installed.
Run this again; if it keeps failing, ask the server owner for a new link."
}

get "$PROFILE_JSON_URL" profile.json 256 "$PROFILE_JSON_SHA256" "Fabric Loader $LOADER_VERSION"
get "$FABRIC_API_URL" fabric-api.jar 512 "$FABRIC_API_SHA512" "Fabric API ($FABRIC_API_NAME)"
get "$VFG_JAR_URL" vfg.jar 256 "$VFG_JAR_SHA256" "the VFG mod ($VFG_JAR_NAME)"

NOW=$(date -u +%Y-%m-%dT%H:%M:%S.000Z)

put() { cp "$1" "$2.vfg-tmp" && mv -f "$2.vfg-tmp" "$2"; }

install_into() {
  local g=$1 v inst f p
  say "Installing into $g"

  # Fabric Loader, as its own installer writes it: the profile JSON and an empty jar beside it.
  v="$g/versions/$VERSION_ID"
  mkdir -p "$v"
  put "$WORK/profile.json" "$v/$VERSION_ID.json"
  : > "$v/$VERSION_ID.jar"

  # The game folder of its own, and its mods: this VFG and this Fabric API, no older copies.
  inst="$g/instances/$INSTANCE"
  mkdir -p "$inst/mods" "$inst/config"
  for f in "$inst/mods"/vfg-client-*.jar "$inst/mods"/fabric-api-*.jar; do
    [ -e "$f" ] || continue
    case ${f##*/} in
      "$VFG_JAR_NAME" | "$FABRIC_API_NAME") ;;
      *) rm -f "$f"; say "Removed the older ${f##*/}" ;;
    esac
  done
  put "$WORK/fabric-api.jar" "$inst/mods/$FABRIC_API_NAME"
  put "$WORK/vfg.jar" "$inst/mods/$VFG_JAR_NAME"

  # The mod's config: today's address and token; your mic and every other setting stay.
  VFG_ADDR=$SERVER_ADDRESS VFG_TOKEN=$CLIENT_TOKEN helper config "$inst/config/vfg-client.json"

  # The server list of that game folder.
  if ! helper servers "$inst/servers.dat" "$SERVER_NAME" "$SERVER_ADDRESS"; then
    say "Could not add the server to the list. In the game, add it by hand: $SERVER_ADDRESS"
  fi

  # The launcher's installation, after a backup of each list it keeps.
  for p in launcher_profiles.json launcher_profiles_microsoft_store.json; do
    [ -f "$g/$p" ] || continue
    [ -e "$g/$p.before-vfg" ] || cp -p "$g/$p" "$g/$p.before-vfg"
    cp -p "$g/$p" "$g/$p.vfg-bak"
    helper profile "$g/$p" "$PROFILE_KEY" "$PROFILE_NAME" "$VERSION_ID" "$inst" "$NOW"
  done
}

for g in "${GAME_DIRS[@]}"; do
  install_into "$g"
done

cat <<EOF

VFG: Done. VFG voice is installed.
  1. Open the Minecraft launcher, pick "$PROFILE_NAME" next to the Play button, and press Play.
  2. Multiplayer -> "$SERVER_NAME" ($SERVER_ADDRESS) -> Join Server.
  3. Press K in the game to pick your microphone. M mutes.
  The first start downloads the speech engine in the background; the mic icon shows its progress.
EOF
