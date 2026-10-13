"""God: the giant, glowing mannequin god_visits sends to stand in front of whoever it visits. Which
of the three gods (its name, skin and the colour of its name) and which way it faces change every
visit, so god_visits writes those, not held in this Config, straight into the extra SNBT it spawns
this with."""

from vfg_engine import Actor, ActorConfig


class God(Actor):
    class Config(ActorConfig):
        summary = "A giant, glowing god that stands facing whoever it visits, and whispers to them alone."
        entity_type = "minecraft:mannequin"
        health = 100.0
        size = 3.0                # a giant, as the old script's attribute did
        damage = 0.0               # it never attacks
        glowing = True
        name_visible = True
        # NEEDS: a mannequin's profile (its skin) and a coloured, bold CustomName vary with which god
        # visits; ActorConfig has no profile field and its own name is plain text (config.py's
        # CustomName is json.dumps(str), no colour or bold), so god_visits writes both, and the
        # Rotation that faces the player, into the nbt it spawns this with (merged onto this).
        nbt = "{Invulnerable:1b,NoGravity:1b,immovable:1b}"
