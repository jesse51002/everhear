from actions._types import Action, Any, CopyName


class Edit(Action):
    """Change values on a running copy of an action, by its name: bigger, faster, no cooldown, for another player too.

    Only the values given change; every other keeps its setting. A live value
    lands within half a second while the copy runs; a change to any other
    restarts the copy with the rest kept. An edit changes values only: what no
    value of the copy's action covers is new code.
    """

    script = "edit"
    runs = "once"
    takes = ["copy", "values"]

    copy: CopyName = ""
    """the running copy's name, as the list of running copies gives it, such as galick_gun_2"""
    values: dict[str, Any] = {}
    """the values to change, each by its name in the copy's own action, with its new setting"""
