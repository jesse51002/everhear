# scripts.py: vfg script deploy|run|set|stop|logs|list|save and vfg state, on EC2. ARGS:
#   {"vfg": "/opt/vfg", "op": "deploy", "tar": "<base64 tar.gz>"}   into scripts/, or
#   {"vfg": "/opt/vfg", "op": "deploy", "tars": [{"root": "scripts"|"made/<server>/scripts", "tar": ...}]}
#   {"vfg": "/opt/vfg", "op": "run", "instance": "galick-alex", "script": "galick_gun",
#    "source": "library"|"made", "server": "main", "values": {...}|null, "argv": [...],
#    "task": "T7"|null, "wait": 2, "tars": [{"root": ..., "tar": ...}], "owner": ..., "poll": 0.5}
#   {"vfg": "/opt/vfg", "op": "set", "instance": "galick-alex", "live": {...},
#    "restart": null|{"values": {...}, "argv": [...]}, "wait": 2}
#   {"vfg": "/opt/vfg", "op": "info", "instance": "galick-alex"}
#   {"vfg": "/opt/vfg", "op": "stop", "name": "galick-alex"}
#   {"vfg": "/opt/vfg", "op": "logs", "name": "galick-alex", "n": 40, "follow": false}
#   {"vfg": "/opt/vfg", "op": "instances"}
#   {"vfg": "/opt/vfg", "mc": "/opt/mc", "op": "state"}
#
# An instance is one run of a script, named by `as=` or after its script. Its files in
# run/: <instance>.pid, <instance>.log, <instance>.json (what was started:
# {"instance", "script", "source", "values", "task", "started"}, plus "server" for a
# made script and "args" for one without VALUES) and <instance>.values.json, the live
# values `vfg script set` writes and kit.values reads again while it runs.
import base64
import io
import re
import shutil
import signal
import subprocess
import tarfile

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
ROOT_RE = re.compile(r"^(scripts|made/[a-z0-9][a-z0-9_-]{0,47}/scripts)$")
PLAYER_RE = re.compile(r"^[A-Za-z0-9_]{1,16}$")
BASE = ARGS["vfg"]
SCRIPTS = os.path.join(BASE, "scripts")
RUN = os.path.join(BASE, "run")


def pidfile(name):
    return os.path.join(RUN, name + ".pid")


def logfile(name):
    return os.path.join(RUN, name + ".log")


def metafile(name):
    return os.path.join(RUN, name + ".json")


def livefile(name):
    return os.path.join(RUN, name + ".values.json")


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def running_pid(name):
    """The instance's pid when it is running; a stale pid file is removed."""
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


def folder_of(meta):
    """Where an instance's script is deployed: scripts/<name>, or made/<server>/scripts/<name>."""
    if meta.get("source") == "made":
        return os.path.join(BASE, "made", meta["server"], "scripts", meta["script"])
    return os.path.join(SCRIPTS, meta["script"])


def meta_of(name):
    """An instance's <name>.json; one started before instances had it is its own script."""
    meta = read_json(metafile(name), None)
    if not isinstance(meta, dict):
        meta = {"instance": name, "script": name, "source": "library", "values": None,
                "task": None, "started": None}
    return meta


def deploy(b64, root="scripts"):
    """Unpack a tar.gz into <root>/.incoming-<pid>/, then swap each folder in."""
    if not ROOT_RE.match(root):
        die(1, "deploy refused: %r is not a scripts folder" % root)
    dest_root = os.path.join(BASE, *root.split("/"))
    os.makedirs(dest_root, exist_ok=True)
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
        if not ((top == "_lib" and root == "scripts") or NAME_RE.match(top)):
            die(1, "deploy refused: %r is not a script name" % top)
        if top not in tops:
            tops.append(top)
    incoming = os.path.join(dest_root, ".incoming-%d" % os.getpid())
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
            cur = os.path.join(dest_root, top)
            old = os.path.join(dest_root, ".old-%s-%d" % (top, os.getpid()))
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
    """One tar ("tar", into "root" or scripts/), or several ("tars": [{"root", "tar"}])."""
    tars = ARGS.get("tars") or [{"root": ARGS.get("root") or "scripts", "tar": ARGS["tar"]}]
    for t in tars:
        tops = deploy(t["tar"], t["root"])
        root = t["root"]
        print(("deployed: " if root == "scripts" else "deployed %s: " % root[:-len("/scripts")])
              + " ".join(tops))


def start(meta, argv, wait):
    """Start an instance as its meta says, detached, with `argv` after main.py.

    Writes <instance>.json and .pid; checks it after `wait` seconds and, if it
    has ended by then, prints its output and exits as it did."""
    name = meta["instance"]
    folder = folder_of(meta)
    main_py = os.path.join(folder, "main.py")
    if not os.path.isfile(main_py):
        die(1, "%s is not deployed (no %s)" % (meta["script"], main_py))
    os.makedirs(RUN, exist_ok=True)
    log = logfile(name)
    with open(log, "w", encoding="utf-8") as f:
        f.write("--- %s run %s %s ---\n" % (now_iso(), name, json.dumps(argv, ensure_ascii=False)))
    env = dict(os.environ)
    # VFG_SCRIPT is the pid marker (running_pid); VFG_INSTANCE names its tag, vfg_<instance>.
    env["VFG_SCRIPT"] = name
    env["VFG_INSTANCE"] = name
    # kit.values reads <VFG_RUN_DIR>/<instance>.values.json again every VFG_LIVE_POLL_S.
    env["VFG_RUN_DIR"] = RUN
    env["VFG_LIVE_POLL_S"] = str(ARGS.get("poll") or 0.5)
    env["PYTHONPATH"] = os.path.join(SCRIPTS, "_lib")
    env["PYTHONUNBUFFERED"] = "1"
    # mc.py finds RCON's settings by VFG_MC_PROPERTIES (default /opt/mc/...).
    env["VFG_MC_PROPERTIES"] = os.path.join(ARGS["mc"], "server.properties")
    # ...and the player a script acts for by default by VFG_OWNER (~/.vfg/config on the PC).
    if ARGS.get("owner"):
        env["VFG_OWNER"] = ARGS["owner"]
    else:
        env.pop("VFG_OWNER", None)
    meta["started"] = now_iso()
    atomic_write(metafile(name), json.dumps(meta, ensure_ascii=False) + "\n")
    logf = open(log, "ab")
    # No shell, and no start_new_session: the child is then not a group
    # leader, so `setsid` keeps its pid, which becomes the script's.
    proc = subprocess.Popen(["setsid", "nohup", "python3", "-u", main_py] + list(argv),
                            cwd=folder, stdin=subprocess.DEVNULL, stdout=logf,
                            stderr=subprocess.STDOUT, env=env, close_fds=True)
    logf.close()
    atomic_write(pidfile(name), "%d\n" % proc.pid)
    try:
        code = proc.wait(timeout=float(wait or 2))
    except subprocess.TimeoutExpired:
        return proc.pid
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
    return None


def op_run():
    name = ARGS["instance"]
    pid = running_pid(name)
    if pid:
        die(1, "%s is running (pid %d) — vfg script stop %s" % (name, pid, name))
    for t in ARGS.get("tars") or []:
        deploy(t["tar"], t["root"])
    meta = {"instance": name, "script": ARGS["script"], "source": ARGS.get("source") or "library",
            "values": ARGS.get("values"), "task": ARGS.get("task"), "started": None}
    if meta["source"] == "made":
        meta["server"] = ARGS["server"]
    if ARGS.get("values") is None:
        meta["args"] = list(ARGS["argv"])
    # A new run starts from its command: live values an earlier run was set to are gone.
    try:
        os.remove(livefile(name))
    except OSError:
        pass
    pid = start(meta, ARGS["argv"], ARGS.get("wait"))
    if pid:
        print("running %s pid %d — vfg script logs %s" % (name, pid, name))


def gone(pid):
    """True once `pid` no longer exists or is a zombie."""
    try:
        with open("/proc/%d/stat" % pid) as f:
            state = f.read().rsplit(")", 1)[1].split()[0]
        return state in ("Z", "X")
    except (OSError, IndexError):
        return True


def stop_instance(name):
    """SIGTERM its process group, SIGKILL after 5 s; False when it was not running."""
    pid = running_pid(name)
    if not pid:
        return False
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
    return True


def op_stop():
    name = ARGS["name"]
    if stop_instance(name):
        print("stopped %s" % name)
    else:
        print("%s not running" % name)


def op_info():
    name = ARGS["instance"]
    if not (os.path.exists(metafile(name)) or os.path.exists(pidfile(name))):
        die(1, "no instance %s — vfg script list" % name)
    print(json.dumps(instance(name), ensure_ascii=False))


def op_set():
    """Live values into <instance>.values.json; a restart with new command values."""
    name = ARGS["instance"]
    if not running_pid(name):
        die(1, "%s is not running — vfg script list" % name)
    meta = meta_of(name)
    live = ARGS.get("live") or {}
    if live:
        cur = read_json(livefile(name), {})
        if not isinstance(cur, dict):
            cur = {}
        cur.update(live)
        atomic_write(livefile(name), json.dumps(cur, ensure_ascii=False) + "\n")
    out = {"live": read_json(livefile(name), {}), "restarted": None}
    restart = ARGS.get("restart")
    if restart:
        stop_instance(name)
        meta["values"] = restart["values"]
        pid = start(meta, restart["argv"], ARGS.get("wait"))
        out["restarted"] = pid
    print(json.dumps(out, ensure_ascii=False))


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


def instance(name):
    """One instance as the PC reads it: its meta, live values, pid and since."""
    meta = meta_of(name)
    live = read_json(livefile(name), {})
    pid = running_pid(name)
    since = None
    if pid:
        try:
            since = hms(os.stat(pidfile(name)).st_mtime)
        except OSError:
            since = "?"
    meta.update({"instance": name, "live": live if isinstance(live, dict) else {},
                 "pid": pid, "since": since})
    return meta


def instance_names():
    """Every instance with a .json or a .pid in run/, by name."""
    names = set()
    try:
        files = os.listdir(RUN)
    except OSError:
        return []
    for fn in files:
        for ext in (".json", ".pid"):
            if fn.endswith(ext) and not fn.endswith(".values.json") and NAME_RE.match(fn[:-len(ext)]):
                names.add(fn[:-len(ext)])
    return sorted(names)


def op_instances():
    print(json.dumps([instance(n) for n in instance_names()], ensure_ascii=False))


# ------------------------------------------------------------------ state

DATA_RE = re.compile(r"has the following entity data: (.*)$", re.S)


def online(r):
    """Who is online, from `list`; a rank prefix before a name is dropped."""
    out = r.command("list")
    m = re.search(r"online:(.*)", out, re.S)
    names = []
    for part in (m.group(1).split(",") if m else []):
        words = part.strip().split()
        if words and PLAYER_RE.match(words[-1]) and words[-1] not in names:
            names.append(words[-1])
    return names


def entity_data(r, player, path):
    m = DATA_RE.search(r.command("data get entity %s %s" % (player, path)))
    return m.group(1).strip() if m else None


def passes(r, cmd):
    """`execute if ...` without run: "Test passed" or "Test failed"."""
    out = r.command(cmd)
    if mc_refused(out):
        return None
    return out.strip().lower().startswith("test passed")


def op_state():
    t0 = time.time()
    world = {"online": [], "daytime": None, "day": None, "raining": None, "thundering": None,
             "rcon": None}
    try:
        r = Rcon(ARGS["mc"])
        try:
            for p in online(r):
                pos = entity_data(r, p, "Pos")
                dim = entity_data(r, p, "Dimension")
                nums = re.findall(r"(-?[\d.]+(?:[eE]-?\d+)?)d", pos or "")
                world["online"].append({
                    "name": p,
                    "pos": [int(float(x) // 1) for x in nums[:3]] if len(nums) >= 3 else None,
                    "dimension": dim.strip('"').split(":")[-1] if dim else None})
            m = re.search(r"(-?\d+)", r.command("time query daytime"))
            world["daytime"] = int(m.group(1)) if m else None
            m = re.search(r"(-?\d+)", r.command("time query day"))
            world["day"] = int(m.group(1)) if m else None
            # Vanilla has no weather query; an inline predicate answers it.
            world["raining"] = passes(r, 'execute if predicate {"condition":"minecraft:weather_check",'
                                       '"raining":true}')
            world["thundering"] = passes(r, 'execute if predicate {"condition":"minecraft:weather_check",'
                                          '"thundering":true}')
        finally:
            r.close()
    except RconDown as e:
        world["rcon"] = str(e)
    world["instances"] = [i for i in (instance(n) for n in instance_names()) if i["pid"]]
    world["ms"] = int((time.time() - t0) * 1000)
    print(json.dumps(world, ensure_ascii=False))
    if world["rcon"]:
        die(5, world["rcon"])


{"deploy": op_deploy, "run": op_run, "set": op_set, "info": op_info, "stop": op_stop,
 "logs": op_logs, "instances": op_instances, "state": op_state}[ARGS["op"]]()
