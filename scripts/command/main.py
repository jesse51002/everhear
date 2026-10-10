"""Command: run up to five server commands in order, stopping at the first one Minecraft refuses; Minecraft's replies are the result."""
ASKS = ["give me speed five", "give me a steak, please, any food", "make it nighttime",
        "can you remove the darkness from me as well", "give me full netherite tools and netherite armor",
        "also you can remove the rain"]
from kit import values, result
import sys
import commands
import mc

MOST = 5
NO_REPLY = "(no reply)"
# The start of every reply Minecraft 26.3 sends instead of doing what was asked: the failure
# messages of its own language file. A parse error also carries <--[HERE]. Replies that only
# say nothing needed doing ("Nothing changed", "already set to", "Test failed") are answers.
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
    if out.startswith(("Unknown or incomplete command", "Incorrect argument for command")) or "<--[HERE]" in out:
        return True
    if any(a in out for a in ANSWERS):
        return False
    return out.startswith(ERROR_STARTS) or any(p in out for p in ERROR_PARTS)


def silent(cmd):
    """What the summary says for a command Minecraft answered with nothing: a failing `execute
    as|at ... run ...`, or one aimed at nobody online, says nothing either."""
    if cmd.split()[0].lower() == "execute":
        return "`%s` gave no reply, so it may not have happened" % cmd
    return "`%s` gave no reply" % cmd


_conn = []


def send(cmd):
    """Send one command once: (Minecraft's reply, or None when RCON failed; whether it may have
    reached Minecraft). On a connection of its own, since mc.rcon would send a command again
    on a new one, and a give or a summon must never happen twice. The RCON lock is held from
    send to reply, as by every client."""
    if not _conn:
        try:
            _conn.append(mc._connect())
        except Exception as e:
            mc.log("rcon error:", type(e).__name__, e)
            return None, False
    try:
        return mc._exchange(_conn[0], cmd), True
    except Exception as e:
        mc.log("rcon error:", type(e).__name__, e)
        del _conn[:]
        return None, True


def run(given):
    """The commands in order, until one is refused or fails: (summary, facts, ok)."""
    if not isinstance(given, list) or not given:
        return "no commands given", {"replies": [], "failed": None, "ran": [], "partial": False}, False
    if len(given) > MOST:
        return ("at most %d commands at a time, not %d" % (MOST, len(given)),
                {"replies": [], "failed": None, "ran": [], "partial": False}, False)
    clean = []
    for c in given:
        c = str(c).strip()
        if c.startswith("/"):
            c = c[1:].lstrip()
        why = "empty command" if not c else commands.command_problem(c)
        if why:  # refused before anything is sent
            return "refused: %s: %s" % (why, c[:200]), {"replies": [], "failed": c, "ran": [], "partial": False}, False
        clean.append(c)
    replies, said, ran, failed, partial = [], [], [], None, False
    for c in clean:
        out, sent = send(c)
        if out is None:
            failed, partial = c, sent
            said.append("RCON dropped while '%s' was sent: it may or may not have happened" % c if sent
                        else "Minecraft is not answering RCON: '%s' was not sent" % c)
            break
        if out.strip():
            replies.append(out)
            said.append(" ".join(out.split()))
        else:
            replies.append(NO_REPLY)
            said.append(silent(c))
        if mc_error(out):
            failed = c
            break
        ran.append(c)
    return "; ".join(said), {"replies": replies, "failed": failed, "ran": ran, "partial": partial}, failed is None


v = values()
summary, facts, ok = run(v["commands"])
result(summary, **facts)
if not ok:
    sys.exit(1)
