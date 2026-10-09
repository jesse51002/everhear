from __future__ import annotations

from typing import Literal

from pydantic import Field

from actions._types import Action, ScriptName


class StopScript(Action):
    """Stop a running script, by name.

    The script cleans up after itself as it stops: what it summoned goes too.
    """

    action: Literal["stop_script"]
    name: ScriptName = Field(description="the running script's folder name")

    def vfg_argv(self, task: str) -> list[str]:
        return ["script", "stop", self.name]
