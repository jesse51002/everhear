# state.py: the box's side of vfg state: who is online and where, the time and the weather, read
# over RCON and nothing written. Appended after common.py and ARGS:
#   {"vfg": "/opt/vfg", "mc": "/opt/mc", "items": false}
# With items, each online player also gets "items": the replies to data get Inventory, equipment and
# SelectedItemSlot; and its state, read from Health, foodLevel, XpLevel, playerGameType and
# active_effects and the armor and max_health attributes: "health", "max_health", "food",
# "xp_level", "armor", "game_mode" and "effects" ([{"id", "amplifier", "duration"}], the duration in
# ticks, -1 for infinite), each null when Minecraft's reply could not be read (never a guess).
# It prints one JSON object, {"online", "daytime", "day", "raining", "thundering", "rcon", "ms"};
# Minecraft not answering RCON is exit 5, after the object (its "rcon" says why).
import re

PLAYER_RE = re.compile(r"^[A-Za-z0-9_]{1,16}$")

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

# And its state. Player data on 26.3 (toolkit/tests/test_vfg.py's MC_26_3 has the replies, as
# recorded): Health a float (20.0f), foodLevel, XpLevel and playerGameType ints,
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
    world["ms"] = int((time.time() - t0) * 1000)
    print(json.dumps(world, ensure_ascii=False))
    if world["rcon"]:
        die(5, world["rcon"])


op_state()
