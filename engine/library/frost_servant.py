"""Frost Servant: a named stray the Ice Queen calls to her side. The frost_servants ability spawns
it, never more than its cap alive; nothing of its own fires it stays until killed, or removed with
her (frost_servants' summon_near: no keep=True)."""

from vfg_engine import Actor, ActorConfig


class FrostServant(Actor):
    class Config(ActorConfig):
        summary = "A named stray the Ice Queen calls to fight beside her."
        entity_type = "minecraft:stray"
        # NEEDS: the old summon() left health and weapon at the mob's own (summon_health 0, no summon_weapon);
        # ActorConfig requires both, so these are vanilla stray's own numbers, copied rather than read live.
        health = 20
        size = 1
        damage = 2
        name = "Frost Servant"
        name_visible = True
