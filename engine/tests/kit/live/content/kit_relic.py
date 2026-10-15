"""An item with a long look (the kit's live test): a gold name, a glint and five lines of lore, so
six of them pass one command's 1446 bytes in a chest."""

from vfg_engine import Item, ItemConfig


class KitRelic(Item):
    class Config(ItemConfig):
        summary = "Does nothing; only looks long."
        base = "carrot_on_a_stick"
        name = "Relic of the Long Description"
        active_in = "mainhand"
        color = "gold"
        glint = True
        lore = ["A relic whose lore runs on and on, line %d of five." % i for i in range(1, 6)]
