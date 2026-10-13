from typing import Annotated, ClassVar

from actions._types import Action, Field, Kind


class EngineConfig(Action):
    """Change an engine class's numbers for everyone: its whole Config class rewritten in Python, as read, with only what was asked changed.

    Read the class's Config first (read_configs), then send the whole class back in
    config, every line and comment kept, only the values asked for changed. The engine
    puts it in exactly the place the old one was, checks it (it parses, every field is
    kept, only values changed: every formula's code as it was, each value a plain number,
    text or Field(...) of them; nothing outside the Config changed, every number in its
    bounds, its damage at three powers) and reloads it for every player at once. If a check fails, nothing
    changes: the old version keeps running, and the error says which check and why.
    """

    engine: ClassVar[str] = "config"   # the engine's control op its values go to: no script
    runs = "once"
    takes = ["class_id", "config"]
    timeout_s = 60   # a deploy and a reload, and when the engine refuses, the old file deployed again

    class_id: str = Field("", pattern=r"^(|[a-z][a-z0-9_]{0,63})$")
    """the class whose Config it is, by its name under ENGINE, such as fireball"""
    config: Annotated[str, Kind("python")] = ""
    """the whole new Config class, from its class Config line to its last line, as Python with its comments"""
