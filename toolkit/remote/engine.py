# engine.py: the box's side of vfg engine (the game engine spec). Appended after common.py and ARGS:
#   {"vfg": "/opt/vfg", "op": "ask", "requests": [{"op": "status", "args": {}}, ...], "timeout": 10}
#   {"vfg": "/opt/vfg", "op": "deploy", "server": "main", "tar": "<base64 tar.gz of content/>",
#    "reload": null | ["fireball.py", ...] | "all", "timeout": 60}
# It speaks the engine's control socket, <vfg>/engine/engine.sock (engine/vfg_engine/server.py has
# the protocol): one connection a request, one JSON line in, {"op", "args", "timeout_s"}, and one
# JSON line out, the reply, {"ok": true|false, "error"?, ...}, or one the socket made itself, with
# "reason" (bad_request, timeout, stopping, failed). Requests go in order, each waited for up to
# "timeout" seconds, which the engine is told, and MARGIN_S more, so its own "did not answer in
# time" is the one that comes back; the first one that gets no answer stops the rest.
#
#   {"vfg": "/opt/vfg", "mc": "/opt/mc", "op": "test", "server": "main", "name": "fire_staff_steve",
#    "tests": "<tar of tests/>", "content": "<tar of content/>" | null, "bindings": [...], "timeout": 60,
#    "boot": 60, "tail": 15, "live": {"vfg", "mc"}?}   on the TEST server: livetest (run_test)
# A deploy is the made content of one server, made/<server>/content/ on the PC, into
# <vfg>/made/<server>/content/ here: unpacked beside it, then swapped in under run/deploy.lock, and
# left as it is when every file is the same. Then, with
# "reload", the engine reloads those files there ("all": every content dir, the op with no paths).
#
# It always prints ONE JSON object and exits 0, so vfg reads what got where:
#   {"replies": [reply, ...], "down": "<why>"?, "timeout": "<why>"?, "deployed": {...}?}
# "down": the engine is not running (no socket, or it refused the connection); "timeout": it took
# the request and did not answer in time (it may still act on it); "deployed": {"folder", "files",
# "changed", "removed", "swapped"}. A deploy that cannot be made is exit 1, with why.
import base64
import io
import re
import shutil
import tarfile

BASE = ARGS["vfg"]
SOCK = os.path.join(BASE, "engine", "engine.sock")
SERVER_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
REPLY_MAX = 64 * 1024 * 1024
MARGIN_S = 2.0
SUN_PATH_MAX = 107   # a Unix socket's address holds 108 bytes, its last a NUL


def connect(s, path):
    """s.connect(path). A path longer than a socket's address holds (a realmc stand-in's <vfg> is
    deep in the PC's folders; the box's /opt/vfg is not) is reached through its folder instead,
    /proc/self/fd/<the folder>/<name>, which the kernel resolves as the path itself (a link there
    to a socket elsewhere included)."""
    if len(os.fsencode(path)) <= SUN_PATH_MAX:
        s.connect(path)
        return
    fd = os.open(os.path.dirname(path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        s.connect("/proc/self/fd/%d/%s" % (fd, os.path.basename(path)))
    finally:
        os.close(fd)


def ask(request, timeout):
    """One request, its reply: (reply, None), or (None, ("down"|"timeout", why))."""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    except OSError as e:
        return None, ("down", "cannot open a socket (%s)" % e)
    try:
        s.settimeout(min(5.0, timeout + MARGIN_S))
        try:
            connect(s, SOCK)
        except FileNotFoundError:
            return None, ("down", "the engine is not running: no control socket at %s (vfg up starts vfg-engine)" % SOCK)
        except socket.timeout:
            return None, ("timeout", "the engine's control socket %s took no connection within %g s"
                          % (SOCK, min(5.0, timeout)))
        except (ConnectionRefusedError, PermissionError, OSError) as e:
            return None, ("down", "the engine is not running: its control socket %s refused us (%s)"
                          % (SOCK, e.strerror or e))
        s.settimeout(timeout + MARGIN_S)
        s.sendall((json.dumps(dict(request, timeout_s=timeout), ensure_ascii=False) + "\n").encode("utf-8"))
        buf = b""
        while b"\n" not in buf:
            try:
                chunk = s.recv(65536)
            except socket.timeout:
                return None, ("timeout", "the engine took %s and did not answer within %g s; it may still do it"
                              % (request.get("op"), timeout))
            except OSError as e:
                return None, ("down", "the engine dropped the connection (%s)" % (e.strerror or e))
            if not chunk:
                break
            buf += chunk
            if len(buf) > REPLY_MAX:
                return {"ok": False, "error": "the engine's reply is over %d MB" % (REPLY_MAX >> 20)}, None
        line = buf.split(b"\n", 1)[0].strip()
        if not line:
            return None, ("down", "the engine closed the connection without an answer to %s" % request.get("op"))
        try:
            reply = json.loads(line.decode("utf-8", "replace"))
        except ValueError:
            return {"ok": False, "error": "the engine answered something that is not JSON: %r" % line[:200]}, None
        if not isinstance(reply, dict) or not isinstance(reply.get("ok"), bool):
            return {"ok": False, "error": "the engine's answer has no ok: %r" % line[:200]}, None
        if not reply["ok"] and reply.get("reason") == "timeout":
            return None, ("timeout", str(reply.get("error") or "the engine did not answer %s in time" % request.get("op")))
        if not reply["ok"] and reply.get("reason") == "stopping":
            return None, ("down", str(reply.get("error") or "the engine is stopping"))
        return reply, None
    finally:
        try:
            s.close()
        except OSError:
            pass


def ask_all(requests, timeout, out):
    replies = out.setdefault("replies", [])
    for req in requests:
        reply, fault = ask({"op": req.get("op"), "args": req.get("args") or {}}, timeout)
        if fault is not None:
            out[fault[0]] = fault[1]
            return
        replies.append(reply)


# ------------------------------------------------------------------ the content deploy

def on_disk(folder):
    """{path in the folder: (exec bit, bytes)}; no caches, no dotfiles; {} when there is none."""
    got = {}
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames if d != "__pycache__" and not d.startswith(".")]
        for fn in filenames:
            if fn.endswith(".pyc") or fn.startswith("."):
                continue
            path = os.path.join(dirpath, fn)
            with open(path, "rb") as f:
                got[os.path.relpath(path, folder)] = (bool(os.stat(path).st_mode & 0o111), f.read())
    return got


class Locked(object):
    """run/deploy.lock, the one every deploy here takes."""

    def __init__(self, path):
        self.path = path

    def __enter__(self):
        import fcntl
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        import fcntl
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)


def deploy(server, b64, folder="content"):
    """made/<server>/<folder>/ (content, or tests) from the tar: unpacked into .incoming-<pid>/
    beside it, then swapped in whole under run/deploy.lock, unless every file is the same already."""
    if not SERVER_RE.match(server or ""):
        die(1, "deploy refused: %r is not a server's name" % server)
    if folder not in ("content", "tests"):
        die(1, "deploy refused: %r is not content or tests" % folder)
    root = os.path.join(BASE, "made", server)
    dest = os.path.join(root, folder)
    try:
        tf = tarfile.open(fileobj=io.BytesIO(base64.b64decode(b64)), mode="r:gz")
        members = tf.getmembers()
    except Exception as e:
        die(1, "deploy refused: not a readable tar (%s)" % e)
    files = {}
    for m in members:
        parts = m.name.split("/")
        if m.name.startswith("/") or ".." in parts or "" in parts[:-1] or parts[0] != folder:
            die(1, "deploy refused: unsafe path %r" % m.name)
        if not (m.isfile() or m.isdir()):
            die(1, "deploy refused: %r is not a plain file or folder" % m.name)
        if m.isfile() and len(parts) > 1:
            files["/".join(parts[1:])] = (bool(m.mode & 0o111), tf.extractfile(m).read())
    os.makedirs(root, exist_ok=True)
    with Locked(os.path.join(BASE, "run", "deploy.lock")):
        have = on_disk(dest)
        changed = sorted(p for p in files if have.get(p) != files[p])
        removed = sorted(p for p in have if p not in files)
        swapped = bool(changed or removed) or not os.path.isdir(dest)
        if swapped:
            incoming = os.path.join(root, ".incoming-%d" % os.getpid())
            shutil.rmtree(incoming, ignore_errors=True)
            new = os.path.join(incoming, folder)
            os.makedirs(new)
            try:
                for rel, (exe, data) in sorted(files.items()):
                    path = os.path.join(new, *rel.split("/"))
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(path, "wb") as f:
                        f.write(data)
                    os.chmod(path, 0o755 if exe else 0o644)
                old = os.path.join(root, ".old-%s-%d" % (folder, os.getpid()))
                if os.path.lexists(dest):
                    os.rename(dest, old)
                os.rename(new, dest)
                shutil.rmtree(old, ignore_errors=True)
            finally:
                shutil.rmtree(incoming, ignore_errors=True)
    return {"folder": "made/%s/%s" % (server, folder), "files": len(files), "changed": changed, "removed": removed,
            "swapped": swapped}


# ------------------------------------------------------------------ a test, on the test server

TEST_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def tail(path, n):
    """The last n lines of a file, or []."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return [l.rstrip("\n") for l in f.readlines()[-n:]] if n > 0 else []
    except OSError:
        return []


def run_test():
    """vfg test: made/<server>/content/ and tests/ deployed here, then livetest run against this
    server (engine/vfg_engine/livetest.py: one engine started for the test, then stopped; its state
    in <vfg>/state/engine), with the library and this server's content, and the task's bindings
    bound first. Its report, its exit code and, after a failure, the last lines of its engine's log."""
    import subprocess
    server, name = ARGS.get("server") or "", ARGS.get("name") or ""
    if not TEST_NAME_RE.match(name):
        die(2, "%r is not a test's name" % name)
    live = ARGS.get("live") or {}
    if live:
        # the live server under another path: the same folder once links are followed, or the same
        # RCON port (as remote/test.py checks it)
        pairs = (("vfg folder", BASE, live.get("vfg")), ("Minecraft folder", ARGS["mc"], live.get("mc")))
        same = [what for what, here, there in pairs if there and os.path.realpath(here) == os.path.realpath(there)]
        if not same and live.get("mc"):
            try:
                port = read_props(os.path.join(ARGS["mc"], "server.properties")).get("rcon.port")
                if port and port == read_props(os.path.join(live["mc"], "server.properties")).get("rcon.port"):
                    same = ["RCON port %s" % port]
            except OSError:
                pass
        if same:
            die(3, "this test server is the live one (the same %s): a test never runs there" % same[0])
    deployed = {"tests": deploy(server, ARGS.get("tests") or "", "tests")}
    if ARGS.get("content"):
        deployed["content"] = deploy(server, ARGS["content"], "content")
    # The engine's package, its venv and its library: the test server's own when it has them, else
    # the live server's on the same host (the box's /opt/vfg/engine), run here on the test server.
    homes = [os.path.join(BASE, "engine")] + ([os.path.join(live["vfg"], "engine")] if live.get("vfg") else [])
    engine = next((h for h in homes if os.access(os.path.join(h, ".venv", "bin", "python"), os.X_OK)), None)
    if engine is None:
        die(3, "no engine to test with (%s): provision it with the engine"
            % ", ".join(os.path.join(h, ".venv", "bin", "python") for h in homes))
    python = os.path.join(engine, ".venv", "bin", "python")
    state = os.path.join(BASE, "state", "engine")
    os.makedirs(state, exist_ok=True)
    cmd = [python, "-m", "vfg_engine.livetest", "--properties", os.path.join(ARGS["mc"], "server.properties"),
           "--state-dir", state, "--content", os.path.join(engine, "library"),
           "--content", os.path.join(BASE, "made", server, "content"),
           "--test", os.path.join(BASE, "made", server, "tests", name + ".py"),
           "--boot", str(float(ARGS.get("boot") or 60)), "--timeout", str(float(ARGS.get("timeout") or 60))]
    if ARGS.get("bindings"):
        path = os.path.join(state, "bindings.%d.json" % os.getpid())
        atomic_write(path, json.dumps(ARGS["bindings"], ensure_ascii=False), 0o600)
        cmd += ["--bindings", path]
    env = dict(os.environ, PYTHONPATH=engine, PYTHONUNBUFFERED="1")
    limit = float(ARGS.get("timeout") or 60) + float(ARGS.get("boot") or 60) + 30
    try:
        p = subprocess.run(cmd, cwd=engine, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=limit)
        code, o, e = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as x:
        code, o, e = 5, x.stdout or b"", (x.stderr or b"") + b"\nlivetest did not end within its limit"
    finally:
        if ARGS.get("bindings"):
            try:
                os.remove(path)
            except OSError:
                pass
    return {"deployed": deployed, "test": {
        "code": code, "out": o.decode("utf-8", "replace"), "err": e.decode("utf-8", "replace")[-4000:],
        "log": tail(os.path.join(state, "engine.log"), int(ARGS.get("tail") or 0)) if code != 0 else []}}


def main():
    timeout = float(ARGS.get("timeout") or 10)
    out = {"replies": []}
    if ARGS.get("op") == "ask":
        ask_all(ARGS.get("requests") or [], timeout, out)
    elif ARGS.get("op") == "deploy":
        server = ARGS.get("server") or ""
        out["deployed"] = deploy(server, ARGS.get("tar") or "")
        wanted = ARGS.get("reload")
        if wanted == "all":
            ask_all([{"op": "reload", "args": {}}], timeout, out)
        elif wanted:
            dest = os.path.join(BASE, "made", server, "content")
            for p in wanted:
                parts = str(p).split("/")
                if str(p).startswith("/") or ".." in parts or "" in parts:
                    die(1, "reload refused: %r is not a file in made/%s/content" % (p, server))
            paths = [os.path.join(dest, *str(p).split("/")) for p in wanted]
            ask_all([{"op": "reload", "args": {"paths": paths}}], timeout, out)
    elif ARGS.get("op") == "test":
        out.update(run_test())
    else:
        die(2, "unknown op %r" % ARGS.get("op"))
    print(json.dumps(out, ensure_ascii=False))


main()
