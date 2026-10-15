"""The test server's setup (bridge/deploy/testserver.py), its unit and reset.sh, the fake systemctl
that backs realmc's minecraft-test, and the pins realmc takes from testserver.py. No Minecraft runs
here: the template and the real server are tried end to end on realmc (integration/realmc/README.md).

    python3 -m unittest discover -s bridge/deploy/tests      # from the repo's root; a second or two
"""
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
DEPLOY = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(DEPLOY))
sys.path.insert(0, DEPLOY)
import testserver as ts  # noqa: E402

UNIT = os.path.join(DEPLOY, "systemd", "minecraft-test.service")
FAKE_BIN = os.path.join(REPO, "toolkit", "tests", "bin")

LIVE_PROPS = """#Minecraft server properties
#Thu Oct 08 06:08:12 UTC 2026
difficulty=hard
gamemode=survival
allow-flight=true
enable-rcon=true
rcon.port=25575
rcon.password=LIVE-SECRET-PASSWORD
management-server-secret=LIVE-MGMT-SECRET
management-server-tls-keystore-password=LIVE-KEYSTORE-SECRET
text-filtering-config={"apiKey":"LIVE-FILTER-SECRET"}
server-port=25565
server-ip=
query.port=25565
online-mode=true
white-list=true
pause-when-empty-seconds=60
view-distance=12
simulation-distance=10
level-seed=12345
motd=26.2 Server
"""
SECRETS = ("LIVE-SECRET-PASSWORD", "LIVE-MGMT-SECRET", "LIVE-KEYSTORE-SECRET", "LIVE-FILTER-SECRET")


def make_jar(path, version=ts.MC_VERSION, extra=b""):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("version.json", json.dumps({"id": version}))
        z.writestr("extra.bin", extra)


def sha(path, algo="sha256"):
    with open(path, "rb") as f:
        return hashlib.new(algo, f.read()).hexdigest()


class Box(unittest.TestCase):
    """A temp /opt: a live mc and vfg (a fake jar, server.properties, eula.txt, a datapack), and a
    --from folder whose Fabric and Carpet jars match pins patched in for the test."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vfg-testserver-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        j = lambda *p: os.path.join(self.tmp, *p)  # noqa: E731
        self.live_mc, self.live_vfg, self.src = j("opt", "mc"), j("opt", "vfg"), j("shared")
        self.mc, self.vfg = self.live_mc + "-test", self.live_vfg + "-test"
        for d in (self.live_mc, os.path.join(self.live_vfg, "python", "bin"),
                  os.path.join(self.live_mc, "world", "datapacks", "everhear", "data")):
            os.makedirs(d)
        make_jar(os.path.join(self.live_mc, "server.jar"))
        with open(os.path.join(self.live_mc, "server.properties"), "w") as f:
            f.write(LIVE_PROPS)
        with open(os.path.join(self.live_mc, "eula.txt"), "w") as f:
            f.write("#By changing the setting below to TRUE you are indicating your agreement\neula=true\n")
        with open(os.path.join(self.live_mc, "world", "datapacks", "everhear", "pack.mcmeta"), "w") as f:
            f.write('{"pack": {"description": "the engine"}}\n')
        # Fake Fabric and Carpet jars, and pins that match them.
        libs = []
        for i, (rel, _sha1) in enumerate(ts.FABRIC_LIBS):
            path = os.path.join(self.src, "libraries", rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(b"lib %d" % i)
            libs.append((rel, sha(path, "sha1")))
        carpet = os.path.join(self.src, "mods", ts.CARPET_JAR)
        os.makedirs(os.path.dirname(carpet))
        with open(carpet, "wb") as f:
            f.write(b"carpet")
        self.pins = {"FABRIC_LIBS": libs, "CARPET_SHA256": sha(carpet), "CARPET_SIZE": 6}
        saved = {k: getattr(ts, k) for k in self.pins}
        for k, v in self.pins.items():
            setattr(ts, k, v)
        self.addCleanup(lambda: [setattr(ts, k, v) for k, v in saved.items()])

    def args(self, *more, port=40001, rcon_port=40002):
        return ["--live-mc", self.live_mc, "--live-vfg", self.live_vfg, "--port", str(port),
                "--rcon-port", str(rcon_port), "--from", self.src, "--no-template"] + list(more)

    def setup_rc(self, *more, **kw):
        return ts.main(self.args(*more, **kw))

    def live_snapshot(self):
        got = {}
        for top in (self.live_mc, self.live_vfg):
            for dp, _dn, fn in os.walk(top):
                for f in fn:
                    p = os.path.join(dp, f)
                    got[p] = (os.stat(p).st_mtime_ns, sha(p))
        return got


class TestProperties(unittest.TestCase):
    def test_the_live_settings_without_secrets_and_ports_and_the_overrides(self):
        lines, changed = ts.test_properties(LIVE_PROPS.splitlines(), [], 25566, 25576)
        text = "\n".join(lines)
        self.assertTrue(changed)
        for secret in SECRETS:
            self.assertNotIn(secret, text)
        p = ts.props_of(lines)
        self.assertEqual((p["server-port"], p["query.port"], p["rcon.port"]), ("25566", "25566", "25576"))
        self.assertEqual(p["server-ip"], "127.0.0.1")
        self.assertEqual(p["level-seed"], ts.SEED)
        for k, v in ts.TEST_OVERRIDES:
            self.assertEqual(p[k], v, k)
        self.assertEqual((p["difficulty"], p["gamemode"], p["allow-flight"]), ("hard", "survival", "true"))
        self.assertNotIn("management-server-secret", p)
        self.assertNotIn("text-filtering-config", p)
        self.assertGreaterEqual(len(p["rcon.password"]), 24)

    def test_its_own_password_is_kept_and_a_rewrite_by_minecraft_is_no_change(self):
        first, _ = ts.test_properties(LIVE_PROPS.splitlines(), [], 25566, 25576)
        # Minecraft rewrites the file at each start: a new date line, its keys in its own order.
        rewritten = ["#Minecraft server properties", "#Tue Oct 13 06:00:00 UTC 2026"] + \
            sorted(l for l in first if not l.startswith("#")) + ["new-key-from-minecraft=1"]
        again, changed = ts.test_properties(LIVE_PROPS.splitlines(), rewritten, 25566, 25576)
        self.assertFalse(changed)
        self.assertEqual(ts.props_of(again)["rcon.password"], ts.props_of(first)["rcon.password"])
        self.assertEqual(ts.props_of(again)["new-key-from-minecraft"], "1")
        moved, changed = ts.test_properties(LIVE_PROPS.splitlines(), rewritten, 25566, 25577)
        self.assertTrue(changed)
        self.assertEqual(ts.props_of(moved)["rcon.password"], ts.props_of(first)["rcon.password"])
        live2 = LIVE_PROPS.replace("difficulty=hard", "difficulty=normal").splitlines()
        self.assertTrue(ts.test_properties(live2, rewritten, 25566, 25576)[1])


class TestRefusals(Box):
    def test_the_box_defaults_are_the_contract(self):
        a = ts.parse([])
        self.assertEqual((a.live_mc, a.live_vfg, a.mc, a.vfg, a.port, a.rcon_port),
                         ("/opt/mc", "/opt/vfg", "/opt/mc-test", "/opt/vfg-test", 25566, 25576))

    def refused(self, *argv, **kw):
        before = self.live_snapshot()
        self.assertEqual(ts.main(self.args(*argv, **kw)), 3)
        self.assertEqual(self.live_snapshot(), before)

    def test_it_never_lays_out_over_the_live_server(self):
        self.refused("--mc", self.live_mc)
        self.refused("--vfg", self.live_vfg)
        self.refused("--mc", os.path.join(self.live_mc, "test"))
        self.refused("--mc", self.vfg)
        os.symlink(self.live_mc, os.path.join(self.tmp, "link"))
        self.refused("--mc", os.path.join(self.tmp, "link"))
        self.assertFalse(os.path.exists(os.path.join(self.live_mc, ts.MARKER)))

    def test_nor_on_the_live_ports(self):
        self.refused(rcon_port=25575)
        self.refused(port=25565)
        self.refused(port=40001, rcon_port=40001)
        with open(os.path.join(self.live_mc, "server.properties"), "a") as f:
            f.write("rcon.port=41000\n")
        self.refused(rcon_port=41000)

    def test_nor_in_a_folder_it_did_not_make_nor_beside_a_test_server(self):
        os.makedirs(self.mc)
        with open(os.path.join(self.mc, "something"), "w") as f:
            f.write("not ours")
        self.refused()
        shutil.rmtree(self.mc)
        with open(os.path.join(self.live_mc, ts.MARKER), "w") as f:
            f.write("{}")
        self.refused()

    def test_nor_as_another_user(self):
        self.assertEqual(self.setup_rc("--user", "nobody-like-this"), 3)
        self.assertFalse(os.path.exists(self.mc))


class TestSetup(Box):
    def test_lays_out_once_then_changes_nothing(self):
        before = self.live_snapshot()
        self.assertEqual(self.setup_rc(), ts.EXIT_RESTART)
        marker = json.load(open(os.path.join(self.mc, ts.MARKER)))
        self.assertEqual((marker["rcon_port"], marker["port"]), (40002, 40001))
        self.assertEqual((marker["live_mc"], marker["live_vfg"]),
                         (os.path.realpath(self.live_mc), os.path.realpath(self.live_vfg)))
        props = ts.props_of(ts.read_lines(os.path.join(self.mc, "server.properties")))
        self.assertEqual(props["rcon.port"], str(marker["rcon_port"]))
        self.assertEqual(os.stat(os.path.join(self.mc, "server.properties")).st_mode & 0o777, 0o600)
        jar = os.path.join(self.mc, "server.jar")
        self.assertFalse(os.path.islink(jar))
        self.assertNotEqual(os.stat(jar).st_ino, os.stat(os.path.join(self.live_mc, "server.jar")).st_ino)
        self.assertEqual(sha(jar), sha(os.path.join(self.live_mc, "server.jar")))
        self.assertTrue(ts.eula_accepted(os.path.join(self.mc, "eula.txt")))
        self.assertTrue(os.path.isfile(os.path.join(self.mc, "mods", ts.CARPET_JAR)))
        args = open(os.path.join(self.mc, "launch.args")).read()
        self.assertIn(ts.FABRIC_MAIN, args)
        for rel, _ in ts.FABRIC_LIBS:
            self.assertIn("libraries/" + rel, args)
            self.assertTrue(os.path.isfile(os.path.join(self.mc, "libraries", rel)))
        self.assertTrue(os.access(os.path.join(self.mc, "reset.sh"), os.X_OK))
        # The vfg side: the live Python, the folders, no secrets; nothing written into state/.
        self.assertEqual(os.readlink(os.path.join(self.vfg, "python")), os.path.join(self.live_vfg, "python"))
        for d in ("run", "tests", "state", "made"):
            self.assertTrue(os.path.isdir(os.path.join(self.vfg, d)), d)
        self.assertEqual(json.load(open(os.path.join(self.vfg, "protected.json"))), [])
        self.assertFalse(os.path.exists(os.path.join(self.vfg, ".env")))
        self.assertEqual(os.listdir(os.path.join(self.vfg, "state")), [])
        self.assertFalse(os.path.exists(os.path.join(self.vfg, "scripts")))
        # Again: nothing changes, and the live side was only read.
        self.assertEqual(self.setup_rc(), ts.EXIT_OK)
        self.assertEqual(self.live_snapshot(), before)

    def test_a_change_on_the_live_side_is_taken_and_asks_for_a_restart(self):
        self.assertEqual(self.setup_rc(), ts.EXIT_RESTART)
        old_sha = sha(os.path.join(self.mc, "server.jar"))
        make_jar(os.path.join(self.live_mc, "server.jar"), extra=b"a newer build")
        self.assertEqual(self.setup_rc(), ts.EXIT_RESTART)
        new_sha = sha(os.path.join(self.mc, "server.jar"))
        self.assertNotEqual(old_sha, new_sha)
        self.assertEqual(new_sha, sha(os.path.join(self.live_mc, "server.jar")))
        with open(os.path.join(self.live_mc, "server.properties"), "a") as f:
            f.write("max-players=50\n")
        self.assertEqual(self.setup_rc(), ts.EXIT_RESTART)
        self.assertEqual(self.setup_rc(rcon_port=40003), ts.EXIT_RESTART)
        self.assertEqual(json.load(open(os.path.join(self.mc, ts.MARKER)))["rcon_port"], 40003)
        self.assertEqual(self.setup_rc(rcon_port=40003), ts.EXIT_OK)
        with open(os.path.join(self.mc, "mods", "stray-mod.jar"), "wb") as f:
            f.write(b"x")
        self.assertEqual(self.setup_rc(rcon_port=40003), ts.EXIT_RESTART)
        self.assertEqual(os.listdir(os.path.join(self.mc, "mods")), [ts.CARPET_JAR])
        with open(os.path.join(self.vfg, ".env"), "w") as f:
            f.write("TYPESAFE_API_KEY=x\n")
        self.setup_rc(rcon_port=40003)
        self.assertFalse(os.path.exists(os.path.join(self.vfg, ".env")))

    def test_another_minecraft_version_is_refused(self):
        make_jar(os.path.join(self.live_mc, "server.jar"), version="26.4")
        self.assertEqual(self.setup_rc(), 1)

    def test_no_eula_no_test_eula_and_no_template(self):
        with open(os.path.join(self.live_mc, "eula.txt"), "w") as f:
            f.write("eula=false\n")
        self.assertEqual(self.setup_rc(), ts.EXIT_RESTART)
        self.assertFalse(os.path.exists(os.path.join(self.mc, "eula.txt")))
        a = ts.parse([x for x in self.args() if x != "--no-template"])
        self.assertEqual(ts.main([x for x in self.args() if x != "--no-template"]), 3)
        self.assertFalse(os.path.exists(os.path.join(a.mc, "world.template")))

    def test_the_template_is_made_again_only_when_it_must(self):
        self.setup_rc()
        jar_sha = sha(os.path.join(self.mc, "server.jar"))
        self.assertEqual(ts.template_wanted(self.mc, jar_sha), "there is none")
        os.makedirs(os.path.join(self.mc, "world.template"))
        info = {"seed": ts.SEED, "version": ts.MC_VERSION, "format": ts.TEMPLATE_FORMAT, "jar_sha256": jar_sha}
        with open(os.path.join(self.mc, "template.json"), "w") as f:
            json.dump(info, f)
        self.assertIsNone(ts.template_wanted(self.mc, jar_sha))
        self.assertEqual(ts.template_wanted(self.mc, "0" * 64), "the live server.jar changed")
        # The template is needed and the test server's port answers: stop it first (exit 11).
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        self.addCleanup(srv.close)
        port = srv.getsockname()[1]
        argv = [x for x in self.args(rcon_port=port) if x != "--no-template"] + ["--fresh"]
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(ts.main(argv), ts.EXIT_STOP_FIRST)
        # What this pass wrote (the new port) is said before it exits: the next pass finds it done.
        self.assertRegex(out.getvalue(), r"mc-test: changed: [^\n]*server\.properties[^\n]*\n.*must be made")


class TestLaunch(Box):
    def test_the_launch_line_takes_two_cpus_c1_and_the_aot_cache_when_there_is_one(self):
        libs = ["libraries/" + rel for rel, _ in ts.FABRIC_LIBS]
        plain = ts.launch_args(libs).splitlines()
        for flag in ts.JVM_FLAGS + ["-XX:ActiveProcessorCount=2", "-XX:TieredStopAtLevel=1"]:
            self.assertIn(flag, plain)
        self.assertFalse([l for l in plain if "AOTCache" in l])
        self.assertEqual(plain[-3:], [":".join(libs), ts.FABRIC_MAIN, "nogui"])
        cached = ts.launch_args(libs, "-XX:AOTCache=" + ts.AOT).splitlines()
        self.assertIn("-XX:AOTCache=test-server.aot", cached)
        self.assertLess(cached.index("-XX:AOTCache=test-server.aot"), cached.index("-cp"))
        self.assertEqual(ts.java_argv("/usr/bin/java", "1536M"), ["/usr/bin/java", "-Xms256M", "-Xmx1536M", "@launch.args"])

    def test_the_aot_cache_is_made_again_when_java_or_the_classpath_changes(self):
        java = os.path.join(self.tmp, "java")
        with open(java, "w") as f:
            f.write('#!/bin/sh\necho "openjdk version \\"25.0.4\\" (fake)" >&2\n')
        os.chmod(java, 0o755)
        ident = ts.java_id(java, ["a.jar", "b.jar"])
        self.assertEqual(ident, ts.java_id(java, ["a.jar", "b.jar"]))
        self.assertNotEqual(ident, ts.java_id(java, ["a.jar", "c.jar"]))
        self.assertIsNone(ts.java_id(os.path.join(self.tmp, "no-java"), ["a.jar"]))
        self.assertEqual(self.setup_rc("--java", java), ts.EXIT_RESTART)
        self.assertEqual(ts.aot_wanted(self.mc, ident), "there is none")
        self.assertIsNone(ts.aot_wanted(self.mc, None))   # no Java to ask: none is made, none used
        with open(os.path.join(self.mc, ts.AOT), "wb") as f:
            f.write(b"x")
        with open(os.path.join(self.mc, ts.AOT_INFO), "w") as f:
            json.dump({"java_id": ident}, f)
        self.assertIsNone(ts.aot_wanted(self.mc, ident))
        self.assertEqual(ts.aot_wanted(self.mc, "another"), "Java or the classpath changed")
        # The layout writes the cache's flag only for a cache this Java made.
        self.assertEqual(self.setup_rc("--java", java), ts.EXIT_OK)
        args = open(os.path.join(self.mc, "launch.args")).read()
        self.assertNotIn("AOTCache", args)   # the cache above was made for other jars
        libs = ["libraries/" + rel for rel, _ in ts.FABRIC_LIBS]
        with open(os.path.join(self.mc, ts.AOT_INFO), "w") as f:
            json.dump({"java_id": ts.java_id(java, libs)}, f)
        self.assertEqual(self.setup_rc("--java", java), ts.EXIT_RESTART)
        self.assertIn("-XX:AOTCache=test-server.aot", open(os.path.join(self.mc, "launch.args")).read())
        self.assertEqual(self.setup_rc("--java", java, "--no-aot"), ts.EXIT_RESTART)
        self.assertNotIn("AOTCache", open(os.path.join(self.mc, "launch.args")).read())


class TestTestUser(Box):
    """--test-user: the sandboxed test's own uid. Its group is faked as this process's own (making a
    system user needs root), which is enough to see every mode and group set."""

    def setUp(self):
        Box.setUp(self)
        import grp
        import pwd
        self.gid = os.getgid()
        self.group = grp.getgrgid(self.gid).gr_name
        fake_user = pwd.struct_passwd(("vfgtest", "x", os.getuid() + 4242, self.gid, "", "/nonexistent",
                                       "/sbin/nologin"))
        fake_group = grp.struct_group(("vfgtest", "x", self.gid, []))
        real_user, real_group = ts.pwd.getpwnam, ts.grp.getgrnam
        ts.pwd.getpwnam = lambda n: fake_user if n == "vfgtest" else real_user(n)
        ts.grp.getgrnam = lambda n: fake_group if n == "vfgtest" else real_group(n)
        self.addCleanup(setattr, ts.pwd, "getpwnam", real_user)
        self.addCleanup(setattr, ts.grp, "getgrnam", real_group)

    def mode(self, *path):
        st = os.lstat(os.path.join(*path))
        return st.st_mode & 0o7777, st.st_gid

    def test_the_test_writes_run_and_state_only_and_reads_its_rcon_password(self):
        before = self.live_snapshot()
        self.assertEqual(self.setup_rc("--test-user", "vfgtest"), ts.EXIT_RESTART)
        self.assertEqual(json.load(open(os.path.join(self.mc, ts.MARKER)))["test_user"], "vfgtest")
        self.assertEqual(self.mode(self.mc, ts.MARKER)[0], 0o644)
        self.assertEqual(self.mode(self.mc, "server.properties"), (0o640, self.gid))
        self.assertEqual(self.mode(self.vfg), (0o2755, self.gid))
        for d in ("run", "state"):
            self.assertEqual(self.mode(self.vfg, d), (0o2775, self.gid), d)
        for d in ("tests", "made"):   # this user writes there by name: never the test's
            self.assertEqual(self.mode(self.vfg, d)[0], 0o755, d)
        self.assertTrue(os.path.islink(os.path.join(self.vfg, "python")))
        self.assertFalse(os.path.lexists(os.path.join(self.vfg, "incantations.json")))
        self.assertIn("TEST_GROUP='vfgtest'", open(os.path.join(self.mc, "reset.sh")).read())
        # Again: nothing changes; the live side was only read.
        self.assertEqual(self.setup_rc("--test-user", "vfgtest"), ts.EXIT_OK)
        self.assertEqual(self.live_snapshot(), before)

    def test_an_install_from_before_the_test_user_is_brought_up_to_it(self):
        self.assertEqual(self.setup_rc(), ts.EXIT_RESTART)   # as the first box install was: no test user
        marker = json.load(open(os.path.join(self.mc, ts.MARKER)))
        self.assertNotIn("test_user", marker)
        self.assertEqual(self.mode(self.mc, "server.properties")[0], 0o600)
        with open(os.path.join(self.vfg, "incantations.json"), "w") as f:   # the old store's place
            f.write('{"version": 3, "incantations": []}\n')
        os.chmod(os.path.join(self.vfg, "run"), 0o777)
        self.assertEqual(self.setup_rc("--test-user", "vfgtest"), ts.EXIT_RESTART)   # reset.sh changed
        self.assertEqual(json.load(open(os.path.join(self.mc, ts.MARKER)))["test_user"], "vfgtest")
        self.assertEqual(self.mode(self.mc, "server.properties"), (0o640, self.gid))
        self.assertEqual(self.mode(self.vfg, "run"), (0o2775, self.gid))
        self.assertFalse(os.path.lexists(os.path.join(self.vfg, "incantations.json")))

    def test_a_missing_test_user_or_the_servers_own_uid_is_refused(self):
        self.assertEqual(self.setup_rc("--test-user", "no-such-user-here"), 3)
        self.assertFalse(os.path.exists(self.mc))
        import pwd
        me = pwd.getpwuid(os.getuid())
        self.assertEqual(self.setup_rc("--test-user", me.pw_name), 3)

    def test_reset_gives_the_properties_back_to_the_group(self):
        self.assertEqual(self.setup_rc("--test-user", "vfgtest"), ts.EXIT_RESTART)
        # reset.sh as written for a group that exists here: this process's own.
        with open(os.path.join(self.mc, "reset.sh"), "w") as f:
            f.write(ts.reset_sh(self.mc, self.live_mc, free_port(), self.group))
        os.makedirs(os.path.join(self.mc, "world.template"))
        os.chmod(os.path.join(self.mc, "server.properties"), 0o600)
        r = subprocess.run([os.path.join(self.mc, "reset.sh")], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(self.mode(self.mc, "server.properties"), (0o640, self.gid))


class TestFirewall(unittest.TestCase):
    """testserver.sh's nftables file, as nft_rules writes it (checked with nft -c, loaded twice and
    tried in a network namespace on 2026-10-13: the test uid reached 127.0.0.1:25576 and was refused
    at once on 25575, 25565, 8787, UDP and off lo; another uid was untouched)."""

    def rules(self, *argv):
        text = open(os.path.join(DEPLOY, "testserver.sh")).read()
        func = text[text.index("nft_rules() {"):]
        func = func[:func.index("\nNFT\n") + 5] + "}\n"
        r = subprocess.run(["bash", "-c", func + 'nft_rules "$@"', "nft_rules"] + list(argv), stdout=subprocess.PIPE,
                           universal_newlines=True, timeout=10)
        self.assertEqual(r.returncode, 0)
        return r.stdout

    def test_the_test_user_reaches_the_test_rcon_on_lo_and_nothing_else(self):
        text = self.rules("25566", "25576", "991", "vfgtest")
        rules = [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]
        self.assertEqual(rules[:3], ["table inet vfg_test", "delete table inet vfg_test", "table inet vfg_test {"])
        self.assertIn('tcp dport { 25566, 25576 } iifname != "lo" drop;', rules)
        out = rules[rules.index("chain output {") + 1:]
        self.assertEqual(out[:4], ["type filter hook output priority 0; policy accept;",
                                   'meta skuid 991 oifname "lo" tcp dport 25576 accept;',
                                   "meta skuid 991 meta l4proto tcp reject with tcp reset;",
                                   "meta skuid 991 reject;"])
        self.assertEqual(out[4:], ["}", "}"])

    def test_the_script_writes_it_with_the_test_users_uid(self):
        text = open(os.path.join(DEPLOY, "testserver.sh")).read()
        self.assertIn('nft_rules "$PORT" "$RCON_PORT" "$TEST_UID" "$TEST_USER"', text)
        self.assertIn('--test-user "$TEST_USER"', text)
        self.assertRegex(text, r"useradd --system --gid \"\$TEST_USER\" --no-create-home")
        self.assertIn('usermod -aG "$TEST_USER" "$OWNER"', text)


class TestPins(Box):
    def test_a_jar_comes_from_a_matching_candidate_or_its_url_and_never_unchecked(self):
        good = os.path.join(self.tmp, "good.jar")
        bad = os.path.join(self.tmp, "bad.jar")
        with open(good, "wb") as f:
            f.write(b"the real one")
        with open(bad, "wb") as f:
            f.write(b"something else")
        digest = sha(good)
        dest = os.path.join(self.tmp, "out", "x.jar")
        self.assertEqual(ts.obtain(dest, digest, "sha256", [bad, good], "file:///nowhere"), good)
        self.assertEqual(sha(dest), digest)
        os.remove(dest)
        self.assertEqual(ts.obtain(dest, digest, "sha256", [bad], "file://" + good), "file://" + good)
        self.assertEqual(sha(dest), digest)
        os.remove(dest)
        with self.assertRaises(ts.Fail):
            ts.obtain(dest, digest, "sha256", [bad], "file://" + bad)
        self.assertFalse(os.path.exists(dest))
        self.assertFalse(os.path.exists(dest + ".part"))

    def test_realmc_runs_on_the_same_pins(self):
        sys.path.insert(0, os.path.join(REPO, "integration", "realmc"))
        try:
            import realmc
        finally:
            sys.path.remove(os.path.join(REPO, "integration", "realmc"))
        self.assertIs(realmc.testserver, ts)
        for name in ("CARPET_JAR", "CARPET_URL", "FABRIC_MAIN", "MAVEN"):
            self.assertEqual(getattr(realmc, name), getattr(ts, name), name)
        self.assertEqual((realmc.SEED, realmc.VERSION), (ts.SEED, ts.MC_VERSION))

    def test_the_real_pins_look_right(self):
        src = open(ts.__file__).read()
        self.assertIn('CARPET_SHA256 = "7c40b93e807ed914064176898037117097446c6cc76e6a80a856c8bf7f7659f9"', src)
        for rel, sha1 in re.findall(r'\("((?:net|org)/[^"]+\.jar)",\s*"([0-9a-f]{40})"\)', src):
            self.assertTrue(any(rel.startswith(p) for p in ts.MAVEN), rel)
        self.assertEqual(len(re.findall(r'\("((?:net|org)/[^"]+\.jar)",\s*"([0-9a-f]{40})"\)', src)), 7)

    def test_the_stage_is_livetests(self):
        text = open(os.path.join(REPO, "engine", "vfg_engine", "livetest.py")).read()
        x, z = re.search(r"^X, Z = (\d+), (\d+)", text, re.M).groups()
        floor = re.search(r"^FLOOR = (\d+)", text, re.M).group(1)
        clear = re.search(r"^CLEAR = (\d+)", text, re.M).group(1)
        self.assertEqual((ts.STAGE_X, ts.STAGE_Z, ts.STAGE_FLOOR, ts.STAGE_CLEAR),
                         (int(x), int(z), int(floor), int(clear)))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestResetSh(Box):
    def setUp(self):
        Box.setUp(self)
        self.rport = free_port()
        self.assertEqual(self.setup_rc(rcon_port=self.rport), ts.EXIT_RESTART)
        os.makedirs(os.path.join(self.mc, "world.template", "region"))
        with open(os.path.join(self.mc, "world.template", "level.dat"), "w") as f:
            f.write("the template")
        os.makedirs(os.path.join(self.mc, "world"))
        with open(os.path.join(self.mc, "world", "dirty"), "w") as f:
            f.write("a test was here")

    def reset(self):
        return subprocess.run([os.path.join(self.mc, "reset.sh")], cwd=self.tmp, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, universal_newlines=True, timeout=30)

    def test_the_live_datapacks_replace_the_templates_never_merge(self):
        # The template with an old copy of the engine's pack (a file the live one dropped) and a pack
        # the live server does not have: the fresh world gets the live packs and nothing else.
        old = os.path.join(self.mc, "world.template", "datapacks")
        os.makedirs(os.path.join(old, "everhear", "data", "gone"))
        os.makedirs(os.path.join(old, "stale_pack"))
        r = self.reset()
        self.assertEqual(r.returncode, 0, r.stdout)
        packs = os.path.join(self.mc, "world", "datapacks")
        self.assertEqual(sorted(os.listdir(packs)), ["everhear"])
        self.assertEqual(sorted(os.listdir(os.path.join(packs, "everhear"))), ["data", "pack.mcmeta"])
        self.assertEqual(os.listdir(os.path.join(packs, "everhear", "data")), [])
        shutil.rmtree(os.path.join(self.live_mc, "world", "datapacks"))
        self.assertEqual(self.reset().returncode, 0)
        self.assertEqual(os.listdir(packs), [])

    def test_a_fresh_world_the_live_datapacks_and_the_token(self):
        before = self.live_snapshot()
        r = self.reset()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertRegex(r.stdout, r"\[vfg/INFO\]: reset.sh: a fresh world from world.template, the live datapacks: "
                                   r"everhear , in \d+ ms")
        world = os.path.join(self.mc, "world")
        self.assertEqual(open(os.path.join(world, "level.dat")).read(), "the template")
        self.assertFalse(os.path.exists(os.path.join(world, "dirty")))
        self.assertTrue(os.path.isfile(os.path.join(world, "datapacks", "everhear", "pack.mcmeta")))
        self.assertTrue(os.path.isfile(os.path.join(world, ts.FRESH)))
        self.assertFalse(os.path.exists(os.path.join(self.mc, "world.new")))
        self.assertEqual(sorted(os.listdir(os.path.join(self.mc, "world.template"))), ["level.dat", "region"])
        self.assertEqual(self.live_snapshot(), before)

    def test_it_refuses_a_folder_without_the_marker_and_a_running_server(self):
        marker = os.path.join(self.mc, ts.MARKER)
        kept = open(marker).read()
        os.remove(marker)
        r = self.reset()
        self.assertEqual(r.returncode, 1)
        self.assertIn("not a test server", r.stdout)
        self.assertTrue(os.path.exists(os.path.join(self.mc, "world", "dirty")))
        # Nor does the setup take back a folder that lost its marker: it did not make it, as far as it knows.
        self.assertEqual(self.setup_rc(rcon_port=self.rport), 3)
        with open(marker, "w") as f:
            f.write(kept)
        srv = socket.socket()
        srv.bind(("127.0.0.1", self.rport))
        srv.listen(1)
        self.addCleanup(srv.close)
        r = self.reset()
        self.assertEqual(r.returncode, 1)
        self.assertIn("is running", r.stdout)
        self.assertTrue(os.path.exists(os.path.join(self.mc, "world", "dirty")))


class TestUnitFile(unittest.TestCase):
    def setUp(self):
        self.text = open(UNIT).read()

    def value(self, key):
        m = re.search(r"^%s=(.*)$" % re.escape(key), self.text, re.M)
        return m and m.group(1)

    def test_the_contract(self):
        for key, want in (("User", "ec2-user"), ("WorkingDirectory", "/opt/mc-test"),
                          ("ExecStartPre", "/opt/mc-test/reset.sh"), ("Nice", "10"), ("CPUWeight", "20"),
                          ("CPUQuota", "200%"),
                          ("IOWeight", "20"), ("OOMScoreAdjust", "800"), ("Restart", "always"),
                          ("StandardOutput", "append:/opt/mc-test/console.log"), ("WantedBy", "multi-user.target"),
                          ("ReadOnlyPaths", "-/opt/mc -/opt/vfg")):
            self.assertEqual(self.value(key), want, key)
        self.assertEqual(self.value("ExecStart"), "/usr/bin/java -Xms%s -Xmx${MC_TEST_HEAP} @launch.args" % ts.XMS)
        self.assertIn("Server stopped", self.value("ExecStopPost"))

    def test_the_heap_and_memory_max_agree_with_the_setup_and_the_live_heap_rule(self):
        heap = re.search(r"MC_TEST_HEAP=(\d+)M", self.text).group(1)
        self.assertEqual(ts.HEAP, heap + "M")
        mem_max = int(re.search(r"^MemoryMax=(\d+)M$", self.text, re.M).group(1))
        self.assertGreaterEqual(mem_max, int(heap) + 768)
        # provision.sh and testserver.sh both take the live heap's reserve from this MemoryMax.
        for script in ("provision.sh", "testserver.sh"):
            with open(os.path.join(DEPLOY, script)) as f:
                text = f.read()
            self.assertIn("minecraft-test.service", text, script)
            self.assertRegex(text, r"- 4 - ", script)
        # provision sets the test server up before the heap, and keeps its reserve only where it is installed.
        with open(os.path.join(DEPLOY, "provision.sh")) as f:
            prov = f.read()
        self.assertLess(prov.index('bash "$HERE/testserver.sh"'), prov.index('heap=$(( (mem_kib'))
        self.assertLess(prov.index('heap=$(( (mem_kib'), prov.index("systemctl enable --now minecraft"))
        self.assertIn("if [[ -f /opt/mc-test/.vfg-test-server && -f /etc/systemd/system/minecraft-test.service ]]",
                      prov)
        start_limit = int(self.value("StartLimitBurst"))
        self.assertGreaterEqual(start_limit, 20)   # a restart after every test must never hit it


class TestRealmcUnit(unittest.TestCase):
    """realmc's minecraft-test: a restart asked for with --no-block yields to a stop after it, as
    systemd's stop job cancels a queued restart (down right after a test leaves it down)."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="vfg-realmc-unit-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.units = os.path.join(self.root, "iqt", "box", "units")
        os.makedirs(self.units)

    def unit(self, *argv):
        env = dict(os.environ, REALMC_ROOT=self.root)
        return subprocess.run([sys.executable, os.path.join(REPO, "integration", "realmc", "realmc.py"), "unit", "iqt",
                               "minecraft-test"] + list(argv), env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, universal_newlines=True, timeout=60)

    def test_a_restart_asked_for_before_a_stop_is_dropped(self):
        import time
        asked = time.time()
        self.assertEqual(self.unit("stop").returncode, 0)
        self.assertGreater(float(open(os.path.join(self.units, "minecraft-test.stopped")).read()), asked)
        r = self.unit("restart", "--asked", "%.6f" % asked)
        self.assertEqual(r.returncode, 0, r.stdout)
        journal = open(os.path.join(self.units, "minecraft-test.journal")).read()
        self.assertIn("dropped: the unit was stopped since", journal)
        self.assertEqual(self.unit("is-active").stdout.strip(), "inactive")
        # One asked for after the stop goes ahead (and fails here: no test server is laid out).
        r = self.unit("restart", "--asked", "%.6f" % time.time())
        self.assertEqual(r.returncode, 1)
        self.assertIn("lay the test server out first", open(os.path.join(self.units, "minecraft-test.journal")).read())


class TestFakeSystemctl(unittest.TestCase):
    """toolkit/tests/bin/systemctl: state-file units as before, and units backed by a program."""

    def setUp(self):
        self.units = tempfile.mkdtemp(prefix="vfg-fake-units-")
        self.addCleanup(shutil.rmtree, self.units, True)

    def ctl(self, *argv):
        env = {"PATH": FAKE_BIN + ":" + os.environ["PATH"], "FAKE_UNITS": self.units}
        r = subprocess.run(["sudo", "-n", "systemctl"] + list(argv), env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, universal_newlines=True, timeout=30)
        return r.returncode, r.stdout.strip()

    def unit(self, name, state):
        with open(os.path.join(self.units, name), "w") as f:
            f.write(state + "\n")

    def test_state_file_units(self):
        self.assertEqual(self.ctl("show", "-p", "LoadState", "--value", "minecraft"), (0, "not-found"))
        self.assertEqual(self.ctl("start", "minecraft")[0], 5)
        self.unit("minecraft", "inactive")
        self.assertEqual(self.ctl("show", "-p", "LoadState", "--value", "minecraft"), (0, "loaded"))
        self.assertNotEqual(self.ctl("is-active", "minecraft")[0], 0)
        self.assertEqual(self.ctl("start", "minecraft"), (0, ""))
        self.assertEqual(self.ctl("is-active", "minecraft"), (0, "active"))
        self.assertEqual(self.ctl("stop", "minecraft")[0], 0)
        self.assertEqual(self.ctl("is-active", "minecraft")[1], "inactive")
        open(os.path.join(self.units, "minecraft.fail"), "w").close()
        self.assertEqual(self.ctl("restart", "minecraft")[0], 1)
        self.assertEqual(self.ctl("is-active", "--quiet", "minecraft"), (3, ""))
        self.assertEqual(self.ctl("reset-failed", "minecraft")[0], 0)
        self.assertEqual(self.ctl("is-active", "minecraft")[1], "inactive")
        self.assertEqual(self.ctl("frobnicate", "minecraft")[0], 1)

    def test_units_backed_by_a_program_get_every_verb(self):
        log = os.path.join(self.units, "calls")
        exe = os.path.join(self.units, "minecraft-test.exec")
        with open(exe, "w") as f:
            f.write('#!/bin/sh\necho "$*" >> %s\ncase "$1" in is-active) echo active ;; esac\n' % log)
        os.chmod(exe, 0o755)
        self.assertEqual(self.ctl("show", "-p", "LoadState", "--value", "minecraft-test"), (0, "loaded"))
        self.assertEqual(self.ctl("is-active", "minecraft-test"), (0, "active"))
        self.assertEqual(self.ctl("show", "-p", "ActiveState", "--value", "minecraft-test"), (0, "active"))
        self.assertEqual(self.ctl("restart", "minecraft-test")[0], 0)
        self.assertEqual(self.ctl("restart", "--no-block", "minecraft-test.service")[0], 0)
        self.assertEqual(self.ctl("--no-block", "restart", "minecraft-test")[0], 0)
        self.assertEqual(self.ctl("stop", "minecraft-test")[0], 0)
        self.assertEqual(self.ctl("start", "minecraft-test")[0], 0)
        self.assertEqual(open(log).read().splitlines(),
                         ["is-active", "is-active", "restart", "restart --no-block", "restart --no-block", "stop",
                          "start"])


if __name__ == "__main__":
    unittest.main()
