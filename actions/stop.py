from actions._types import Action, CopyName


class Stop(Action):
    """Stop a running copy of an action, by its name: a weapon, a rain, a rule, anything that keeps going.

    The copy cleans up as it stops: everything it made that carries its tag
    goes too. What it made to stay, such as summoned mobs or a build, stays.
    """

    script = "stop"
    runs = "once"
    takes = ["copy"]

    copy: CopyName = ""
    """the running copy's name, as the list of running copies gives it, such as galick_gun_2"""
