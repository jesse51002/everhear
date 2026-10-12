from actions._types import Action, Any, Field


class IncantationCreate(Action):
    """Bind a word or a phrase to an action for a player, so saying it fires the action at once, with no AI: a spell, an incantation.

    "Make fire ball shoot a fireball": the speaker's own, unless someone asks for
    everyone. The action and its values are checked as a run of it would be, and a
    player field left empty becomes whoever casts it. Its aliases, how the phrase
    is likely said or misheard, are made as it is created. Creating a phrase the
    player has already replaces it. The notes warn when the phrase is short, when
    it is everyday talk, which casts whenever it is said, or when it sounds like,
    or starts like, another of that player's phrases.
    """

    script = "incantations"
    runs = "once"
    takes = ["player", "phrase", "action", "values", "cooldown_s"]

    # A plain text with a pattern, as save_place's player: Players needs a default it takes,
    # and an incantation has none. vfg refuses it empty.
    player: str = Field("", pattern=r"^(|@a|[A-Za-z0-9_]{1,16}( *, *[A-Za-z0-9_]{1,16})*)$")
    """whose incantation it is: the speaker's exact name, the player who asked for it; several separated by commas; @a only when someone explicitly asks for everyone"""
    phrase: str = Field("", max_length=80)
    """what they say to cast it, a word or a phrase, as they say it: fireball, heal, fire ball, kamehameha"""
    action: str = Field("", pattern=r"^(|[a-z][a-z0-9_]{0,47})$")
    """the action it fires, by its name on the menu, such as command or galick_gun"""
    values: dict[str, Any] = {}
    """that action's values for this phrase, each by its name in that action; leave a player field out, or empty, so it is whoever casts it"""
    cooldown_s: float = Field(3, ge=0, le=600)
    """seconds before it can be cast again"""
