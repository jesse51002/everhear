#!/usr/bin/env python3
"""The installer's tests: publish.sh against a stand-in aws and ssh, then the installers it
built, run against fake home folders, downloading from a local web server that stands in for
S3, Fabric's meta server and Modrinth.

    python3 installer/tests/test_installer.py            # everything
    python3 installer/tests/test_installer.py -k Ps      # unittest's own filters

Needs bash, python3 and perl. The PowerShell tests need pwsh (PowerShell 7) on PATH, at
$VFG_PWSH, or at ~/.local/opt/powershell-*/pwsh; without it they are skipped, and say so.
No real token, host, bucket or AWS call is involved: the token is made up per run, the
addresses are from the documentation ranges (203.0.113.0/24, 198.51.100.0/24).
"""
import functools
import glob
import hashlib
import http.server
import json
import os
import pathlib
import re
import secrets
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request

INSTALLER = pathlib.Path(__file__).resolve().parent.parent
REPO = INSTALLER.parent
MC, LOADER = "26.3", "0.19.5"
VERSION_ID = "fabric-loader-%s-%s" % (LOADER, MC)
FABRIC_API_NAME = "fabric-api-0.162.0+26.3.jar"
ADDR1, ADDR2 = "203.0.113.10", "198.51.100.77"


def find_pwsh():
    for c in [os.environ.get("VFG_PWSH"), shutil.which("pwsh")] + sorted(
            glob.glob(os.path.expanduser("~/.local/opt/powershell-*/pwsh")), reverse=True):
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


PWSH = find_pwsh()

# --- stand-ins -------------------------------------------------------------------------------

AWS_STUB = r'''#!/usr/bin/env python3
"""A stand-in aws: s3api head-bucket/create-bucket/put-bucket-lifecycle-configuration, s3 cp, s3 presign."""
import json, os, shutil, sys, hashlib
root, url, log = os.environ["FAKE_S3_ROOT"], os.environ["FAKE_S3_URL"], os.environ["FAKE_AWS_LOG"]
argv = sys.argv[1:]
def opt(name, default=None):
    return argv[argv.index(name) + 1] if name in argv else default
def err(msg, code=254):
    sys.stderr.write(msg + "\n"); sys.exit(code)
region = opt("--region")
if region != "us-east-2":
    err("stub: expected --region us-east-2, got %r" % region)
i = argv.index("--region")
argv = argv[:i] + argv[i + 2:]
with open(log, "a") as f:
    f.write(json.dumps(argv) + "\n")
if argv[0] == "s3api":
    bucket = opt("--bucket")
    if argv[1] == "head-bucket":
        if os.path.isdir(os.path.join(root, bucket)):
            sys.exit(0)
        err("An error occurred (404) when calling the HeadBucket operation: Not Found")
    if argv[1] == "create-bucket":
        if bucket in os.environ.get("FAKE_S3_TAKEN", "").split():
            err("An error occurred (BucketAlreadyExists) when calling the CreateBucket operation: taken")
        if opt("--create-bucket-configuration") != "LocationConstraint=us-east-2":
            err("stub: bad location constraint")
        os.makedirs(os.path.join(root, bucket))
        print(json.dumps({"Location": "http://%s.s3.amazonaws.com/" % bucket}))
        sys.exit(0)
    if argv[1] == "put-bucket-lifecycle-configuration":
        json.loads(opt("--lifecycle-configuration"))
        with open(os.path.join(root, bucket + ".lifecycle.json"), "w") as f:
            f.write(opt("--lifecycle-configuration"))
        sys.exit(0)
if argv[0] == "s3" and argv[1] == "cp":
    valued = {"--content-type", "--content-disposition", "--cache-control"}
    pos = [a for i, a in enumerate(argv[2:], 2) if not a.startswith("--") and argv[i - 1] not in valued]
    src, dst = pos
    bucket, key = dst[len("s3://"):].split("/", 1)
    if not os.path.isdir(os.path.join(root, bucket)):
        err("upload failed: NoSuchBucket")
    out = os.path.join(root, bucket, key)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    shutil.copyfile(src, out)
    with open(out + ".meta", "w") as f:
        json.dump({"content_type": opt("--content-type"), "disposition": opt("--content-disposition")}, f)
    sys.exit(0)
if argv[0] == "s3" and argv[1] == "presign":
    target = [a for a in argv[2:] if a.startswith("s3://")][0]
    bucket, key = target[len("s3://"):].split("/", 1)
    sig = hashlib.sha256(key.encode()).hexdigest()
    q = ("X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIAEXAMPLE%2F20261008%2Fus-east-2%2Fs3%2Faws4_request"
         "&X-Amz-Date=20261008T120000Z&X-Amz-Expires=" + opt("--expires-in") + "&X-Amz-SignedHeaders=host")
    if os.environ.get("FAKE_AWS_TEMP_CREDS"):
        q += "&X-Amz-Security-Token=FwoGZXIvYXdzEXAMPLE"
    print("%s/%s/%s?%s&X-Amz-Signature=%s" % (url, bucket, key, q, sig))
    sys.exit(0)
err("stub: unexpected aws call %r" % argv, 2)
'''

SSH_STUB = r'''#!/usr/bin/env python3
import json, os, sys
with open(os.environ["FAKE_SSH_LOG"], "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\n")
if os.environ.get("FAKE_SSH_FAIL"):
    sys.stderr.write("ssh: connect to host: Connection timed out\n"); sys.exit(255)
if "VFG_CLIENT_TOKEN" in sys.argv[-1] and "/opt/vfg/.env" in sys.argv[-1]:
    sys.stdout.write(os.environ["FAKE_TOKEN_LINE"] + "\n"); sys.exit(0)
sys.stderr.write("ssh stub: unexpected command\n"); sys.exit(1)
'''

# Running the launcher: the count file says how many more checks see it running.
PGREP_STUB = r'''#!/usr/bin/env bash
f=${FAKE_PGREP_COUNT:-}
if [ -n "$f" ] && [ -f "$f" ]; then
  n=$(cat "$f")
  if [ "$n" -gt 0 ]; then echo $((n - 1)) > "$f"; echo 4242; exit 0; fi
fi
exit 1
'''

UNAME_DARWIN = r'''#!/usr/bin/env bash
if [ "${1:-}" = "-s" ]; then echo Darwin; else exec /usr/bin/uname "$@"; fi
'''

# Get-Process for the PowerShell tests: same count file.
PS_PRELUDE = r'''
function global:Get-Process {
	[CmdletBinding()] param([string[]]$Name)
	$f = $env:FAKE_PGREP_COUNT
	if ($f -and (Test-Path -LiteralPath $f)) {
		$n = [int](Get-Content -LiteralPath $f)
		if ($n -gt 0) { Set-Content -LiteralPath $f ($n - 1); return [pscustomobject]@{ ProcessName = 'MinecraftLauncher'; Id = 4242 } }
	}
}
'''


def write_exec(path, text):
    path.write_text(text)
    path.chmod(0o755)


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


# --- NBT, read independently of the installers' own code ---------------------------------------

def nbt_read(b):
    pos = [0]

    def take(n):
        v = b[pos[0]:pos[0] + n]
        assert len(v) == n, "truncated NBT"
        pos[0] += n
        return v

    def string():
        return take(struct.unpack(">H", take(2))[0]).decode("utf-8")

    def payload(t):
        if t == 1: return struct.unpack(">b", take(1))[0]
        if t == 2: return struct.unpack(">h", take(2))[0]
        if t == 3: return struct.unpack(">i", take(4))[0]
        if t == 4: return struct.unpack(">q", take(8))[0]
        if t == 5: return struct.unpack(">f", take(4))[0]
        if t == 6: return struct.unpack(">d", take(8))[0]
        if t == 7: return take(struct.unpack(">i", take(4))[0])
        if t == 8: return string()
        if t == 9:
            et, n = take(1)[0], struct.unpack(">i", take(4))[0]
            return [payload(et) for _ in range(n)]
        if t == 10:
            out = {}
            while True:
                tt = take(1)[0]
                if tt == 0:
                    return out
                name = string()
                out[name] = payload(tt)
        raise AssertionError("tag %d" % t)

    assert take(1)[0] == 10
    string()
    root = payload(10)
    assert pos[0] == len(b), "trailing bytes in servers.dat"
    return root


def nbt_string(name, value):
    raw_n, raw_v = name.encode(), value.encode()
    return b"\x08" + struct.pack(">H", len(raw_n)) + raw_n + struct.pack(">H", len(raw_v)) + raw_v


def nbt_byte(name, value):
    raw_n = name.encode()
    return b"\x01" + struct.pack(">H", len(raw_n)) + raw_n + struct.pack(">b", value)


def servers_dat(entries, extra=b""):
    """A servers.dat as the game writes it, plus optional extra root tags."""
    out = b"\x0a\x00\x00" + extra + b"\x09\x00\x07servers\x0a" + struct.pack(">i", len(entries))
    for e in entries:
        out += e + b"\x00"
    return out + b"\x00"


LAUNCHER_PROFILES = {
    "profiles": {
        "b1946ac92492d2347c6235b4d2611184": {
            "created": "1970-01-02T00:00:00.000Z", "icon": "Grass", "lastUsed": "2026-10-01T18:22:11.120Z",
            "lastVersionId": "latest-release", "name": "", "type": "latest-release"},
        "4a7f0c": {
            "created": "2025-05-05T10:00:00.000Z", "gameDir": "/somewhere/modpack", "icon": "Furnace",
            "javaArgs": "-Xmx4G -XX:+UseG1GC", "lastUsed": "2026-09-30T21:00:00.000Z",
            "lastVersionId": "fabric-loader-0.16.0-1.21", "name": "My modpack \u2728 caf\u00e9", "type": "custom",
            "resolution": {"height": 480, "width": 854}},
    },
    "settings": {"crashAssistance": True, "enableAdvanced": False, "enableAnalytics": True,
                 "enableHistorical": False, "enableReleases": True, "enableSnapshots": False,
                 "keepLauncherOpen": False, "profileSorting": "ByLastPlayed", "showGameLog": False,
                 "showMenu": False, "soundOn": False},
    "version": 3,
}


def sha(alg, path):
    return hashlib.new(alg, pathlib.Path(path).read_bytes()).hexdigest()


def load_json(path):
    raw = pathlib.Path(path).read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "%s has a byte-order mark" % path
    return json.loads(raw.decode("utf-8"))


# --- the shared fixture: a web server, the stand-ins, and two publishes -------------------------

class World:
    """Everything the tests share: built once per run."""

    def __init__(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="vfg-installer-test-"))
        self.www = self.tmp / "www"
        (self.www / "s3").mkdir(parents=True)
        self.stubs = self.tmp / "stubs"
        self.stubs.mkdir()
        self.mac_stubs = self.tmp / "mac-stubs"
        self.mac_stubs.mkdir()
        write_exec(self.stubs / "aws", AWS_STUB)
        write_exec(self.stubs / "ssh", SSH_STUB)
        write_exec(self.stubs / "pgrep", PGREP_STUB)
        write_exec(self.mac_stubs / "uname", UNAME_DARWIN)

        handler = functools.partial(QuietHandler, directory=str(self.www))
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]

        # Fabric's meta server and Modrinth's CDN.
        meta = self.www / "meta/v2/versions/loader" / MC / LOADER / "profile"
        meta.mkdir(parents=True)
        self.profile_json = {
            "id": VERSION_ID, "inheritsFrom": MC, "releaseTime": "2026-10-07T15:06:27+0000",
            "time": "2026-10-07T15:06:27+0000", "type": "release",
            "mainClass": "net.fabricmc.loader.impl.launch.knot.KnotClient",
            "arguments": {"game": [], "jvm": ["-DFabricMcEmu= net.minecraft.client.main.Main "]},
            "libraries": [{"name": "net.fabricmc:fabric-loader:" + LOADER, "url": "https://maven.fabricmc.net/"}]}
        (meta / "json").write_text(json.dumps(self.profile_json))
        cdn = self.www / "cdn"
        cdn.mkdir()
        (cdn / FABRIC_API_NAME).write_bytes(b"PK\x03\x04 fake fabric api " + secrets.token_bytes(4096))
        self.fabric_sha512 = sha("sha512", cdn / FABRIC_API_NAME)

        self.libs = self.tmp / "libs"
        self.libs.mkdir()
        self.owner_home = self.tmp / "owner-home"
        (self.owner_home / ".vfg").mkdir(parents=True)
        self.key = self.tmp / "not-a-key.pem"
        self.key.write_text("not a key\n")
        self.config = self.owner_home / ".vfg/config"
        self.config.write_text("# test config\nVFG_SSH_HOST=%s\nVFG_SSH_KEY=%s\n" % (ADDR1, self.key))
        self.aws_log = self.tmp / "aws.log"
        self.ssh_log = self.tmp / "ssh.log"

        # Two publishes, as two sessions: a new IP, a new token and a newer jar the second time.
        self.token1 = "test-token-" + secrets.token_urlsafe(18)
        self.token2 = "test-token-" + secrets.token_urlsafe(18)
        self.jar1 = self.make_jar("vfg-client-0.1.0.jar", age=100)
        self.pub1 = self.publish(self.token1)
        self.set_host(ADDR2)
        self.jar2 = self.make_jar("vfg-client-0.1.1.jar", age=10)
        self.pub2 = self.publish(self.token2)

    def make_jar(self, name, age):
        p = self.libs / name
        p.write_bytes(b"PK\x03\x04 fake vfg jar " + name.encode() + secrets.token_bytes(2048))
        t = time.time() - age
        os.utime(p, (t, t))
        return p

    def set_host(self, host):
        text = re.sub(r"(?m)^VFG_SSH_HOST=.*$", "VFG_SSH_HOST=" + host, self.config.read_text())
        self.config.write_text(text)

    def publish_env(self, token_line, **extra):
        env = {
            "PATH": "%s:/usr/bin:/bin" % self.stubs, "HOME": str(self.owner_home), "LANG": "C.UTF-8",
            "TMPDIR": str(self.tmp), "XDG_CACHE_HOME": str(self.tmp / "cache"),
            "FAKE_S3_ROOT": str(self.www / "s3"), "FAKE_S3_URL": self.base + "/s3",
            "FAKE_AWS_LOG": str(self.aws_log), "FAKE_SSH_LOG": str(self.ssh_log), "FAKE_TOKEN_LINE": token_line,
            "VFG_CLIENT_JAR_DIR": str(self.libs), "VFG_FABRIC_META_URL": self.base + "/meta",
            "VFG_FABRIC_API_URL": self.base + "/cdn/" + urllib.parse.quote(FABRIC_API_NAME),
            "VFG_FABRIC_API_SHA512": self.fabric_sha512,
        }
        env.update(extra)
        return env

    def publish(self, token, token_line=None, expect_ok=True, **extra):
        aws_before = self.aws_log.read_text() if self.aws_log.exists() else ""
        r = subprocess.run([str(INSTALLER / "publish.sh")], env=self.publish_env(token_line or token, **extra),
                           capture_output=True, text=True, timeout=120)
        aws_calls = [json.loads(l) for l in self.aws_log.read_text()[len(aws_before):].splitlines()] \
            if self.aws_log.exists() else []
        pub = {"rc": r.returncode, "stdout": r.stdout, "stderr": r.stderr, "aws": aws_calls, "token": token}
        if expect_ok:
            assert r.returncode == 0, "publish failed:\n" + r.stderr
            lines = r.stdout.splitlines()
            pub["page_url"] = lines[0]
            page = urllib.request.urlopen(lines[0]).read().decode("utf-8")
            pub["page"] = page
            links = dict(re.findall(r'href="([^"]+)" download="(vfg-install\.(?:cmd|sh))"', page))
            links = {v: k.replace("&amp;", "&") for k, v in links.items()}
            ps = re.search(r"<code id=\"ps\">irm &#x27;(.+?)&#x27; \| iex</code>", page)
            links["vfg-install.ps1"] = ps.group(1).replace("&amp;", "&")
            pub["dir"] = self.tmp / ("pub-%s" % secrets.token_hex(4))
            pub["dir"].mkdir()
            for name, url in links.items():
                (pub["dir"] / name).write_bytes(urllib.request.urlopen(url).read())
            pub["links"] = links
        return pub

    def home(self, name):
        h = self.tmp / "homes" / (name + "-" + secrets.token_hex(3))
        h.mkdir(parents=True)
        return h

    def close(self):
        self.server.shutdown()
        shutil.rmtree(self.tmp, ignore_errors=True)


WORLD = None


def world():
    global WORLD
    if WORLD is None:
        WORLD = World()
    return WORLD


def tearDownModule():
    if WORLD is not None:
        WORLD.close()


# --- publish.sh ---------------------------------------------------------------------------------

class PublishTest(unittest.TestCase):
    def test_prints_only_the_link_and_the_expiry(self):
        p = world().pub1
        lines = p["stdout"].splitlines()
        self.assertEqual(len(lines), 2, p["stdout"])
        self.assertTrue(lines[0].startswith(world().base + "/s3/vfg-installer-"), lines[0])
        self.assertIn("/index.html?", lines[0])
        self.assertRegex(lines[1], r"^expires \w{3} \d{2} \w{3} 20\d\d \d\d:\d\d \S+$")
        self.assertEqual(p["stderr"], "")

    def test_token_never_printed_or_logged(self):
        w = world()
        for p in (w.pub1, w.pub2):
            for text in (p["stdout"], p["stderr"], w.aws_log.read_text(), w.ssh_log.read_text()):
                self.assertNotIn(p["token"], text)
            # ...but it is in the installers, which is the point.
            for name in ("vfg-install.sh", "vfg-install.ps1", "vfg-install.cmd"):
                self.assertIn(p["token"], (p["dir"] / name).read_text())
            self.assertNotIn(p["token"], p["page"])

    def test_ssh_uses_the_deploy_scripts_options(self):
        calls = [json.loads(l) for l in world().ssh_log.read_text().splitlines()]
        self.assertTrue(calls)
        for argv in calls:
            self.assertIn("BatchMode=yes", argv)
            self.assertTrue({"ec2-user@" + ADDR1, "ec2-user@" + ADDR2} & set(argv), argv)
            self.assertIn("/opt/vfg/.env", argv[-1])

    def test_bucket_made_once_private_and_remembered(self):
        w = world()
        made = [c for c in w.pub1["aws"] if c[:2] == ["s3api", "create-bucket"]]
        self.assertEqual(len(made), 1)
        bucket = made[0][made[0].index("--bucket") + 1]
        self.assertRegex(bucket, r"^vfg-installer-[0-9a-f]{8}$")
        self.assertIn("VFG_INSTALL_BUCKET=" + bucket, w.config.read_text().splitlines())
        # Nothing ever turns Block Public Access off or adds an ACL or a bucket policy.
        for c in w.pub1["aws"] + w.pub2["aws"]:
            self.assertFalse(any(x in ("put-public-access-block", "delete-public-access-block", "put-bucket-policy",
                                       "put-bucket-acl", "put-object-acl") for x in c), c)
            self.assertNotIn("--acl", c)
        lifecycle = json.loads((w.www / "s3" / (bucket + ".lifecycle.json")).read_text())
        self.assertEqual(lifecycle["Rules"][0]["Expiration"]["Days"], 8)
        # The second publish reuses it.
        self.assertFalse([c for c in w.pub2["aws"] if c[:2] == ["s3api", "create-bucket"]])
        self.assertTrue([c for c in w.pub2["aws"] if c[:2] == ["s3api", "head-bucket"]])

    def test_objects_types_and_one_random_prefix_per_publish(self):
        w = world()
        prefixes = []
        for p in (w.pub1, w.pub2):
            cps = [c for c in p["aws"] if c[:2] == ["s3", "cp"]]
            keys = [c[-1].split("/", 3)[3] for c in cps]
            prefix = {k.split("/", 1)[0] for k in keys}
            self.assertEqual(len(prefix), 1, keys)
            prefix = prefix.pop()
            self.assertRegex(prefix, r"^\d{8}-[0-9a-f]{24}$")
            prefixes.append(prefix)
            names = sorted(k.split("/", 1)[1] for k in keys)
            jar = "vfg-client-0.1.0.jar" if p is w.pub1 else "vfg-client-0.1.1.jar"
            self.assertEqual(names, sorted(["files/" + jar, "files/" + FABRIC_API_NAME, "files/%s.json" % VERSION_ID,
                                            "vfg-install.sh", "vfg-install.cmd", "vfg-install.ps1", "index.html"]))
            presigns = [c for c in p["aws"] if c[:2] == ["s3", "presign"]]
            self.assertEqual(len(presigns), 7)
            for c in presigns:
                self.assertEqual(c[c.index("--expires-in") + 1], "604800")
            # The page is uploaded last, after the installers it links to.
            self.assertTrue(cps[-1][-1].endswith("/index.html"))
            bucket = cps[0][-1].split("/")[2]
            meta = lambda k: json.loads((w.www / "s3" / bucket / prefix / (k + ".meta")).read_text())
            self.assertEqual(meta("index.html")["content_type"], "text/html; charset=utf-8")
            self.assertIsNone(meta("index.html")["disposition"])
            self.assertEqual(meta("vfg-install.ps1")["content_type"], "text/plain; charset=utf-8")
            self.assertEqual(meta("vfg-install.cmd")["disposition"], 'attachment; filename="vfg-install.cmd"')
            self.assertEqual(meta("vfg-install.sh")["disposition"], 'attachment; filename="vfg-install.sh"')
        self.assertNotEqual(prefixes[0], prefixes[1])

    def test_installers_carry_the_session(self):
        w = world()
        for p, addr, jar in ((w.pub1, ADDR1, w.jar1), (w.pub2, ADDR2, w.jar2)):
            sh = (p["dir"] / "vfg-install.sh").read_text()
            self.assertIn("SERVER_ADDRESS=" + addr + "\n", sh)
            self.assertIn("VFG_JAR_NAME=%s\n" % jar.name, sh)
            self.assertIn("VFG_JAR_SHA256=%s\n" % sha("sha256", jar), sh)
            self.assertIn("FABRIC_API_SHA512=%s\n" % w.fabric_sha512, sh)
            ps1 = (p["dir"] / "vfg-install.ps1").read_text()
            self.assertIn("$ServerAddress = '%s'" % addr, ps1)
            self.assertIn("$ClientToken = '%s'" % p["token"], ps1)
            self.assertIn(addr, p["page"])
            self.assertNotIn("@@", sh + ps1 + p["page"])

    def test_page_links_are_the_presigned_urls(self):
        p = world().pub1
        for name, url in p["links"].items():
            self.assertIn("/" + name + "?", url)
            self.assertIn("X-Amz-Signature=", url)
            self.assertIn("X-Amz-Expires=604800", url)
        self.assertIn("More info", p["page"])
        self.assertIn("Run anyway", p["page"])

    def test_cmd_is_crlf_and_ascii(self):
        raw = (world().pub1["dir"] / "vfg-install.cmd").read_bytes()
        raw.decode("ascii")
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))
        self.assertTrue(raw.startswith(b"@echo off\r\n"))
        self.assertIn(b"\r\n##VFG-POWERSHELL##\r\n", raw)

    def test_newest_jar_wins_and_sources_jars_are_skipped(self):
        w = world()
        src = w.libs / "vfg-client-0.1.1-sources.jar"
        src.write_bytes(b"PK sources")
        try:
            p = w.publish(w.token2)
            self.assertIn("VFG_JAR_NAME=vfg-client-0.1.1.jar\n", (p["dir"] / "vfg-install.sh").read_text())
        finally:
            src.unlink()

    def test_bad_fabric_api_checksum_uploads_nothing(self):
        w = world()
        shutil.rmtree(w.tmp / "cache", ignore_errors=True)
        p = w.publish(w.token2, expect_ok=False, VFG_FABRIC_API_SHA512="0" * 128)
        self.assertNotEqual(p["rc"], 0)
        self.assertIn("does not match Modrinth's sha512", p["stderr"])
        self.assertFalse([c for c in p["aws"] if c[:2] == ["s3", "cp"]])
        self.assertEqual(p["stdout"], "")

    def test_quoted_token_in_env_file(self):
        w = world()
        token = "test-token-" + secrets.token_urlsafe(12)
        p = w.publish(token, token_line='"%s"' % token)
        self.assertIn("$ClientToken = '%s'\n" % token, (p["dir"] / "vfg-install.ps1").read_text())
        self.assertNotIn(token, p["stdout"] + p["stderr"])

    def test_ssh_failure_is_one_clear_line(self):
        w = world()
        p = w.publish(w.token2, expect_ok=False, FAKE_SSH_FAIL="1")
        self.assertNotEqual(p["rc"], 0)
        self.assertIn("could not read VFG_CLIENT_TOKEN", p["stderr"])
        self.assertFalse([c for c in p["aws"] if c[:2] == ["s3", "cp"]])

    def test_temporary_credentials_warn(self):
        w = world()
        p = w.publish(w.token2, FAKE_AWS_TEMP_CREDS="1")
        self.assertIn("temporary", p["stderr"])
        self.assertEqual(len(p["stdout"].splitlines()), 2)


# --- the installers, against fake homes ------------------------------------------------------------

class InstallerCases:
    """The cases every installer must pass. Subclasses say how to run one and where Minecraft is."""

    def run_installer(self, pub, home, **env):  # -> CompletedProcess
        raise NotImplementedError

    def game_dirs(self, home):  # -> [the game folder(s) this layout has]
        raise NotImplementedError

    # helpers

    def make_game(self, game, profiles=True, store=False):
        game.mkdir(parents=True, exist_ok=True)
        if profiles:
            (game / "launcher_profiles.json").write_text(json.dumps(LAUNCHER_PROFILES, indent=2, ensure_ascii=False),
                                                       encoding="utf-8")
        if store:
            data = json.loads(json.dumps(LAUNCHER_PROFILES))
            data["profiles"]["store-only"] = {"name": "Store profile", "type": "custom", "lastVersionId": "1.21.4",
                                              "created": "2026-01-01T00:00:00.000Z"}
            (game / "launcher_profiles_microsoft_store.json").write_text(json.dumps(data), encoding="utf-8")
        return game

    def ok(self, r):
        self.assertEqual(r.returncode, 0, "installer failed\nSTDOUT:\n%s\nSTDERR:\n%s" % (r.stdout, r.stderr))
        return r

    def assert_installed(self, game, pub, addr, token, jar, backup=".before-vfg"):
        w = world()
        inst = game / "instances" / ("vfg-" + MC)
        # Fabric Loader, as its installer writes it.
        vdir = game / "versions" / VERSION_ID
        self.assertEqual(load_json(vdir / (VERSION_ID + ".json")), w.profile_json)
        self.assertEqual((vdir / (VERSION_ID + ".jar")).stat().st_size, 0)
        # Mods: exactly these two, byte for byte.
        mods = sorted(p.name for p in (inst / "mods").iterdir())
        self.assertEqual(mods, sorted([jar.name, FABRIC_API_NAME]))
        self.assertEqual(sha("sha256", inst / "mods" / jar.name), sha("sha256", jar))
        self.assertEqual(sha("sha512", inst / "mods" / FABRIC_API_NAME), w.fabric_sha512)
        # Config.
        cfg = load_json(inst / "config" / "vfg-client.json")
        self.assertEqual(cfg["server_address"], addr)
        self.assertEqual(cfg["client_token"], token)
        # Server list.
        servers = nbt_read((inst / "servers.dat").read_bytes())["servers"]
        self.assertEqual(servers[0], {"name": "VFG", "ip": addr})
        self.assertEqual(sum(1 for s in servers if s.get("name") == "VFG"), 1)
        # Launcher profile(s): ours added, everything else exactly as it was.
        for name in ("launcher_profiles.json", "launcher_profiles_microsoft_store.json"):
            path = game / name
            if not path.exists():
                continue
            lp = load_json(path)
            before = load_json(str(path) + backup)
            ours = lp["profiles"].pop("vfg-" + MC)
            self.assertEqual(ours["name"], "VFG " + MC)
            self.assertEqual(ours["type"], "custom")
            self.assertEqual(ours["lastVersionId"], VERSION_ID)
            self.assertEqual(pathlib.Path(ours["gameDir"]), inst)
            self.assertRegex(ours["lastUsed"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
            before["profiles"].pop("vfg-" + MC, None)
            self.assertEqual(lp, before)
        return inst

    # cases

    def test_fresh_install(self):
        w = world()
        home = w.home("fresh")
        game = self.make_game(self.game_dirs(home)[0])
        original = (game / "launcher_profiles.json").read_bytes()
        r = self.ok(self.run_installer(w.pub1, home))
        self.assertIn("Done", r.stdout)
        self.assertIn("press K", r.stdout.replace("Press K", "press K"))
        self.assertNotIn(w.pub1["token"], r.stdout + r.stderr)
        self.assert_installed(game, w.pub1, ADDR1, w.token1, w.jar1)
        self.assertEqual((game / "launcher_profiles.json.before-vfg").read_bytes(), original)
        self.assertEqual((game / "launcher_profiles.json.vfg-bak").read_bytes(), original)
        self.assertEqual(load_json(game / "instances" / ("vfg-" + MC) / "config" / "vfg-client.json"),
                         {"server_address": ADDR1, "client_token": w.token1})
        leftovers = [p for p in game.rglob("*") if p.name.endswith(".vfg-tmp")]
        self.assertEqual(leftovers, [])

    def test_both_launcher_profile_files(self):
        w = world()
        home = w.home("store")
        game = self.make_game(self.game_dirs(home)[0], store=True)
        store_before = (game / "launcher_profiles_microsoft_store.json").read_bytes()
        self.ok(self.run_installer(w.pub1, home))
        self.assert_installed(game, w.pub1, ADDR1, w.token1, w.jar1)
        self.assertEqual((game / "launcher_profiles_microsoft_store.json.before-vfg").read_bytes(), store_before)
        store = load_json(game / "launcher_profiles_microsoft_store.json")
        self.assertIn("store-only", store["profiles"])
        self.assertIn("vfg-" + MC, store["profiles"])

    def test_only_the_store_profile_file(self):
        w = world()
        home = w.home("store-only")
        game = self.make_game(self.game_dirs(home)[0], profiles=False, store=True)
        self.ok(self.run_installer(w.pub1, home))
        self.assertFalse((game / "launcher_profiles.json").exists())
        self.assert_installed(game, w.pub1, ADDR1, w.token1, w.jar1)

    def test_existing_config_keeps_the_mic_and_everything_else(self):
        w = world()
        home = w.home("mic")
        game = self.make_game(self.game_dirs(home)[0])
        cfg_dir = game / "instances" / ("vfg-" + MC) / "config"
        cfg_dir.mkdir(parents=True)
        old = {"server_address": "192.0.2.1", "bridge_url": "", "client_token": "old-token", "mic_device":
               "Yeti Stereo Microphone Analog Stereo", "model": "moonshine-tiny-en", "icon_corner": "bottom_right",
               "muted": True, "silence_seconds": 4.5, "vad_threshold": 0.6, "stt_threads": 3, "log_text": False,
               "a_key_from_a_newer_mod": {"nested": [1, 2, 3]}}
        (cfg_dir / "vfg-client.json").write_text(json.dumps(old, indent=2))
        r = self.ok(self.run_installer(w.pub1, home))
        cfg = load_json(cfg_dir / "vfg-client.json")
        want = dict(old, server_address=ADDR1, client_token=w.token1)
        self.assertEqual(cfg, want)
        self.assertNotIn("bridge_url", r.stdout)
        if os.name == "posix" and not isinstance(self, PsCases):
            self.assertEqual(os.stat(cfg_dir / "vfg-client.json").st_mode & 0o777, 0o600)

    def test_bridge_url_set_by_hand_is_kept_and_mentioned(self):
        w = world()
        home = w.home("bridge")
        game = self.make_game(self.game_dirs(home)[0])
        cfg_dir = game / "instances" / ("vfg-" + MC) / "config"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "vfg-client.json").write_text('{"bridge_url": "http://192.0.2.9:8787", "mic_device": "USB"}')
        r = self.ok(self.run_installer(w.pub1, home))
        cfg = load_json(cfg_dir / "vfg-client.json")
        self.assertEqual(cfg["bridge_url"], "http://192.0.2.9:8787")
        self.assertEqual(cfg["mic_device"], "USB")
        self.assertIn("bridge_url", r.stdout)

    def test_unreadable_config_is_kept_as_bad(self):
        w = world()
        home = w.home("badcfg")
        game = self.make_game(self.game_dirs(home)[0])
        cfg_dir = game / "instances" / ("vfg-" + MC) / "config"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "vfg-client.json").write_text("{ not json")
        self.ok(self.run_installer(w.pub1, home))
        self.assertEqual((cfg_dir / "vfg-client.json.bad").read_text(), "{ not json")
        self.assertEqual(load_json(cfg_dir / "vfg-client.json"), {"server_address": ADDR1, "client_token": w.token1})

    def test_rerun_next_session_updates_and_keeps(self):
        w = world()
        home = w.home("rerun")
        game = self.make_game(self.game_dirs(home)[0])
        original = (game / "launcher_profiles.json").read_bytes()
        self.ok(self.run_installer(w.pub1, home))
        inst = game / "instances" / ("vfg-" + MC)
        # Between sessions: the player picks a mic, adds a server, gives the profile more memory,
        # and the launcher adds a profile of its own.
        cfg = load_json(inst / "config" / "vfg-client.json")
        cfg["mic_device"] = "Blue Yeti"
        (inst / "config" / "vfg-client.json").write_text(json.dumps(cfg))
        friend = nbt_string("name", "Friend's server") + nbt_string("ip", "friend.example.org") \
            + nbt_byte("acceptTextures", 1) + nbt_string("icon", "aGVsbG8=")
        dat = nbt_read((inst / "servers.dat").read_bytes())
        self.assertEqual(dat["servers"], [{"name": "VFG", "ip": ADDR1}])
        (inst / "servers.dat").write_bytes(servers_dat(
            [nbt_string("name", "VFG") + nbt_string("ip", ADDR1) + nbt_byte("hidden", 0), friend]))
        lp = load_json(game / "launcher_profiles.json")
        lp["profiles"]["vfg-" + MC]["javaArgs"] = "-Xmx3G"
        lp["profiles"]["new-one"] = {"name": "Added later", "type": "custom", "lastVersionId": "1.21.5"}
        (game / "launcher_profiles.json").write_text(json.dumps(lp, indent=2, ensure_ascii=False), encoding="utf-8")
        after_first = (game / "launcher_profiles.json").read_bytes()
        stray = inst / "mods" / "vfg-client-0.0.9.jar"
        stray.write_bytes(b"old")
        other_mod = inst / "mods" / "sodium-0.6.jar"
        other_mod.write_bytes(b"someone else's mod")

        r = self.ok(self.run_installer(w.pub2, home))
        self.assertIn("Removed the older vfg-client-0.1.0.jar", r.stdout)
        self.assertFalse(stray.exists())
        self.assertFalse((inst / "mods" / "vfg-client-0.1.0.jar").exists())
        self.assertTrue(other_mod.exists())
        other_mod.unlink()
        self.assert_installed(game, w.pub2, ADDR2, w.token2, w.jar2, backup=".vfg-bak")
        cfg = load_json(inst / "config" / "vfg-client.json")
        self.assertEqual(cfg["mic_device"], "Blue Yeti")
        servers = nbt_read((inst / "servers.dat").read_bytes())["servers"]
        self.assertEqual(servers, [{"name": "VFG", "ip": ADDR2},
                                   {"name": "Friend's server", "ip": "friend.example.org", "acceptTextures": 1,
                                    "icon": "aGVsbG8="}])
        lp = load_json(game / "launcher_profiles.json")
        self.assertEqual(lp["profiles"]["vfg-" + MC]["javaArgs"], "-Xmx3G")
        self.assertIn("new-one", lp["profiles"])
        self.assertEqual(sum(1 for p in lp["profiles"].values() if p.get("name") == "VFG " + MC), 1)
        # The first backup is the untouched original; the last is the list just before this run.
        self.assertEqual((game / "launcher_profiles.json.before-vfg").read_bytes(), original)
        self.assertEqual((game / "launcher_profiles.json.vfg-bak").read_bytes(), after_first)

    def test_rerun_with_the_same_link_changes_nothing_else(self):
        w = world()
        home = w.home("same")
        game = self.make_game(self.game_dirs(home)[0])
        self.ok(self.run_installer(w.pub1, home))
        lp1 = load_json(game / "launcher_profiles.json")
        self.ok(self.run_installer(w.pub1, home))
        lp2 = load_json(game / "launcher_profiles.json")
        lp1["profiles"]["vfg-" + MC].pop("lastUsed")
        lp2["profiles"]["vfg-" + MC].pop("lastUsed")
        self.assertEqual(lp1, lp2)
        self.assert_installed(game, w.pub1, ADDR1, w.token1, w.jar1)

    def test_extra_root_tags_in_servers_dat_survive(self):
        w = world()
        home = w.home("nbt")
        game = self.make_game(self.game_dirs(home)[0])
        inst = game / "instances" / ("vfg-" + MC)
        inst.mkdir(parents=True)
        extra = b"\x03\x00\x07version" + struct.pack(">i", 3)
        (inst / "servers.dat").write_bytes(servers_dat([nbt_string("name", "A") + nbt_string("ip", "a.example")], extra))
        self.ok(self.run_installer(w.pub1, home))
        dat = nbt_read((inst / "servers.dat").read_bytes())
        self.assertEqual(dat["version"], 3)
        self.assertEqual(dat["servers"], [{"name": "VFG", "ip": ADDR1}, {"name": "A", "ip": "a.example"}])

    def test_no_minecraft_folder(self):
        w = world()
        home = w.home("none")
        self.game_dirs(home)[0].mkdir(parents=True)  # a folder, but the launcher never ran
        r = self.run_installer(w.pub1, home)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("run the Minecraft launcher once first", r.stdout + r.stderr)
        self.assertEqual(list(self.game_dirs(home)[0].rglob("*")), [])

    def test_waits_for_the_launcher_to_close(self):
        w = world()
        home = w.home("running")
        game = self.make_game(self.game_dirs(home)[0])
        count = home.parent / ("pgrep-" + home.name)
        count.write_text("2")
        t0 = time.time()
        r = self.ok(self.run_installer(w.pub1, home, FAKE_PGREP_COUNT=str(count)))
        self.assertGreaterEqual(time.time() - t0, 1.5)
        self.assertIn("Close", r.stdout)
        self.assertIn("Closed. Carrying on.", r.stdout)
        self.assertEqual(count.read_text().strip(), "0")
        self.assert_installed(game, w.pub1, ADDR1, w.token1, w.jar1)

    def test_tampered_download_installs_nothing(self):
        w = world()
        home = w.home("tamper")
        game = self.make_game(self.game_dirs(home)[0])
        url = urllib.parse.urlsplit(w.pub2["links"]["vfg-install.sh"])
        prefix_dir = w.www / urllib.parse.unquote(url.path).lstrip("/").rsplit("/", 1)[0]
        jar = prefix_dir / "files" / w.jar2.name
        good = jar.read_bytes()
        jar.write_bytes(good + b"evil")
        try:
            r = self.run_installer(w.pub2, home)
        finally:
            jar.write_bytes(good)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("failed its checksum", r.stdout + r.stderr)
        self.assertFalse((game / "instances").exists())
        self.assertFalse((game / "versions").exists())
        self.assertEqual(load_json(game / "launcher_profiles.json"), LAUNCHER_PROFILES)

    def test_expired_link_says_so(self):
        w = world()
        home = w.home("expired")
        game = self.make_game(self.game_dirs(home)[0])
        url = urllib.parse.urlsplit(w.pub2["links"]["vfg-install.sh"])
        prefix_dir = w.www / urllib.parse.unquote(url.path).lstrip("/").rsplit("/", 1)[0]
        f = prefix_dir / "files" / FABRIC_API_NAME
        hidden = f.with_name("hidden")
        f.rename(hidden)
        try:
            r = self.run_installer(w.pub2, home)
        finally:
            hidden.rename(f)
        self.assertIn("ask the server owner for today's link", r.stdout + r.stderr)
        self.assertFalse((game / "instances").exists())

    def test_broken_launcher_profiles_are_left_alone(self):
        w = world()
        home = w.home("broken")
        game = self.make_game(self.game_dirs(home)[0])
        (game / "launcher_profiles.json").write_text('{"profiles": {')
        r = self.run_installer(w.pub1, home)
        self.assertIn("is not valid JSON, so it was left alone", r.stdout + r.stderr)
        self.assertEqual((game / "launcher_profiles.json").read_text(), '{"profiles": {')
        self.assertFalse((game / "instances").exists())


class ShCases(InstallerCases):
    helper = None
    mac = False

    def game_dirs(self, home):
        if self.mac:
            return [home / "Library" / "Application Support" / "minecraft"]
        return [home / ".minecraft", home / ".var/app/com.mojang.Minecraft/.minecraft"]

    def run_installer(self, pub, home, shell="bash", **env):
        w = world()
        path = "%s:/usr/bin:/bin" % w.stubs
        if self.mac:
            path = "%s:%s" % (w.mac_stubs, path)
        e = {"PATH": path, "HOME": str(home), "LANG": "C.UTF-8", "TMPDIR": str(w.tmp)}
        if self.helper:
            e["VFG_INSTALL_HELPER"] = self.helper
        e.update(env)
        return subprocess.run([shell, str(pub["dir"] / "vfg-install.sh")], env=e, capture_output=True, text=True,
                              timeout=120, cwd=str(home))


class ShLinuxPython(ShCases, unittest.TestCase):
    helper = "python3"

    def test_flatpak_only(self):
        w = world()
        home = w.home("flatpak")
        std, flat = self.game_dirs(home)
        std.mkdir(parents=True)  # an old ~/.minecraft without a launcher in it
        (std / "options.txt").write_text("x")
        self.make_game(flat)
        r = self.ok(self.run_installer(w.pub1, home))
        self.assert_installed(flat, w.pub1, ADDR1, w.token1, w.jar1)
        self.assertEqual(sorted(p.name for p in std.iterdir()), ["options.txt"])
        self.assertIn(str(flat), r.stdout)

    def test_both_linux_folders_get_it(self):
        w = world()
        home = w.home("bothlinux")
        std, flat = self.game_dirs(home)
        self.make_game(std)
        self.make_game(flat)
        self.ok(self.run_installer(w.pub1, home))
        self.assert_installed(std, w.pub1, ADDR1, w.token1, w.jar1)
        self.assert_installed(flat, w.pub1, ADDR1, w.token1, w.jar1)

    def test_the_same_folder_twice_through_a_link_is_done_once(self):
        w = world()
        home = w.home("symlink")
        std, flat = self.game_dirs(home)
        self.make_game(flat)
        std.symlink_to(flat)
        r = self.ok(self.run_installer(w.pub1, home))
        self.assertEqual(r.stdout.count("Installing into"), 1)

    def test_run_with_sh_re_execs_bash(self):
        w = world()
        home = w.home("sh")
        game = self.make_game(self.game_dirs(home)[0])
        self.ok(self.run_installer(w.pub1, home, shell="sh"))
        self.assert_installed(game, w.pub1, ADDR1, w.token1, w.jar1)

    def test_helper_chosen_by_itself(self):
        w = world()
        home = w.home("auto")
        game = self.make_game(self.game_dirs(home)[0])
        self.helper = None
        try:
            self.ok(self.run_installer(w.pub1, home))
        finally:
            self.helper = "python3"
        self.assert_installed(game, w.pub1, ADDR1, w.token1, w.jar1)


class ShLinuxFlatpakPerl(ShCases, unittest.TestCase):
    """The perl helper, with Minecraft where the Flatpak launcher keeps it."""
    helper = "perl"

    def game_dirs(self, home):
        return [home / ".var/app/com.mojang.Minecraft/.minecraft"]


class ShMacPerl(ShCases, unittest.TestCase):
    helper = None  # a Mac picks perl first by itself
    mac = True

    def test_mac_picks_perl(self):
        # With python3 broken on PATH, a Mac still installs: it never needed it.
        w = world()
        home = w.home("macpy")
        game = self.make_game(self.game_dirs(home)[0])
        bad = w.tmp / ("nopython-" + secrets.token_hex(3))
        bad.mkdir()
        write_exec(bad / "python3", "#!/bin/sh\necho 'xcode-select: note: no developer tools were found' >&2\nexit 1\n")
        r = self.run_installer(w.pub1, home, PATH="%s:%s:%s:/usr/bin:/bin" % (bad, w.mac_stubs, w.stubs))
        self.ok(r)
        self.assert_installed(game, w.pub1, ADDR1, w.token1, w.jar1)


class ShMacPython(ShCases, unittest.TestCase):
    helper = "python3"
    mac = True


@unittest.skipUnless(PWSH, "pwsh (PowerShell 7) not found: install it under ~/.local/opt/powershell-*/ or set VFG_PWSH")
class PsCases(InstallerCases, unittest.TestCase):
    """install.ps1 as `irm <link> | iex` would run it, with a fake %APPDATA%."""

    def game_dirs(self, home):
        return [home / "AppData" / "Roaming" / ".minecraft"]

    def env(self, home, **extra):
        w = world()
        e = {"PATH": "/usr/bin:/bin", "HOME": str(home), "APPDATA": str(home / "AppData" / "Roaming"),
             "TMPDIR": str(w.tmp), "LANG": "C.UTF-8", "POWERSHELL_TELEMETRY_OPTOUT": "1",
             "POWERSHELL_UPDATECHECK": "Off", "DOTNET_CLI_TELEMETRY_OPTOUT": "1"}
        e.update(extra)
        return e

    def run_installer(self, pub, home, **env):
        script = PS_PRELUDE + "\n$global:VfgInstallOk = $null\n" \
            "Invoke-Expression ([IO.File]::ReadAllText('%s'))\n" \
            "if (-not $global:VfgInstallOk) { exit 1 }\n" % (pub["dir"] / "vfg-install.ps1")
        return subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", script], env=self.env(home, **env),
                              capture_output=True, text=True, timeout=180, cwd=str(home))

    def test_failure_exit_flag(self):
        w = world()
        home = w.home("psfail")
        r = self.run_installer(w.pub1, home)
        self.assertNotEqual(r.returncode, 0)

    def test_cmd_wrapper_runs_the_powershell_below_it(self):
        """Runs the exact -Command line from vfg-install.cmd, with VFG_SELF as cmd.exe would set it."""
        w = world()
        home = w.home("cmd")
        game = self.make_game(self.game_dirs(home)[0])
        cmd = (w.pub1["dir"] / "vfg-install.cmd").read_bytes().decode("ascii")
        line = [l for l in cmd.split("\r\n") if "powershell.exe" in l][0]
        command = re.search(r'-Command "(.*)"$', line).group(1)
        r = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", PS_PRELUDE + "\n" + command],
                           env=self.env(home, VFG_SELF=str(w.pub1["dir"] / "vfg-install.cmd")),
                           capture_output=True, text=True, timeout=180)
        self.ok(r)
        self.assert_installed(game, w.pub1, ADDR1, w.token1, w.jar1)
        # ...and a cmd whose PowerShell part is missing says so and fails.
        broken = home / "broken.cmd"
        broken.write_text(cmd.split("##VFG-POWERSHELL##")[0])
        r = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", command],
                           env=self.env(home, VFG_SELF=str(broken)), capture_output=True, text=True, timeout=60)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("damaged", r.stdout)

    def test_session_is_left_clean(self):
        """Pasted into a window, it leaves no variables or functions behind and does not exit."""
        w = world()
        home = w.home("clean")
        self.make_game(self.game_dirs(home)[0])
        script = PS_PRELUDE + "\nInvoke-Expression ([IO.File]::ReadAllText('%s'))\n" \
            "'after:' + [bool](Get-Variable ClientToken -ErrorAction SilentlyContinue) + ':' + " \
            "[bool](Get-Command Install-Vfg -ErrorAction SilentlyContinue)\n" % (w.pub1["dir"] / "vfg-install.ps1")
        r = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", script], env=self.env(home),
                           capture_output=True, text=True, timeout=180)
        self.assertIn("after:False:False", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
