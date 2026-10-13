"""Incantations: per-player phrases bound to abilities (the game engine spec, "Incantations").

An incantation triggers an ability; it does not grant one. Bindings are per player, never for
everyone (`@a` is refused). The engine owns the store, kept in its saved state, and writes the
export the bridge reads (VFG_ENGINE_EXPORT, `{"version": n, "incantations": [...]}`) atomically
whenever the list changes, the version going up by one each time.

Phrases and aliases are normalised exactly as the kit's scripts/_lib/incantations.py does;
`normalize` is a copy, held to the kit's answers by tests/core/test_incantations.py.
"""

from __future__ import annotations

import json
import re
import unicodedata

from .statefile import write_json

MAX_PHRASE = 80           # characters, normalised (the kit's)
MAX_ALIASES = 24          # the kit's limit on aliases per entry
PLAYER_RE = re.compile(r"^[A-Za-z0-9_]{1,16}$")
EVERYONE = "@a"


def normalize(text):
    """The drop rule's normalisation: lowercase, accents and apostrophes dropped, every other
    mark (hyphens and underscores too) a space, whitespace collapsed. (A copy of the kit's.)"""
    s = unicodedata.normalize("NFKD", str(text or "")).lower()
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"['‘’]", "", s)
    return " ".join(re.sub(r"[\W_]+", " ", s).split())


def check_phrase(phrase) -> str:
    p = normalize(phrase)
    if not p:
        raise ValueError("%r is not an incantation: a phrase is a word at least" % (phrase,))
    if len(p) > MAX_PHRASE:
        raise ValueError("%r is too long for an incantation: %d characters at most" % (phrase, MAX_PHRASE))
    return p


def check_player(player) -> str:
    p = str(player or "").strip()
    if p.lower() == EVERYONE or p.startswith("@"):
        raise ValueError("incantations are per player: bind each player by name, not %r" % p)
    if not PLAYER_RE.match(p):
        raise ValueError("%r is not a player name (1 to 16 of A-Z, a-z, 0-9 and _)" % p)
    return p


def entry_id(player: str, phrase: str) -> str:
    """steve.fire_ball, as the kit names entries."""
    return "%s.%s" % (str(player).lower(), normalize(phrase).replace(" ", "_"))


class Bindings:
    def __init__(self, export_path: str | None = None):
        self.export_path = export_path
        self.entries: list[dict] = []
        self.version = 0
        self._exported: list | None = None

    # ---- change --------------------------------------------------------------------------

    def bind(self, player, phrase, ability: str, aliases=(), everyday_aliases=(), by=None,
             now: float = 0.0) -> dict:
        player = check_player(player)
        p = check_phrase(phrase)
        own = []
        for a in aliases or ():
            n = normalize(a)
            if n and n != p and n not in own:
                own.append(n)
        if len(own) > MAX_ALIASES:
            raise ValueError("%d aliases; an incantation has %d at most" % (len(own), MAX_ALIASES))
        everyday = []
        for a in everyday_aliases or ():
            n = normalize(a)
            if n and n not in own:
                raise ValueError("everyday alias %r is not one of the aliases" % a)
            if n and n not in everyday:
                everyday.append(n)
        entry = {"id": entry_id(player, p), "player": player, "phrase": p, "aliases": own,
                 "everyday_aliases": everyday, "ability": ability, "by": by, "created": now}
        self.entries = [e for e in self.entries if e["id"] != entry["id"]]
        self.entries.append(entry)
        return entry

    def unbind(self, player, phrase) -> dict:
        key, p = str(player or "").lower(), normalize(phrase)
        for e in self.entries:
            if e["player"].lower() == key and e["phrase"] == p:
                self.entries.remove(e)
                return e
        raise KeyError("%s has no incantation %r" % (player, p))

    # ---- read ----------------------------------------------------------------------------

    def for_player(self, player) -> list[dict]:
        key = str(player or "").lower()
        return [e for e in self.entries if e["player"].lower() == key]

    def lookup(self, player, heard) -> dict | None:
        """The player's entry whose phrase or an alias, normalised, is what was heard."""
        h = normalize(heard)
        if not h:
            return None
        for e in self.for_player(player):
            if h == e["phrase"] or h in e["aliases"]:
                return e
        return None

    def listing(self, cooldown_of, player=None) -> list[dict]:
        """The entries as the `incantations` op and the export give them: the ability's
        cooldown_s from its Config now (None while the ability is not loaded)."""
        rows = self.for_player(player) if player else list(self.entries)
        return [{"id": e["id"], "player": e["player"], "phrase": e["phrase"], "aliases": list(e["aliases"]),
                 "everyday_aliases": list(e["everyday_aliases"]), "ability": e["ability"],
                 "cooldown_s": cooldown_of(e["ability"])} for e in rows]

    def export(self, cooldown_of) -> bool:
        """Write the export if the list changed since it was last written; True when it did. The
        first time after a start, "last written" is the export file on disk: an unchanged list
        keeps its version, so a restart alone does not make the bridge and the mods fetch again."""
        rows = self.listing(cooldown_of)
        if self._exported is None:
            version, written = self._on_disk()
            if written == rows and version is not None:
                self.version = max(self.version, version)
                self._exported = rows
                return False
            if version is not None:
                self.version = max(self.version, version)
        if rows == self._exported:
            return False
        self.version += 1
        if self.export_path:
            write_json(self.export_path, {"version": self.version, "incantations": rows})
        self._exported = rows
        return True

    def _on_disk(self):
        """(version, rows) of the export file as written, or (None, None)."""
        if not self.export_path:
            return None, None
        try:
            with open(self.export_path, encoding="utf-8") as f:
                doc = json.load(f)
            version = doc.get("version")
            if isinstance(version, int) and not isinstance(version, bool):
                return version, doc.get("incantations")
        except (OSError, ValueError, AttributeError):
            pass
        return None, None

    # ---- saving --------------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {"version": self.version, "entries": self.entries}

    def load(self, doc):
        doc = doc or {}
        self.version = int(doc.get("version", 0))
        self.entries = list(doc.get("entries") or [])
        self._exported = None
