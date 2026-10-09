from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from actions._types import Action, ScriptName


class RunScript(Action):
    """Start one of the scripts in scripts/, by name, with its arguments.

    Each script's README says what it does, its usage and how it ends; give the
    arguments its usage line asks for, in that order. A script that runs until
    stopped keeps going after this action reports; stop_script ends it.
    """

    action: Literal["run_script"]
    name: ScriptName = Field(description="the script's folder name")
    args: list[str] = Field(
        default_factory=list, max_length=10,
        description="its arguments, in the order its usage line gives them, such as a player's name or a number")

    @field_validator("args")
    @classmethod
    def _args(cls, v: list[str]) -> list[str]:
        out = []
        for a in v:
            a = a.strip()
            if not a or len(a) > 100 or any(ord(c) < 32 or ord(c) == 127 for c in a):
                raise ValueError("each argument is one short word or number")
            out.append(a)
        return out

    def vfg_argv(self, task: str) -> list[str]:
        # `--` keeps an argument such as --wait or -5 away from vfg's own options.
        sep = ["--"] if any(a.startswith("-") for a in self.args) else []
        return ["script", "run", self.name, *sep, *self.args]
