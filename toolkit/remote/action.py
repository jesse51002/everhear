# action.py: vfg action run. Appended after common.py and ARGS. ARGS:
#   {"mode": "handler", "name": "teleport", "task": "T7", "args": {...the action's fields...},
#    "handler": "<actions/<name>/handler.py source>", "places": "<remote/places.py source>",
#    "mc": "/opt/mc", "vfg": "/opt/vfg"}
#   {"mode": "commands", "task": "T7", "commands": ["give ...", ...], "mc": "/opt/mc", "vfg": "/opt/vfg"}
#
# It prints exactly ONE line on stdout, {"ok": true|false, "summary": "...", ...}, and exits 0
# whenever the action ran, even when ok is false. It exits non-zero (common.py's die) only when
# the action could not run at all: RCON down is 5, bad ARGS 2, the scripts' _lib missing 3.
#
# Handler mode puts <vfg>/scripts/_lib on sys.path (so `from mc import rcon, pos, log` works),
# registers the places source as the module `places`, execs the handler source as the module
# `handler` and calls handler.run(args), args being the fields plus "task". While it runs,
# stdout is stderr: mc.log() and print() never reach the JSON line. An exception is
# {"ok": false, "summary": "handler error: <Type>: <message>"}.
#
# Commands mode runs the commands in order through RCON, refusing the owner's commands and
# anything that places or removes blocks before it sends any, and stops at the first reply
# Minecraft marks as an error: {"ok", "summary": replies joined with "; ", "replies", "failed"}.
# An empty reply is "(no reply)" in replies. A failing `execute as|at ... run ...`, or one aimed
# at an offline player, also replies nothing, so an `execute` without a reply is said plainly
# in the summary as maybe not done; ok stays true, since nobody can tell.
import traceback
import types

# The owner's commands: the same words as scripts/_lib/mc.py's REFUSED and actions/_types.py.
REFUSED = frozenset(["stop", "op", "deop", "ban", "ban-ip", "pardon", "pardon-ip",
                     "whitelist", "kick", "save-off"])
BLOCK_COMMANDS = frozenset(["setblock", "fill", "clone", "place", "fillbiome"])
RCON_MAX = 1400

# The start of every reply Minecraft 26.3 sends instead of doing what was asked: the
# failure messages of its own language file. A parse error also carries <--[HERE]
# (common.py's mc_refused). Replies that only say nothing needed doing ("Nothing
# changed", "already set to", "Test failed") are answers, not errors.
ERROR_STARTS = (
    "Unknown or incomplete command", "Incorrect argument for command", "Could not parse command",
    "Unknown ", "Invalid ", "Incomplete (expected", "Expected ", "Unclosed quoted string",
    "Integer must not", "Long must not", "Float must not", "Double must not", "Trailing data found",
    "Unexpected trailing data", "Missing ", "Only one ", "Only players may", "Only whole numbers",
    "Selector not allowed", "Tags aren't allowed", "Min cannot be bigger", "Distance cannot be negative",
    "Level shouldn't be negative", "Limit must be at least", "Property '", "Element '", "Tag '",
    "No matches for selector", "Chat message was too long", "The tick count must",
    "No entity was found", "No player was found", "That player does not exist", "That position is ",
    "Can't ", "Cannot ", "Could not ", "Couldn't ", "Unable to ", "There is no ", "Found no elements matching",
    "No targets accepted", "No relevant score holders", "No living entities", "No bossbar exists",
    "A bossbar already exists", "No schedules with ID", "Too many ", "Failed to ", "Monsters cannot be summoned",
    "Players can't be ridden", "An unexpected error occurred", "Maximum number of contexts",
    "While instantiating macro", "Entity ", "Attribute ", "Modifier ", "Block ", "World border cannot",
    "The range of the random value", "Source position ", "Target position ", "The target block is not",
    "Selected entity is not a waypoint", "You must be on a team", "You can only trigger",
    "You cannot trigger", "Enable only works", "This argument accepts a single NBT value",
    "Nothing changed. Targets either have no item", "Unable to apply this effect", "Option '", "Can only ",
    "Reload failed", "Must specify", "The source does not have", "The target does not have",
)
ERROR_PARTS = (
    " is not a valid entity for this command", " is not holding any item", " cannot support that enchantment",
    " is higher than the maximum level of ", " couldn't start riding ", " is not riding any vehicle",
    " cannot be spectated", " is not in spectator mode", " returned invalid value",
    "' does not exist", "' already exists", " does not exist for clock ",
)
# Replies that start with a name, which may be any word: never read as an error.
ANSWERS = (" has the following entity data: ", " has the following block data: ",
           " has the following contents: ")


def mc_error(out):
    """True when `out` is Minecraft refusing or failing a command."""
    if mc_refused(out) or out.startswith("Unknown or incomplete command"):
        return True
    if any(a in out for a in ANSWERS):
        return False
    return out.startswith(ERROR_STARTS) or any(p in out for p in ERROR_PARTS)


def command_problem(cmd):
    """Why a command may not run, or None: its first word and the word after every `run`."""
    if any(c in cmd for c in "\r\n\x00"):
        return "a command is one line"
    n = len(cmd.encode("utf-8"))
    if n > RCON_MAX:
        return "the command is %d bytes; RCON takes at most %d" % (n, RCON_MAX)
    words = cmd.split()
    checks = words[:1] + [words[i + 1] for i, w in enumerate(words[:-1]) if w.lower() == "run"]
    for w in checks:
        w = w.lstrip("/").lower()
        if w.startswith("minecraft:"):
            w = w[len("minecraft:"):]
        if w in BLOCK_COMMANDS:
            return "%s places or removes blocks" % w
        if w in REFUSED:
            return "%s is the server owner's to do" % w
    return None


NO_REPLY = "(no reply)"


def silent(cmd):
    """What the summary says for a command Minecraft answered with nothing."""
    if cmd.split()[0].lower() == "execute":
        return "`%s` gave no reply, so it may not have happened" % cmd
    return "`%s` gave no reply" % cmd


def emit(result):
    sys.stdout.write(json.dumps(result, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


def one_line(s):
    return " ".join(str(s).split())


def run_commands():
    cmds = ARGS.get("commands")
    if not isinstance(cmds, list) or not cmds or not all(isinstance(c, str) for c in cmds):
        die(2, "commands: a list of at least one command")
    clean = []
    for c in cmds:
        c = c.strip()
        if c.startswith("/"):
            c = c[1:].lstrip()
        why = "empty command" if not c else command_problem(c)
        if why:
            emit({"ok": False, "summary": "refused: %s: %s" % (why, c[:200]), "replies": [], "failed": c})
            return
        clean.append(c)
    try:
        r = Rcon(ARGS["mc"])
    except RconDown as e:
        die(5, str(e))
    replies, failed, said = [], None, []
    try:
        for c in clean:
            try:
                out = r.command(c)
            except RconDown as e:
                failed = c
                said.append("%s, before '%s' ran" % (e, c))
                break
            if out.strip():
                replies.append(out)
                said.append(one_line(out))
            else:
                replies.append(NO_REPLY)
                said.append(silent(c))
            if mc_error(out):
                failed = c
                break
    finally:
        r.close()
    emit({"ok": failed is None, "summary": "; ".join(said), "replies": replies, "failed": failed})


def load_module(name, source, filename):
    mod = types.ModuleType(name)
    mod.__file__ = filename
    sys.modules[name] = mod
    exec(compile(source, filename, "exec"), mod.__dict__)
    return mod


def run_handler():
    name = ARGS.get("name")
    if not isinstance(name, str) or not name or not isinstance(ARGS.get("handler"), str) \
            or not isinstance(ARGS.get("places"), str) or not isinstance(ARGS.get("args"), dict):
        die(2, "handler mode needs name, handler, places and args")
    lib = os.path.join(ARGS["vfg"], "scripts", "_lib")
    if not os.path.exists(os.path.join(lib, "mc.py")):
        die(3, "%s/mc.py is missing: vfg script deploy" % lib)
    try:
        Rcon(ARGS["mc"]).close()
    except RconDown as e:
        die(5, str(e))
    sys.path.insert(0, lib)
    # mc reads the RCON settings from here, and names its log lines and TAG after argv[0]'s folder.
    os.environ["VFG_MC_PROPERTIES"] = os.path.join(ARGS["mc"], "server.properties")
    sys.argv = [os.path.join(ARGS["vfg"], "actions", name, "handler.py")]
    args = dict(ARGS["args"])
    args["task"] = ARGS.get("task") or ""
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        try:
            load_module("places", ARGS["places"], "places.py")
            handler = load_module("handler", ARGS["handler"], "actions/%s/handler.py" % name)
            result = handler.run(args)
            if not isinstance(result, dict) or not isinstance(result.get("ok"), bool) \
                    or not isinstance(result.get("summary"), str):
                result = {"ok": False, "summary": "handler error: run() returned %s, not "
                                                  "{\"ok\": bool, \"summary\": str}" % type(result).__name__}
        except (Exception, SystemExit) as e:
            traceback.print_exc()
            result = {"ok": False, "summary": one_line("handler error: %s: %s" % (type(e).__name__, e))}
    finally:
        sys.stdout = real_stdout
    emit(result)


def main():
    if not isinstance(ARGS.get("mc"), str) or not isinstance(ARGS.get("vfg"), str):
        die(2, "ARGS needs mc and vfg")
    if ARGS.get("mode") == "commands":
        run_commands()
    elif ARGS.get("mode") == "handler":
        run_handler()
    else:
        die(2, "mode: handler or commands")


if __name__ == "__main__":  # `python3 -u -` on EC2; tests load the functions without running
    main()
