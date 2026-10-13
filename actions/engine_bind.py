from actions._types import Action, Args, ClassId, Field, Player


class EngineBind(Action):
    """Bind a word or a phrase for one player, so saying it casts one of their engine abilities, or starts a scenario with set args, at once and with no AI: an incantation.

    For one player, never everyone. To cast an ability, give ability: the player must
    have it already (a grant that landed in an earlier step, or one ENGINE shows). To start
    a scenario, give scenario and its args instead; an arg set to caster is filled with
    whoever casts it (a Player arg, or a Place arg: where they stand). The phrase is the
    player's own words for it. Its aliases, how the phrase is likely said or misheard, are
    made as it is bound, and the notes warn when it is short, everyday talk, or sounds like
    another of that player's phrases. Binding a phrase the player has already replaces it.
    """

    engine = "bind"
    takes = ["player", "phrase", "ability", "scenario", "args", "cooldown_s"]
    timeout_s = 40   # the alias call takes about 7 s and up to 20

    player: Player = ""
    """whose incantation it is: one player's exact name, the player who asked"""
    phrase: str = Field("", max_length=80)
    """what they say to cast it, a word or a phrase, as they say it: fireball, fire ball, kamehameha"""
    ability: ClassId = ""
    """the ability it casts, by its name under ENGINE, such as fireball; empty when it starts a scenario"""
    scenario: ClassId = ""
    """the scenario it starts, by its name under ENGINE, such as meteor; empty when it casts an ability"""
    args: Args = {}
    """the scenario's args, set once for every cast, each by its name; caster for whoever casts it"""
    cooldown_s: float = Field(3, ge=0, le=600)
    """a scenario's incantation: seconds before it can be cast again; an ability's is its own Config's"""
