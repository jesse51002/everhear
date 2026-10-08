# scripts.py: vfg script deploy|run|stop|logs|list, on EC2. ARGS:
#   {"vfg": "/opt/vfg", "op": "deploy", "tar": "<base64 tar.gz>"}
#   {"vfg": "/opt/vfg", "op": "run", "name": "castle", "args": [...], "wait": 2, "tar": null|"..."}
#   {"vfg": "/opt/vfg", "op": "stop", "name": "castle"}
#   {"vfg": "/opt/vfg", "op": "logs", "name": "castle", "n": 40, "follow": false}
#   {"vfg": "/opt/vfg", "op": "list"}
import base64
import io
import re
import shutil
import signal
import subprocess
import tarfile

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
BASE = ARGS["vfg"]
SCRIPTS = os.path.join(BASE, "scripts")
RUN = os.path.join(BASE, "run")


def pidfile(name):
    return os.path.join(RUN, name + ".pid")


def logfile(name):
    return os.path.join(RUN, name + ".log")


def running_pid(name):
    """The script's pid when it is running; a stale pid file is removed."""
    path = pidfile(name)
    pid = read_pid(path)
    if pid is None:
        return None
    if pid_with_marker(pid, "VFG_SCRIPT=" + name):
        return pid
    try:
        os.remove(path)
    except OSError:
        pass
    return None


def deploy(b64):
    """Unpack a tar.gz into .incoming-<pid>/, then swap each folder in."""
    os.makedirs(SCRIPTS, exist_ok=True)
    os.makedirs(RUN, exist_ok=True)
    try:
        tf = tarfile.open(fileobj=io.BytesIO(base64.b64decode(b64)), mode="r:gz")
        members = tf.getmembers()
    except Exception as e:
        die(1, "deploy refused: not a readable tar (%s)" % e)
    tops = []
    for m in members:
        name = m.name
        parts = name.split("/")
        if name.startswith("/") or ".." in parts or "" in parts[:-1]:
            die(1, "deploy refused: unsafe path %r" % name)
        if not (m.isfile() or m.isdir()):
            die(1, "deploy refused: %r is not a plain file or folder" % name)
        top = parts[0]
        if top != "_lib" and not NAME_RE.match(top):
            die(1, "deploy refused: %r is not a script name" % top)
        if top not in tops:
            tops.append(top)
    incoming = os.path.join(SCRIPTS, ".incoming-%d" % os.getpid())
    shutil.rmtree(incoming, ignore_errors=True)
    os.makedirs(incoming)
    try:
        for m in members:
            dest = os.path.join(incoming, *[p for p in m.name.split("/") if p])
            if m.isdir():
                os.makedirs(dest, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            src = tf.extractfile(m)
            with open(dest, "wb") as f:
                shutil.copyfileobj(src, f)
            os.chmod(dest, 0o755 if m.mode & 0o111 else 0o644)
        for top in tops:
            new = os.path.join(incoming, top)
            cur = os.path.join(SCRIPTS, top)
            old = os.path.join(SCRIPTS, ".old-%s-%d" % (top, os.getpid()))
            if os.path.lexists(cur):
                os.rename(cur, old)
            os.rename(new, cur)
            if os.path.lexists(old):
                shutil.rmtree(old, ignore_errors=True)
    finally:
        shutil.rmtree(incoming, ignore_errors=True)
    return sorted(tops, key=lambda t: (t != "_lib", t))


def tail_lines(path, n):
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        block = 8192
        data = b""
        while size > 0 and data.count(b"\n") <= n:
            step = min(block, size)
            size -= step
            f.seek(size)
            data = f.read(step) + data
            block *= 2
    lines = data.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    return lines[-n:] if n > 0 else []


def write_out(data):
    sys.stdout.flush()
    sys.stdout.buffer.write(data)
    sys.stdout.flush()


def op_deploy():
    print("deployed: " + " ".join(deploy(ARGS["tar"])))


def op_run():
    name = ARGS["name"]
    pid = running_pid(name)
    if pid:
        die(1, "%s is running (pid %d) — vfg script stop %s" % (name, pid, name))
    if ARGS.get("tar"):
        deploy(ARGS["tar"])
    os.makedirs(RUN, exist_ok=True)
    folder = os.path.join(SCRIPTS, name)
    main_py = os.path.join(folder, "main.py")
    if not os.path.isfile(main_py):
        die(1, "%s is not deployed (no %s)" % (name, main_py))
    log = logfile(name)
    with open(log, "w", encoding="utf-8") as f:
        f.write("--- %s run %s %s ---\n" % (now_iso(), name, json.dumps(ARGS["args"], ensure_ascii=False)))
    env = dict(os.environ)
    env["VFG_SCRIPT"] = name
    env["PYTHONPATH"] = os.path.join(SCRIPTS, "_lib")
    env["PYTHONUNBUFFERED"] = "1"
    # mc.py finds RCON's settings by VFG_MC_PROPERTIES (default /opt/mc/...).
    env["VFG_MC_PROPERTIES"] = os.path.join(ARGS["mc"], "server.properties")
    # ...and the player a script acts for by default by VFG_OWNER (~/.vfg/config on the PC).
    if ARGS.get("owner"):
        env["VFG_OWNER"] = ARGS["owner"]
    else:
        env.pop("VFG_OWNER", None)
    logf = open(log, "ab")
    # No shell, and no start_new_session: the child is then not a group
    # leader, so `setsid` keeps its pid, which becomes the script's.
    proc = subprocess.Popen(["setsid", "nohup", "python3", "-u", main_py] + list(ARGS["args"]),
                            cwd=folder, stdin=subprocess.DEVNULL, stdout=logf,
                            stderr=subprocess.STDOUT, env=env, close_fds=True)
    logf.close()
    atomic_write(pidfile(name), "%d\n" % proc.pid)
    try:
        code = proc.wait(timeout=float(ARGS.get("wait") or 2))
    except subprocess.TimeoutExpired:
        print("running %s pid %d — vfg script logs %s" % (name, proc.pid, name))
        return
    # Its output: the last 40 lines of the log, without the log's header.
    with open(log, "rb") as f:
        header = f.readline().rstrip(b"\n")
    lines = tail_lines(log, 41)
    if lines and lines[0] == header:
        lines = lines[1:]
    lines = lines[-40:]
    if lines:
        write_out(b"\n".join(lines) + b"\n")
    try:
        os.remove(pidfile(name))
    except OSError:
        pass
    if code != 0:
        die(1, "%s exited with code %d" % (name, code))


def gone(pid):
    """True once `pid` no longer exists or is a zombie."""
    try:
        with open("/proc/%d/stat" % pid) as f:
            state = f.read().rsplit(")", 1)[1].split()[0]
        return state in ("Z", "X")
    except (OSError, IndexError):
        return True


def op_stop():
    name = ARGS["name"]
    pid = running_pid(name)
    if not pid:
        print("%s not running" % name)
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except PermissionError as e:
        die(1, "cannot stop %s: %s" % (name, e))
    deadline = time.time() + 5
    while time.time() < deadline and not gone(pid):
        time.sleep(0.1)
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        os.remove(pidfile(name))
    except OSError:
        pass
    print("stopped %s" % name)


def op_logs():
    name = ARGS["name"]
    log = logfile(name)
    if not os.path.exists(log):
        die(1, "no log for %s — it has not been run" % name)
    lines = tail_lines(log, int(ARGS.get("n") or 40))
    if lines:
        write_out(b"\n".join(lines) + b"\n")
    if not ARGS.get("follow"):
        return
    f = open(log, "rb")
    f.seek(0, 2)
    pos = f.tell()
    ino = os.fstat(f.fileno()).st_ino
    while True:
        time.sleep(0.25)
        try:
            st = os.stat(log)
        except OSError:
            continue
        if st.st_ino != ino or st.st_size < pos:
            f.close()
            f = open(log, "rb")
            ino = os.fstat(f.fileno()).st_ino
            pos = 0
        if st.st_size > pos:
            f.seek(pos)
            data = f.read()
            pos += len(data)
            try:
                write_out(data)
            except OSError:
                return


def op_list():
    if not os.path.isdir(SCRIPTS):
        return
    out = []
    for name in sorted(os.listdir(SCRIPTS)):
        if name.startswith((".", "_")) or not os.path.isdir(os.path.join(SCRIPTS, name)):
            continue
        pid = running_pid(name)
        if pid:
            try:
                since = hms(os.stat(pidfile(name)).st_mtime)
            except OSError:
                since = "?"
            out.append("%s running pid %d since %s" % (name, pid, since))
        else:
            out.append("%s stopped" % name)
    if out:
        print("\n".join(out))


{"deploy": op_deploy, "run": op_run, "stop": op_stop, "logs": op_logs, "list": op_list}[ARGS["op"]]()
