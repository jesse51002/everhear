# commands.py: which server commands may run. Python 3.9+, standard library only.
#
# The one list of refused commands, deployed with the kit: mc.py imports it, so no script
# sends the owner's commands; actions/_types.py loads it by path on the PC, so an action's
# Command refuses them and the block commands too; the implementers' guard loads it by path;
# and vfg ships it with the programs that need it. A test checks that each uses this file and
# that no other writes the words out again (toolkit/tests, TestOneRefusedList).

# The owner's commands: never an action's.
REFUSED = frozenset(["stop", "op", "deop", "ban", "ban-ip", "pardon", "pardon-ip",
                     "whitelist", "kick", "save-off"])

# Commands that place or remove blocks: building is an action's that builds, else an implementer's.
BLOCK_COMMANDS = frozenset(["setblock", "fill", "clone", "place", "fillbiome"])

# What one RCON packet carries, in bytes of UTF-8.
RCON_MAX = 1400


def command_problem(cmd):
    """Why a command may not run, in words for the brain, or None.

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
            return ("%s places or removes blocks, and building is an action's that builds it, else an "
                    "implementer's: hand it off" % w)
        if w in REFUSED:
            return "%s is the server owner's to do, never an action's" % w
    return None
