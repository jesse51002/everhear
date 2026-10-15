"""Args: what a Scenario's start, or a Mark's application, takes (DESIGN2 §1).

    class Meteor(Scenario):
        class Args(Args):
            at: Place = Field("", description="where it falls: a saved place, x y z, or a player")
            count: int = Field(5, ge=1, le=50, description="how many")

`start` and `apply` validate what they are given against the class's Args and refuse in words;
`self.args` is the model, its Place args already resolved to a Spot (dim, pos, yaw). Every field
needs a default and a description (a field's docstring counts), so the brain can show and fill it.

The arg types, each checked at start against the 26.3 registries, the online players and the
engine's places:

    Player       one online player's name ("" for none)
    Players      "@a" (everyone), "@r" (one at random) or names "Steve,Alex"; resolved fresh each time
                 with players(ctx, self.args.players), so it follows joins
    Place        a saved place's name, "x y z [dim]", or an online player's name -> a Spot
    EntityType   "zombie" -> "minecraft:zombie"
    BlockId      "stone", "oak_stairs[facing=east]"
    BlockMix     "70% stone 30% andesite": read with mix(text)
    ItemId       "diamond_sword"
    ReplaceRules "snow > air; solid > 70% lava 30% keep": read with rules(text)
    Command      one raw command (a leading / dropped); never one of the server owner's
    Seconds      a number of seconds, 0 or more
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationInfo

from . import registries

__all__ = ["Args", "Spot", "Player", "Players", "Place", "EntityType", "BlockId", "BlockMix", "ItemId",
           "ReplaceRules", "Command", "Seconds", "players", "mix", "rules", "pick", "schema", "SOLID"]

PLAYER_RE = re.compile(r"^[A-Za-z0-9_]{1,16}$")
PLACE_RE = re.compile(r"^[a-z0-9_]{1,32}$")
_NUM = r"-?\d+(?:\.\d+)?"
_COORDS = re.compile(r"^(%s)[ ,]+(%s)[ ,]+(%s)(?:[ ,]+(?:minecraft:)?([a-z_]+))?$" % (_NUM, _NUM, _NUM))
DIMS = {"overworld": "minecraft:overworld", "nether": "minecraft:the_nether",
        "the_nether": "minecraft:the_nether", "end": "minecraft:the_end", "the_end": "minecraft:the_end"}
HEIGHTS = {"minecraft:overworld": (-64, 319), "minecraft:the_nether": (0, 255), "minecraft:the_end": (0, 255)}
BORDER = 30_000_000
CASTER = "caster"          # in a binding's fixed args: whoever spoke the phrase

# "solid" in a replace rule: every block a player cannot walk through, as five 26.3 tags that hold
# no bedrock, barrier or command block (the old kit's replace_blocks).
SOLID = ("#minecraft:mineable/pickaxe", "#minecraft:mineable/axe", "#minecraft:mineable/shovel",
         "#minecraft:mineable/hoe", "#minecraft:wool")
_MIX_WORD = re.compile(r"(\d+(?:\.\d+)?)%|([^\s,%\[{;]+(?:\[[^\]]*\])?(?:\{[^}]*\})?)")


class Kind:
    """What an arg is, for the schema the brain reads ("place", "players", ...)."""

    def __init__(self, name: str):
        self.name = name

    def __repr__(self):
        return "Kind(%r)" % self.name


class Spot(BaseModel):
    """A place resolved: where, in which dimension, facing which way."""
    model_config = ConfigDict(frozen=True)
    dim: str = "minecraft:overworld"
    pos: tuple[float, float, float]
    yaw: float = 0.0
    name: str = ""             # what it was given as: a saved place's name, a player's, or ""

    @property
    def x(self):
        return self.pos[0]

    @property
    def y(self):
        return self.pos[1]

    @property
    def z(self):
        return self.pos[2]


# ---- the validators -----------------------------------------------------------------------

def _rt(info: ValidationInfo):
    return (info.context or {}).get("rt") if info is not None else None


def _player(v: Any, info: ValidationInfo) -> Any:
    if not isinstance(v, str):
        return v
    s = v.strip()
    if not s:
        return ""
    if not PLAYER_RE.match(s):
        raise ValueError("%r is not a player's name" % s)
    rt = _rt(info)
    if rt is not None:
        p = next((p for name, p in rt.snapshot.players.items() if name.lower() == s.lower()), None)
        if p is None:
            raise ValueError("%s is not online" % s)
        return p.name
    return s


def _players(v: Any) -> Any:
    if isinstance(v, (list, tuple)) and all(isinstance(x, str) for x in v):
        v = ",".join(v)
    if not isinstance(v, str):
        return v
    s = v.strip() or "@a"
    if s in ("@a", "@r"):
        return s
    names = [n.strip() for n in s.split(",") if n.strip()]
    for n in names:
        if not PLAYER_RE.match(n):
            raise ValueError('%r is not "@a", "@r" or players\' names separated by commas' % s)
    return ",".join(names)


def _id(kind: str, known: frozenset, states: bool = False):
    def check(v: Any) -> Any:
        if not isinstance(v, str):
            return v
        s = v.strip()
        if not s:
            return ""
        cut = min([i for i in (s.find("["), s.find("{")) if i >= 0] or [len(s)]) if states else len(s)
        head, rest = s[:cut].lower(), s[cut:]
        name = head if ":" in head else "minecraft:" + head
        if name not in known:
            raise ValueError("%s is not %s %s in Minecraft 26.3" % (head, "an" if kind[0] in "aeiou" else "a", kind))
        return name + rest
    return check


def _block(text: str, *, keep: bool = False, tags: bool = False, solid: bool = False) -> str:
    """One block of a mix or rule, namespaced, its [states] and {nbt} kept; ValueError if 26.3 lacks it."""
    if keep and text == "keep":
        return text
    if solid and text == "solid":
        return text
    if text.startswith("#"):
        if not tags:
            raise ValueError("%s: a #tag is for what a rule changes (before >), not what it builds" % text)
        name = text[1:] if ":" in text else "minecraft:" + text[1:]
        if name not in registries.BLOCK_TAGS:
            raise ValueError("%s is not a block tag in Minecraft 26.3" % text)
        return "#" + name
    return _id("block", registries.BLOCKS, states=True)(text)


def mix(text: str, keep: bool = False) -> list[tuple[str, float]]:
    """[(block, weight)] from "70% stone, 30% andesite" (a block with no % weighs 1); "keep" is
    allowed where `keep` (a rule's right side: the block as it was). ValueError in words."""
    s = str(text or "").strip()
    if not s or _MIX_WORD.sub(" ", s).replace(",", " ").strip():
        raise ValueError('%r is not a block or a mix like "70%% stone 30%% andesite"' % s)
    out, weight = [], None
    for w, block in _MIX_WORD.findall(s):
        if w:
            weight = float(w)
            continue
        out.append((_block(block, keep=keep), 1.0 if weight is None else weight))
        weight = None
    if not out or not sum(w for _, w in out):
        raise ValueError("%r: its weights add up to 0%%" % s)
    return out


def pick(choices: list[tuple[str, float]], seed: str) -> str:
    """One block of a mix, the same for the same seed (a cell's place) every time."""
    r = random.Random(seed)
    return r.choices([c[0] for c in choices], [c[1] for c in choices])[0]


@dataclass(frozen=True)
class Rule:
    froms: tuple[str, ...]           # block ids, #tags ("solid" expanded to SOLID)
    to: tuple[tuple[str, float], ...]   # a mix; "keep" leaves the block as it was
    text: str


def rules(text) -> list[Rule]:
    """The rules of "snow > air; solid > 70% lava 30% keep", in order. ValueError in words."""
    if isinstance(text, (list, tuple)):
        text = "; ".join(str(t) for t in text)
    out = []
    for part in [p.strip() for p in str(text or "").split(";") if p.strip()]:
        left, arrow, right = part.partition(">")
        if not arrow:
            raise ValueError('%r is not a rule: "from ... > to", such as "stone dirt > 70%% lava 30%% keep"' % part)
        froms = []
        if _MIX_WORD.sub(" ", left).replace(",", " ").strip():
            raise ValueError("%r: before > are block ids, #tags or solid" % part)
        for w, block in _MIX_WORD.findall(left):
            if w:
                raise ValueError("%r: a weight goes after >, on what blocks become" % part)
            b = _block(block, tags=True, solid=True)
            froms.extend(SOLID if b == "solid" else (b,))
        if not froms:
            raise ValueError("%r: name the blocks to change before >" % part)
        out.append(Rule(tuple(froms), tuple(mix(right, keep=True)), part))
    if not out:
        raise ValueError('no rules: "from ... > to; from ... > to"')
    return out


def _mix_text(v: Any) -> Any:
    if isinstance(v, str):
        mix(v)
        return " ".join(v.split())
    return v


def _rules_text(v: Any) -> Any:
    if isinstance(v, (list, tuple)):
        v = "; ".join(str(t) for t in v)
    if isinstance(v, str):
        rules(v)
        return "; ".join(p.strip() for p in v.split(";") if p.strip())
    return v


def _command(v: Any) -> Any:
    if not isinstance(v, str):
        return v
    s = v.strip()
    if s.startswith("/"):
        s = s[1:].lstrip()
    if not s:
        return ""
    from .mc.commands import refused_word      # the one list of the owner's commands
    word = refused_word(s)
    if word:
        raise ValueError("%s is the server owner's to do, never the engine's: %s" % (word, s[:80]))
    return s


def _place(v: Any, info: ValidationInfo) -> Any:
    if v is None or isinstance(v, Spot):
        return v
    if isinstance(v, dict):
        return Spot(**v)
    if isinstance(v, (list, tuple)) and len(v) == 3:
        return Spot(pos=tuple(float(c) for c in v))
    if not isinstance(v, str):
        return v
    s = v.strip()
    if not s:
        return None
    m = _COORDS.match(s.lower())
    if m:
        x, y, z = (float(g) for g in m.groups()[:3])
        dim = DIMS.get(m.group(4) or "overworld")
        if dim is None:
            raise ValueError("%r: the dimension after x y z is overworld, the_nether or the_end" % s)
        lo, hi = HEIGHTS[dim]
        if abs(x) >= BORDER or abs(z) >= BORDER or not lo <= y <= hi:
            raise ValueError("%s is outside the world (y from %d to %d there)" % (s, lo, hi))
        return Spot(dim=dim, pos=(x, y, z))
    rt = _rt(info)
    if rt is None:
        raise ValueError("%r: a place's name or a player's is read when the instance starts" % s)
    return resolve_place(rt, s)


def resolve_place(rt, s: str) -> Spot:
    """A saved place by name, else an online player where they stand; ValueError in words."""
    name = place_name(s)
    saved = rt.places.get(name) if name else None
    if saved is not None:
        return Spot(dim=saved["dim"], pos=tuple(saved["pos"]), yaw=float(saved.get("yaw", 0.0)), name=name)
    p = next((p for n, p in rt.snapshot.players.items() if n.lower() == s.lower()), None)
    if p is not None:
        return Spot(dim=p.dim, pos=tuple(p.pos), yaw=float(p.yaw), name=p.name)
    known = sorted(rt.places.all())
    raise ValueError("%r is not a saved place (%s), x y z [dim], or an online player's name"
                     % (s, "saved: " + ", ".join(known) if known else "none is saved"))


def place_name(s: str) -> str:
    """'My Castle' -> 'my_castle'; '' when it cannot be a place's name."""
    name = re.sub(r"[\s-]+", "_", str(s or "").strip().lower())
    return name if PLACE_RE.match(name) else ""


Player = Annotated[str, Kind("player"), BeforeValidator(_player)]
Players = Annotated[str, Kind("players"), BeforeValidator(_players)]
Place = Annotated[Spot | None, Kind("place"), BeforeValidator(_place)]
EntityType = Annotated[str, Kind("entity type"), BeforeValidator(_id("entity type", registries.ENTITY_TYPES))]
BlockId = Annotated[str, Kind("block"), BeforeValidator(_id("block", registries.BLOCKS, states=True))]
ItemId = Annotated[str, Kind("item"), BeforeValidator(_id("item", registries.ITEMS))]
BlockMix = Annotated[str, Kind("block mix"), BeforeValidator(_mix_text)]
ReplaceRules = Annotated[str, Kind("replace rules"), BeforeValidator(_rules_text)]
Command = Annotated[str, Kind("command"), BeforeValidator(_command)]
Seconds = Annotated[float, Kind("seconds"), Field(ge=0)]


class Args(BaseModel):
    """What a start (a Scenario) or an application (a Mark) takes. Every field has a default and a
    description; an arg not declared is refused."""
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True,
                              use_attribute_docstrings=True)


def validate(cls, given: dict | None, rt=None) -> Args:
    """`given` checked against `cls` (an Args class); ValueError listing what is wrong, in words."""
    from pydantic import ValidationError
    try:
        return cls.model_validate(dict(given or {}), context={"rt": rt} if rt is not None else None)
    except ValidationError as exc:
        parts = []
        for e in exc.errors():
            where = ".".join(str(p) for p in e.get("loc", ())) or "args"
            msg = e.get("msg", "")
            if e.get("type") == "extra_forbidden":
                msg = "not an arg of this (it takes: %s)" % (", ".join(cls.model_fields) or "none")
            parts.append("%s: %s" % (where, msg.removeprefix("Value error, ")))
        raise ValueError("; ".join(parts)) from None


def players(ctx, arg: str = "@a") -> list[str]:
    """The online players a Players arg means now: "@a" everyone, "@r" one at random, else the
    named ones who are online (in their own spelling). It follows joins and leaves."""
    online = [p.name for p in ctx.world.players.values() if p.alive]
    s = (arg or "@a").strip()
    if s == "@a":
        return online
    if s == "@r":
        return [random.choice(online)] if online else []
    want = {n.strip().lower() for n in s.split(",") if n.strip()}
    return [n for n in online if n.lower() in want]


# ---- the schema the brain and `vfg engine` read --------------------------------------------

def _kind_of_type(ann, metadata=()) -> str:
    """The word for a type: an arg type's own (Kind), "one of ...", a list of something, or plain."""
    import typing
    for m in metadata:
        if isinstance(m, Kind):
            return m.name
    origin = typing.get_origin(ann)
    if origin is typing.Annotated:
        base, *meta = typing.get_args(ann)
        return _kind_of_type(base, meta)
    if origin is typing.Literal:
        return "one of " + ", ".join(repr(a) for a in typing.get_args(ann))
    if origin in (list, tuple, set, frozenset):
        inner = [a for a in typing.get_args(ann) if a is not Ellipsis]
        return "list of " + (_kind_of_type(inner[0]) if inner else "anything")
    if origin is dict:
        k, v = (typing.get_args(ann) + (str, str))[:2]
        return "dict of %s to %s" % (_kind_of_type(k), _kind_of_type(v))
    if origin in (typing.Union, getattr(__import__("types"), "UnionType", None)):
        parts = [a for a in typing.get_args(ann) if a is not type(None)]
        word = " or ".join(_kind_of_type(a) for a in parts)
        return word + (" or none" if len(parts) < len(typing.get_args(ann)) else "")
    names = {bool: "true or false", int: "whole number", float: "number", str: "text"}
    return names.get(ann, getattr(ann, "__name__", str(ann)))


def _kind(info) -> str:
    return _kind_of_type(info.annotation, info.metadata)


def _bounds(info) -> dict:
    """ge/gt/le/lt for a number; min_length/max_length for text or a list; pattern for text."""
    out = {}
    for m in info.metadata:
        for key in ("ge", "gt", "le", "lt", "min_length", "max_length", "pattern"):
            if getattr(m, key, None) is not None:
                out[key] = getattr(m, key)
    return out


def schema(cls) -> list[dict]:
    """Each arg as {name, type, default, bounds, description}."""
    out = []
    for name, info in cls.model_fields.items():
        default = info.get_default(call_default_factory=True)
        if isinstance(default, Spot):
            default = default.model_dump(mode="json")
        out.append({"name": name, "type": _kind(info), "default": default, "bounds": _bounds(info),
                    "description": info.description or ""})
    return out


def kind_of_field(cls, name: str) -> str | None:
    info = cls.model_fields.get(name)
    return _kind(info) if info is not None else None
