# commands.py: which server commands an action may send. Python 3.9, standard library only.
#
# The one copy of these rules. toolkit/vfg ships it to EC2 ahead of remote/action.py (as the
# module `commands`, the way place.py gets places.py), and actions/_types.py loads this same
# file by path on the PC, so the action planner's refusal and EC2's are the same words.
# scripts/_lib/mc.py and the orchestrator's guard keep their own REFUSED; a test keeps every
# list equal.

# The owner's commands: never an action's.
REFUSED = frozenset(["stop", "op", "deop", "ban", "ban-ip", "pardon", "pardon-ip",
                     "whitelist", "kick", "save-off"])

# Commands that place or remove blocks: building is the orchestrator's.
BLOCK_COMMANDS = frozenset(["setblock", "fill", "clone", "place", "fillbiome"])

# What one RCON packet carries, in bytes of UTF-8.
RCON_MAX = 1400


def command_problem(cmd):
    """Why a command may not run, in words for the action planner, or None.

    Looks at its first word and at the word after every `run`, so
    `execute as @a at @s run fill ...` and `return run setblock ...` are caught."""
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
            return "%s places or removes blocks, and building is the orchestrator's: hand it on" % w
        if w in REFUSED:
            return "%s is the server owner's to do, never an action's" % w
    return None
