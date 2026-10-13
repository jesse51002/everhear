"""Command: run up to five vanilla server commands in order, stopping at the first one Minecraft
refuses; Minecraft's own replies are the result. Never places or removes blocks: setblock, fill,
clone, place and fillbiome are refused here, even after `execute ... run` (building is a
scenario's job, through the block queue), same as the owner's commands, which the engine's own
Command arg already refuses. Port of the old command action/script: the error-reply table below is
copied verbatim from it (Minecraft 26.3's own failure wording), and the five-command cap, so the
player-facing words and the tuning stay the same. The old script's own RCON connection (kept open
across a batch so a give or a summon could never run twice) is gone: `ctx.query` is the engine's
one writer, so that is no longer a risk content has to manage."""

from vfg_engine import Args, Field, Scenario, ScenarioConfig
from vfg_engine import Command as CommandArg

MOST = 5
NO_REPLY = "(no reply)"
# Block-editing commands this scenario itself refuses (never Minecraft's to send back an error
# for): checked on the command's first word, or the word right after any `run` (so `execute ...
# run fill ...` is caught too), same as the engine's own owner-command check works.
BLOCKED = frozenset({"setblock", "fill", "clone", "place", "fillbiome"})

# The start of every reply Minecraft 26.3 sends instead of doing what was asked: the failure
# messages of its own language file. A parse error also carries <--[HERE]. Replies that only say
# nothing needed doing ("Nothing changed", "already set to", "Test failed") are answers, not errors.
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


def _blocked_word(cmd: str) -> str | None:
    """The blocked building word in `cmd` (its first word, or the word right after any `run`), or
    None."""
    words = cmd.split()
    if not words:
        return None
    checks = [words[0]]
    for i, w in enumerate(words[:-1]):
        if w.lower() == "run":
            checks.append(words[i + 1])
    for w in checks:
        w = w.lstrip("/").lower()
        if w in BLOCKED:
            return w
    return None


def _mc_error(out: str) -> bool:
    """True when `out` is Minecraft refusing or failing a command."""
    if out.startswith(("Unknown or incomplete command", "Incorrect argument for command")) or "<--[HERE]" in out:
        return True
    if any(a in out for a in ANSWERS):
        return False
    return out.startswith(ERROR_STARTS) or any(p in out for p in ERROR_PARTS)


def _silent(cmd: str) -> str:
    """What the summary says for a command Minecraft answered with nothing: a failing `execute
    as|at ... run ...`, or one aimed at nobody online, says nothing either."""
    if cmd.split()[0].lower() == "execute":
        return "`%s` gave no reply, so it may not have happened" % cmd
    return "`%s` gave no reply" % cmd


class Command(Scenario):
    class Config(ScenarioConfig):
        summary = "Runs up to five vanilla commands in order; Minecraft's own replies are the result."
        duration_s = 5

    class Args(Args):
        commands: list[CommandArg] = Field(
            [], max_length=MOST,
            description="the server commands to run, in the order they run, without a leading slash, "
                       "for example 'give Steve minecraft:golden_apple 64'; at most five")

    def start(self, ctx):
        given = self.args.commands
        if not given:
            self.result("no commands given", replies=[], failed=None, ran=[], partial=False)
            self.end()
            return
        said, replies, ran, failed, partial = [], [], [], None, False
        for cmd in given:
            word = _blocked_word(cmd)
            if word:
                said.append("refused: %s is a scenario's to do, not a command: %s" % (word, cmd[:200]))
                failed = cmd
                break
            try:
                out = ctx.query(cmd)
            except Exception as exc:
                # NEEDS: telling a command that reached Minecraft but got no reply apart from one
                # never sent at all (the old script's own RCON connection told those apart by
                # catching the drop before vs. after sending; ctx.query gives one exception either
                # way)
                said.append("Minecraft did not answer '%s': %s; it may or may not have happened" % (cmd, exc))
                failed, partial = cmd, True
                break
            if out.strip():
                replies.append(out)
                said.append(" ".join(out.split()))
            else:
                replies.append(NO_REPLY)
                said.append(_silent(cmd))
            if _mc_error(out):
                failed = cmd
                break
            ran.append(cmd)
        self.result("; ".join(said), replies=replies, failed=failed, ran=ran, partial=partial)
        self.end()
