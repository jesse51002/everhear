from typing import ClassVar

from actions._types import Action, Field


class EngineBind(Action):
    """Bind a word or a phrase to one of a player's engine abilities, so saying it casts the ability at once, with no AI: an incantation.

    For one player, never everyone: the player must have the ability already (a grant
    that landed in an earlier step, or one ENGINE shows). The phrase is the player's own
    words for it. Its aliases, how the phrase is likely said or misheard, are made as it
    is bound, and the notes warn when it is short, everyday talk, or sounds like another
    of that player's phrases. Binding a phrase the player has already replaces it.
    """

    engine: ClassVar[str] = "bind"   # the engine's control op its values go to: no script
    runs = "once"
    takes = ["player", "phrase", "ability"]
    timeout_s = 40   # the alias call takes about 7 s and up to 20

    player: str = Field("", pattern=r"^(|[A-Za-z0-9_]{1,16})$")
    """whose incantation it is: one player's exact name, the player who asked"""
    phrase: str = Field("", max_length=80)
    """what they say to cast it, a word or a phrase, as they say it: fireball, fire ball, kamehameha"""
    ability: str = Field("", pattern=r"^(|[a-z][a-z0-9_]{0,63})$")
    """the ability it casts, by its name under ENGINE, such as fireball"""
