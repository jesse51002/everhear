# scripts.py: the box's side of vfg action run, vfg script deploy|logs|list|save and vfg state.
# Appended after common.py, ARGS and scripts/_lib/instances.py (the module `instances`), the
# code that starts, stops and edits a copy, which the stop and edit scripts import too. ARGS:
#   {"vfg": "/opt/vfg", "op": "deploy", "tars": [{"root": "scripts"|"made/<server>/scripts", "tar": ...,
#    "stamps": {"_lib": "<sha256>", "galick_gun": ...}}]}
#   {"vfg": "/opt/vfg", "mc": "/opt/mc", "op": "run", "action": "galick_gun", "script": "galick_gun",
#    "source": "library"|"made", "server": "main", "values": {...}, "live": [...], "runs": "once"|
#    "until_stopped", "task": "galick_gun_styrk"|null, "as": null|"steve_gun", "base": null|
#    "galick_gun_steve", "tars": [...] | "stamps": [{"root", "stamps"}], "owner": ..., "poll": 0.5,
#    "wait": 20, "on_result": false}
# "base" is the name the copy takes in place of the action's (vfg action run --for: the brain's own
# copy, <action>_<player>), then with _2, _3 ... (instances.Box.pick).
#
# A deploy keeps each folder's stamp, the PC's hash of what it sent (toolkit/vfg, stamp), in the
# folder, as .vfg-stamp. A run with "stamps" in place of "tars" deploys nothing: when every folder
# it names has that stamp, it starts the copy; when one does not (missing, another, or none: a
# folder an older vfg swapped in has none), it starts nothing and answers {"needs": "tars",
# "stale": ["scripts/_lib", ...]}, exit 0, and vfg sends the run again with the tars.
#   {"vfg": "/opt/vfg", "op": "stale", "stamps": [{"root", "stamps"}]}   answers {"stale": [...]}, the
#    folders a deploy would have to send (vfg test, before it runs a test there)
#   {"vfg": "/opt/vfg", "op": "info", "copy": "galick_gun_2"}
#   {"vfg": "/opt/vfg", "op": "logs", "name": "galick_gun_2", "n": 40, "follow": false}
#   {"vfg": "/opt/vfg", "op": "copies"}
#   {"vfg": "/opt/vfg", "mc": "/opt/mc", "op": "state", "items": false}   with items, each online
#    player also gets "items": the replies to data get Inventory, equipment and SelectedItemSlot;
#    and its state, read from Health, foodLevel, XpLevel, playerGameType and active_effects and
#    the armor and max_health attributes: "health", "max_health", "food", "xp_level", "armor",
#    "game_mode" and "effects" ([{"id", "amplifier", "duration"}], the duration in ticks, -1
#    for infinite), each null when Minecraft's reply could not be read (never a guess)
#
# `run` deploys (or checks the stamps), starts the copy (instances.Box.start) and waits: a once copy until it ends or
# "wait" s pass, an until-stopped one until its first result() or "wait" s (the 2 s check),
# counted from the op's start, so the deploy and the RCON check are inside it (at least 0.5 s
# is left for the copy). It prints one JSON object: {"copy", "pid", "started", "running",
# "code", "result", "error", "tail"}; when it ended with an error, "error" is the last line of
# the one it crashed on (<copy>.error, mc.py's) or null, and "tail" the last lines it printed.
# Before anything starts: Minecraft not answering RCON is exit 5, the script not deployed 3,
# `as=` a running copy's name 1.
import base64
import io
import re
import shutil
import tarfile

ROOT_RE = re.compile(r"^(scripts|made/[a-z0-9][a-z0-9_-]{0,47}/scripts)$")
PLAYER_RE = re.compile(r"^[A-Za-z0-9_]{1,16}$")
BASE = ARGS["vfg"]
RUN = os.path.join(BASE, "run")
RUN_MIN_WAIT = 0.5    # what a copy is waited for at least (or the whole wait, when shorter), whatever the deploy took
STAMP = ".vfg-stamp"  # in a deployed folder: the stamp vfg sent with it
STAMP_RE = re.compile(r"^[0-9a-f]{64}$")


def box(rcon=None):
    return instances.Box(BASE, ARGS.get("mc") or "/opt/mc", owner=ARGS.get("owner") or "",
                         poll=ARGS.get("poll") or 0.5, rcon=rcon)


def on_disk(folder):
    """{path from the folder: (exec bit, bytes)} of what is deployed there; no caches."""
    got = {}
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames if d != "__pycache__" and not d.startswith(".")]
        for fn in filenames:
            if fn.endswith(".pyc") or fn.startswith("."):
                continue
            path = os.path.join(dirpath, fn)
            try:
                with open(path, "rb") as f:
                    got[os.path.relpath(path, folder)] = (bool(os.stat(path).st_mode & 0o111), f.read())
            except OSError:
                return None
    return got


def write_stamp(folder, value):
    tmp = os.path.join(folder, "%s.tmp-%d" % (STAMP, os.getpid()))
    with open(tmp, "w", encoding="ascii") as f:
        f.write(value + "\n")
    os.replace(tmp, os.path.join(folder, STAMP))


def scripts_root(root, top):
    """<root>/<top> below /opt/vfg, refused unless root is a scripts folder and top a script (or _lib)."""
    if not ROOT_RE.match(root):
        die(1, "refused: %r is not a scripts folder" % root)
    if not ((top == "_lib" and root == "scripts") or instances.NAME_RE.match(top)):
        die(1, "refused: %r is not a script name" % top)
    return os.path.join(BASE, *root.split("/") + [top])


def stale(entries):
    """Of [{"root", "stamps": {folder: stamp}}], the folders deployed here with another stamp or
    none, or missing: ["scripts/_lib", ...]. No lock: a stamp moves with its folder, so a read
    mid-swap finds the old folder's, the new one's, or none (stale, and the tars come)."""
    out = []
    for t in entries:
        for top, want in sorted((t.get("stamps") or {}).items()):
            try:
                with open(os.path.join(scripts_root(t["root"], top), STAMP), encoding="ascii") as f:
                    have = f.read().strip()
            except (OSError, ValueError):
                have = None
            if not (STAMP_RE.match(want or "") and have == want):
                out.append("%s/%s" % (t["root"], top))
    return out


def deploy(b64, root="scripts", stamps=None):
    """Unpack a tar.gz into <root>/.incoming-<pid>/, then swap each folder in, one deploy at
    a time. A folder the box has already, file for file, is left as it is: a copy starting
    meanwhile never finds its _lib half swapped. Each folder keeps its stamp from `stamps`
    ({folder: stamp}): a swapped one gets it before the swap, so the two never part."""
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
    tops, files = [], {}
    for m in members:
        name = m.name
        parts = name.split("/")
        if name.startswith("/") or ".." in parts or "" in parts[:-1]:
            die(1, "deploy refused: unsafe path %r" % name)
        if not (m.isfile() or m.isdir()):
            die(1, "deploy refused: %r is not a plain file or folder" % name)
        top = parts[0]
        if not ((top == "_lib" and root == "scripts") or instances.NAME_RE.match(top)):
            die(1, "deploy refused: %r is not a script name" % top)
        if top not in tops:
            tops.append(top)
        if m.isfile():
            files.setdefault(top, {})["/".join(parts[1:])] = (bool(m.mode & 0o111), tf.extractfile(m).read())
    with instances._Flock(os.path.join(RUN, "deploy.lock")):
        swap = [t for t in tops if on_disk(os.path.join(dest_root, t)) != files.get(t, {})]
        incoming = os.path.join(dest_root, ".incoming-%d" % os.getpid())
        shutil.rmtree(incoming, ignore_errors=True)
        os.makedirs(incoming)
        try:
            for m in members:
                if m.name.split("/")[0] not in swap:
                    continue
                dest = os.path.join(incoming, *[p for p in m.name.split("/") if p])
                if m.isdir():
                    os.makedirs(dest, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as f:
                    f.write(files[m.name.split("/")[0]]["/".join(m.name.split("/")[1:])][1])
                os.chmod(dest, 0o755 if m.mode & 0o111 else 0o644)
            for top in tops:
                given = (stamps or {}).get(top)
                folder = os.path.join(incoming if top in swap else dest_root, top)
                if given and STAMP_RE.match(given) and os.path.isdir(folder):
                    write_stamp(folder, given)
            for top in swap:
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


def answer(obj):
    print(json.dumps(obj, ensure_ascii=False))


def op_deploy():
    """One tar ("tar", into "root" or scripts/), or several ("tars": [{"root", "tar"}])."""
    tars = ARGS.get("tars") or [{"root": ARGS.get("root") or "scripts", "tar": ARGS["tar"]}]
    for t in tars:
        tops = deploy(t["tar"], t["root"], t.get("stamps"))
        root = t["root"]
        print(("deployed: " if root == "scripts" else "deployed %s: " % root[:-len("/scripts")])
              + " ".join(tops))


def op_run():
    """Deploy, start one copy of an action and wait for it, as the module comment says."""
    began = time.time()
    if ARGS.get("tars"):
        for t in ARGS["tars"]:
            deploy(t["tar"], t["root"], t.get("stamps"))
    else:
        need = stale(ARGS.get("stamps") or [])
        if need:
            answer({"needs": "tars", "stale": need})   # nothing started: vfg sends the tars
            return
    meta = {"action": ARGS["action"], "script": ARGS["script"], "source": ARGS.get("source") or "library",
            "values": ARGS.get("values") or {}, "live": ARGS.get("live") or [],
            "runs": ARGS.get("runs") or "until_stopped", "task": ARGS.get("task")}
    if meta["source"] == "made":
        meta["server"] = ARGS["server"]
    try:
        Rcon(ARGS["mc"]).close()  # a copy with Minecraft down would only log its failures
    except RconDown as e:
        die(5, str(e))
    b = box()
    try:
        name, pid, proc = b.start(meta, ARGS.get("as"), base=ARGS.get("base"))
    except instances.NotDeployed as e:
        die(3, str(e))
    except instances.CopyError as e:
        die(1, str(e))
    # The caller's clock started before the deploy: a once copy past its timeout is answered
    # as still running, not cut off by vfg's or the brain's own timeout.
    wait = float(ARGS.get("wait") or instances.CHECK_S)
    left = max(min(RUN_MIN_WAIT, wait), wait - (time.time() - began))
    got = b.wait(name, proc, left, on_result=bool(ARGS.get("on_result")))
    got.update(copy=name, pid=pid, started=b.meta(name)["started"])
    if not got["running"] and got["code"]:
        got.update(error=b.error(name), tail=b.tail(name, 20))
    answer(got)


def op_stale():
    answer({"stale": stale(ARGS.get("stamps") or [])})


def op_info():
    name = ARGS["copy"]
    b = box()
    if not (os.path.exists(b.path(name, ".json")) or os.path.exists(b.path(name, ".pid"))):
        die(1, "no copy %s — vfg script list" % name)
    answer(b.copy(name))


def op_copies():
    answer(box().copies())


def op_logs():
    name = ARGS["name"]
    log = os.path.join(RUN, name + ".log")
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


# Vanilla has no weather query; an inline predicate answers it ("Test passed" or "Test
# failed"). 26.3 wants "type" in it, not the older "condition". It reads the rain and thunder
# players see, which fade in and out over a few seconds after `weather ...`.
WEATHER = 'execute if predicate {"type":"minecraft:weather_check","%s":true}'


def time_of_day(r):
    """(the time of day 0..23999, the day number). On 26.3 `time query day` is the day
    timeline and `time query time` the overworld clock, every tick since the world began;
    before it, `time query daytime` and `time query day` (the day number)."""
    out = r.command("time query day")
    m = re.search(r"Timeline \S+ is at (-?\d+)", out)
    if m:
        c = re.search(r"is at (-?\d+)", r.command("time query time"))
        return int(m.group(1)) % 24000, (int(c.group(1)) // 24000 if c else None)
    day = re.search(r"(-?\d+)", out)
    d = re.search(r"(-?\d+)", r.command("time query daytime"))
    return (int(d.group(1)) % 24000 if d else None), (int(day.group(1)) if day else None)


# What vfg state --items asks of each online player, as the replies the brain's listing reads.
ITEMS = ("data get entity {p} Inventory",
         "execute if data entity {p} equipment run data get entity {p} equipment",
         "data get entity {p} SelectedItemSlot")

# And its state. Player data on 26.3 (scripts/_test/mc_26_3.py has the replies, and says which
# are recorded): Health a float (20.0f), foodLevel, XpLevel and playerGameType ints,
# active_effects a list of {id, amplifier (a byte, left out at 0), duration (ticks, -1 infinite),
# ...}, absent with no effect on, so Minecraft answers "Found no elements matching
# active_effects". Armor is no NBT: the armor attribute is what the armor worn adds up to.
GAME_MODES = {0: "survival", 1: "creative", 2: "adventure", 3: "spectator"}
NUMBER_RE = re.compile(r"^(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)[bBsSlLfFdD]?$")
ATTRIBUTE_RE = re.compile(r"attribute .+ for entity .+ is (-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*$", re.S)


def nbt_number(r, player, path):
    """A number the player's data holds at `path` (`20.0f`, `18`), as a float; None for any
    other reply."""
    got = entity_data(r, player, path)
    m = NUMBER_RE.match(got.strip()) if got is not None else None
    return float(m.group(1)) if m else None


def attribute(r, player, name):
    """`attribute <p> minecraft:<name> get`: the value, its modifiers counted; None for any
    other reply."""
    m = ATTRIBUTE_RE.search(r.command("attribute %s minecraft:%s get" % (player, name)).strip())
    return float(m.group(1)) if m else None


def compounds(listed):
    """The {...} compounds of an SNBT list, each whole (nested ones inside it too)."""
    out, depth, quote, start = [], 0, "", None
    for i, ch in enumerate(listed):
        if quote:
            if ch == quote and listed[i - 1] != "\\":
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch in "{[":
            depth += 1
            if ch == "{" and depth == 2:
                start = i
        elif ch in "}]":
            if ch == "}" and depth == 2 and start is not None:
                out.append(listed[start:i + 1])
                start = None
            depth -= 1
    return out


def top_fields(compound):
    """A compound's own text, every nested compound and list in it left out: the hidden effect
    a stronger one keeps is not read as this one's."""
    out, depth, quote = [], 0, ""
    for i, ch in enumerate(compound):
        if quote:
            if ch == quote and compound[i - 1] != "\\":
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch in "{[":
            depth += 1
            continue
        elif ch in "}]":
            depth -= 1
            continue
        if depth == 1:
            out.append(ch)
    return "".join(out)


def effects(r, player):
    """[{"id", "amplifier", "duration"}] from active_effects; [] when Minecraft says the player
    has none; None for any other reply."""
    reply = r.command("data get entity %s active_effects" % player).strip()
    if reply.startswith("Found no elements matching active_effects"):
        return []
    m = DATA_RE.search(reply)
    if not m or not m.group(1).strip().startswith("["):
        return None
    out = []
    for c in compounds(m.group(1).strip()):
        own = top_fields(c)
        name = re.search(r"\bid:\s*\"([^\"]+)\"", own)
        if not name:
            continue
        amp = re.search(r"\bamplifier:\s*(-?\d+)b", own)
        dur = re.search(r"\bduration:\s*(-?\d+)\b", own)
        out.append({"id": name.group(1), "amplifier": int(amp.group(1)) % 256 if amp else 0,
                    "duration": int(dur.group(1)) if dur else None})
    return out


def player_state(r, p):
    """Health, food, XP level, game mode, effects and armor of one online player."""
    health = nbt_number(r, p, "Health")
    food, level, mode = (nbt_number(r, p, path) for path in ("foodLevel", "XpLevel", "playerGameType"))
    return {"health": health, "max_health": attribute(r, p, "max_health"),
            "food": int(food) if food is not None else None,
            "xp_level": int(level) if level is not None else None,
            "armor": attribute(r, p, "armor"),
            "game_mode": GAME_MODES.get(int(mode)) if mode is not None else None,
            "effects": effects(r, p)}


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
                entry = {
                    "name": p,
                    "pos": [int(float(x) // 1) for x in nums[:3]] if len(nums) >= 3 else None,
                    "dimension": dim.strip('"').split(":")[-1] if dim else None}
                if ARGS.get("items"):
                    # A player wearing nothing has no equipment: the execute if keeps that from failing.
                    entry["items"] = [r.command(c.format(p=p)) for c in ITEMS]
                    entry.update(player_state(r, p))
                world["online"].append(entry)
            world["daytime"], world["day"] = time_of_day(r)
            world["raining"] = passes(r, WEATHER % "raining")
            world["thundering"] = passes(r, WEATHER % "thundering")
        finally:
            r.close()
    except RconDown as e:
        world["rcon"] = str(e)
    world["copies"] = box().copies()
    world["ms"] = int((time.time() - t0) * 1000)
    print(json.dumps(world, ensure_ascii=False))
    if world["rcon"]:
        die(5, world["rcon"])


{"deploy": op_deploy, "run": op_run, "info": op_info, "logs": op_logs, "copies": op_copies,
 "state": op_state, "stale": op_stale}[ARGS["op"]]()
