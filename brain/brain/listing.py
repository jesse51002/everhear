"""A player's items as the brain can use them. Code, no model.

The replies to `data get entity <p> Inventory`, `... equipment` and `... SelectedItemSlot`
become one line naming each item by the slot `item modify` takes, the held one marked:

    Steve's items, by slot: hotbar.0 stick; hotbar.4 iron_sword (held); inventory.5
    diamond_sword {sharpness 5, mending 1}; armor.feet diamond_boots

Raw, Slot 14b is inventory.5 and the hand is a number in another reply, and the model
misread both on 2026-10-10: it enchanted the wrong sword, and said a player holding one had
none. Anything that is not such a listing is left as it is.
"""

from __future__ import annotations

import re

DATA = re.compile(r"^(?P<who>\S+) has the following entity data: (?P<data>.*)$", re.S)
# The equipment compound's keys, as the slots `item modify` takes.
WORN = {"head": "armor.head", "chest": "armor.chest", "legs": "armor.legs", "feet": "armor.feet",
        "offhand": "weapon.offhand", "body": "armor.body"}


def top_level(text: str, sep: str = ",") -> list[str]:
    """`text` split at every `sep` outside braces, brackets and quotes."""
    out, depth, quote, start = [], 0, "", 0
    for i, ch in enumerate(text):
        if quote:
            if ch == quote and text[i - 1] != "\\":
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
        elif ch == sep and depth == 0:
            out.append(text[start:i])
            start = i + 1
    out.append(text[start:])
    return [x.strip() for x in out if x.strip()]


def fields(item: str) -> dict[str, str]:
    """`{count: 1, Slot: 4b, id: "minecraft:x"}` as its top-level keys and raw values."""
    inner = item.strip()
    if inner.startswith("{") and inner.endswith("}"):
        inner = inner[1:-1]
    out = {}
    for part in top_level(inner):
        key, _, value = part.partition(":")
        out[key.strip().strip("\"'")] = value.strip()
    return out


def describe(item: dict[str, str]) -> str:
    """`diamond_sword {sharpness 5, named "Excalibur", damaged 200}`: the id and what sets it apart."""
    name = item.get("id", "?").strip("\"'").removeprefix("minecraft:")
    extra = []
    components = item.get("components", "")
    ench = re.search(r'"minecraft:enchantments":\s*\{([^{}]*)\}', components)
    if ench:
        extra += [f"{n} {lvl}" for n, lvl in re.findall(r'"minecraft:([a-z0-9_]+)":\s*(\d+)', ench.group(1))]
    named = re.search(r'"minecraft:custom_name":\s*"((?:[^"\\]|\\.)*)"', components)
    if named:
        extra.append(f'named "{named.group(1)}"')
    damage = re.search(r'"minecraft:damage":\s*(\d+)', components)
    if damage:
        extra.append(f"damaged {damage.group(1)}")
    count = item.get("count", "1").rstrip("b")
    head = f"{count} {name}" if count not in ("", "1") else name
    return head + (" {" + ", ".join(extra) + "}" if extra else "")


def readable(replies: list[str]) -> str | None:
    """The listing line, when `replies` hold an Inventory list; else None."""
    who, held, carried, worn = "", None, None, []
    for reply in replies:
        m = DATA.match(str(reply).strip())
        if not m:
            continue
        who, data = m.group("who"), m.group("data").strip()
        if data.startswith("["):
            carried = [fields(x) for x in top_level(data[1:-1])]
        elif data.startswith("{"):
            body = fields(data)
            if "id" in body:
                continue  # one item, such as SelectedItem: not part of a listing
            worn = [(WORN.get(k, k), fields(v)) for k, v in body.items() if v.startswith("{")]
        elif re.fullmatch(r"-?\d+", data):
            held = int(data)
    if carried is None:
        return None
    slots = []
    for item in carried:
        n = int(re.sub(r"\D", "", item.get("Slot", "-1")) or -1)
        if 0 <= n <= 8:
            slot = f"hotbar.{n}"
        elif 9 <= n <= 35:
            slot = f"inventory.{n - 9}"
        else:
            continue
        slots.append(f"{slot} {describe(item)}" + (" (held)" if n == held else ""))
    slots += [f"{slot} {describe(item)} (worn)" for slot, item in worn]
    if not slots:
        return f"{who} carries and wears nothing"
    return f"{who}'s items, by slot: " + "; ".join(slots)
