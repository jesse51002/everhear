from actions._types import Action, Players, preset


class Herobrine(Action, preset("director")):
    """Herobrine haunts us: he joins the game, opens doors behind us, puts out torches, leaves signs and whispers our names."""

    script = "herobrine"
    runs = "until_stopped"
    takes = ["player", "director_min_s", "director_max_s", "director_events", "director_messages"]
    defaults = {"director_weights": [3, 1, 1, 1, 1, 1, 1, 1, 1], "director_min_s": 60, "director_max_s": 150,
                "director_messages": ["i see you", "turn around", "you are not alone", "behind you"],
                "director_events": ["sound", "door", "fake_join", "message", "shadow", "torches", "dark", "sign",
                                    "lightning"]}
    descriptions = {"director_events": "the pranks he picks from: sound (behind them), door (opens and shuts), "
                                       "fake_join (Herobrine joins the game), message (he whispers in chat), shadow "
                                       "(him, watching), torches (go out), dark, sign (\"behind you\"), lightning (far "
                                       "behind)"}

    player: Players = "@a"
    """who he haunts: a name, several separated by commas, or @a"""
