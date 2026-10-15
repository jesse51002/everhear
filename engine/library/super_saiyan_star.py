"""Super Saiyan star: right-click to power up (or down) into Super Saiyan form (the super_saiyan
ability, its own file). It looks like a nether star, though underneath it is a carrot on a stick
(one of the only two bases that report a right-click, WRITING.md): an item's look can now give it
any model (made/main's work, DESIGN2 "Also needed"). (super_saiyan/main.py's item.)"""

from vfg_engine import Item, ItemConfig, Use


class SuperSaiyanStar(Item):
    class Config(ItemConfig):
        summary = "Right-click to power up (or down) into Super Saiyan form (the super_saiyan ability)."
        base = "carrot_on_a_stick"
        model = "minecraft:nether_star"
        name = "Super Saiyan"
        active_in = "mainhand"
        color = "blue"
        glint = True
        unbreakable = True

    def start(self, ctx):
        self.listen(Use, "on_use")

    def on_use(self, ctx, ev):
        ctx.user.cast("super_saiyan")
