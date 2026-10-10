from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from actions._types import Action, ScriptName


class RunScript(Action):
    """Start one of the scripts in scripts/, by name, with its arguments.

    Each script says what it does and lists every value it takes, with its type, default
    and limits or options. Give only the values the instruction asks for, as key=value,
    the key named exactly as the script lists it; every other value keeps its default.
    A script written the old way lists a usage line instead: give its words in that order.
    A script that runs until stopped keeps going after this action reports; stop_script
    ends it.
    """

    action: Literal["run_script"]
    name: ScriptName = Field(description="the script's folder name")
    args: list[str] = Field(
        default_factory=list, max_length=10,
        description="key=value pairs, each key named exactly as the script lists its values, only "
                    "for the values the instruction asks for; for a script with a usage line instead, "
                    "its words in that order")

    @field_validator("args")
    @classmethod
    def _args(cls, v: list[str]) -> list[str]:
        out = []
        for a in v:
            a = a.strip()
            if not a or len(a) > 100 or any(ord(c) < 32 or ord(c) == 127 for c in a):
                raise ValueError("each argument is one short key=value pair, on one line")
            out.append(a)
        return out

    def vfg_argv(self, task: str) -> list[str]:
        # --task shows in vfg script list and vfg state; `--` keeps a word such as --wait or
        # -5 away from vfg's own options.
        sep = ["--"] if any(a.startswith("-") for a in self.args) else []
        return ["script", "run", "--task", task, self.name, *sep, *self.args]
