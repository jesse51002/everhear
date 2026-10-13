"""migrate_map: the old system's actions onto the engine's classes, for `vfg engine migrate`.

The old incantation store (/opt/vfg/incantations.json) bound each phrase to an action, with the
action's values. The engine binds a phrase to an ability (cast) or to a scenario with its args set
once (the game engine spec, part 2). MAP says, for every action a binding could name (the old
library's actions and made/main's), what its binding becomes. One row an action:

    {"ability": "<id>"}
        the binding casts that ability. An ability takes no args: its numbers are its Config, one
        version for everyone, so the binding's own values stay behind, said in a note.
    {"scenario": "<id>", "args": {...}, "values": {...}}
        the binding starts that scenario, its args set once: "args" the fixed ones (a merged
        script's recipe: {"mobs": "zoo"}), then each old value the binding gave, by "values":
            "old_field": "new_arg"                     renamed, the value as it was
            "old_field": ["new_arg", {old: new, ...}]   renamed, the value translated (a value the
                                                       table lacks is refused)
            "old_field": None                          dropped on purpose: it means nothing now
        An old value with no entry stays behind, said in a note. A player field left as the
        caster's (the binding's caster_fields) becomes "caster", which the engine fills with
        whoever says it.
    {"refuse": "<why, in words>"}
        it cannot be moved: the words say why and what to do instead.

and "note": "<words>" on any row, said with every binding it moves. An action with no row is
left behind, never guessed.

    translate(action, entry) -> (kind, id, args, notes)   or raises Refused(words)

Python 3.9+, standard library only, like toolkit/vfg.
"""


class Refused(Exception):
    """An old binding that cannot be moved; the message says why, in words."""


MAP = {
    # ---- made/main (inventory G4): KrownJM's three are bound today.
    "blizzard_freeze": {"ability": "blizzard"},
    "fire_aura_krown": {"ability": "flame"},
    "fireball_cast": {"ability": "fireball"},
}


def _words(v):
    """A value as a note shows it."""
    import json
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def translate(action, entry):
    """One old binding's action and values as (kind, id, args, notes): kind "ability" or
    "scenario". Raises Refused with why when it cannot be moved, or when the action has no row."""
    row = MAP.get(action)
    if row is None:
        raise Refused("%s has no engine class in the migration table" % action)
    if row.get("refuse"):
        raise Refused(row["refuse"])
    asked = entry.get("asked") if isinstance(entry.get("asked"), dict) else entry.get("values")
    asked = dict(asked) if isinstance(asked, dict) else {}
    casters = [str(f) for f in entry.get("caster_fields") or []]
    notes = [row["note"]] if row.get("note") else []
    if row.get("ability"):
        given = dict((k, v) for k, v in asked.items() if k not in casters)
        if given:
            notes.append("its values %s stay behind: %s's Config is one version for everyone"
                         % (_words(given), row["ability"]))
        return "ability", row["ability"], {}, notes
    scenario = row.get("scenario")
    if not scenario:
        raise Refused("%s's row in the migration table names no ability, scenario or refusal" % action)
    args = dict(row.get("args") or {})
    rules = row.get("values") or {}
    left = {}
    for field, value in asked.items():
        if field in casters:
            continue
        if field not in rules:
            left[field] = value
            continue
        rule = rules[field]
        if rule is None:
            continue
        if isinstance(rule, (list, tuple)):
            arg, table = rule[0], rule[1]
            key = value if isinstance(value, (str, int, float, bool)) else _words(value)
            if key not in table:
                raise Refused("%s=%s has no %s %s in the engine: bind it again by hand"
                              % (field, _words(value), scenario, arg))
            args[arg] = table[key]
        else:
            args[rule] = value
    for field in casters:
        rule = rules.get(field)
        arg = rule[0] if isinstance(rule, (list, tuple)) else rule
        if arg:
            args[arg] = "caster"
        else:
            notes.append("its caster's %s has no arg in %s: whoever says it is not filled in" % (field, scenario))
    if left:
        notes.append("its values %s stay behind: %s has no arg for them" % (_words(left), scenario))
    return "scenario", scenario, args, notes
