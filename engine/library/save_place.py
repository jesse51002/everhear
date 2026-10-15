"""Save place: save where a player stands, or given coordinates, under a name everyone can use.
Give either the player or the coordinates, never both. Saving a name again moves it. Every later
scenario, and the brain, sees it by name (`ctx.places`), so teleport can go there. The one way the
brain names a place in the engine's own places store (DESIGN2 §2): the old save_place action/script
wrote the same old `everhear:places` list that teleport and the kit's presets once read from
Minecraft's command storage; this writes the engine's own, through `self.save_place`."""

import math

from vfg_engine import Args, Field, Place, Player, Scenario, ScenarioConfig, Spot


class SavePlace(Scenario):
    class Config(ScenarioConfig):
        summary = "Saves where a player stands, or given coordinates, under a name everyone can use."
        duration_s = 1

    class Args(Args):
        name: str = Field("", pattern=r"^[a-z0-9_]{0,32}$",
                          description="the place's name, short, lowercase, with _ for spaces: castle, home, "
                                     "zoo_entrance")
        player: Player = Field("", description="save where this player stands, by their exact name; empty when "
                                              "the coordinates are given")
        at: Place = Field("", description="or save these coordinates instead, x y z (the_nether or the_end "
                                         "after them); empty when the player is given")

    def start(self, ctx):
        a = self.args
        if not a.name:
            self.result("which name? a place is saved under a short name: castle, home, zoo_entrance", name="", failed=True)
            self.end()
            return
        if bool(a.player) == (a.at is not None):
            self.result("give the player or the coordinates, not both" if a.player else
                       "give the player or the coordinates", name=a.name, failed=True)
            self.end()
            return
        if a.player:
            p = ctx.world.player(a.player)      # already known online: the Player arg checked it at start
            spot = Spot(dim=p.dim, pos=tuple(p.pos), yaw=p.yaw)
        else:
            spot = a.at
        try:
            key = self.save_place(a.name, spot)
        except ValueError as exc:
            self.result(str(exc), name=a.name, failed=True)
            self.end()
            return
        doc = ctx.places.get(key)
        x, y, z = (int(math.floor(c)) for c in doc["pos"])
        dim = doc["dim"][len("minecraft:"):] if doc["dim"].startswith("minecraft:") else doc["dim"]
        self.result("saved %s at %d %d %d in %s" % (key, x, y, z, dim),
                   name=key, pos=[x, y, z], dim=doc["dim"], by=doc.get("by"))
        self.end()
