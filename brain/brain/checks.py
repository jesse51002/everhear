"""The checks a hand-off and a follow-up must pass, whoever writes them: the brain's step
(call.step_type) and the router's answer (router.route_type), so a brief the router writes is held
to the same bar as one the brain writes, in the same words (Prototype_Conversation_And_Router).

Pure functions on what the model wrote, duck-typed on its fields: a hand-off has `name`,
`description`, `words`, `picture`, `assumed`, `cast` and `done_when` (call.HandOff), a follow-up
`task`, `brief` and `cancel` (call.FollowUp). Nothing here imports call.py, which imports this.

Each check raises ValueError with the words the model is sent back, the first that fails: a
pydantic validator that calls one turns it into the error structured() returns to the model, and
the brain's prompt and tests read those words, so they stay exactly as call.step_type wrote them.
What the checks leave alone is code's to carry out: a name already taken gets _2 when its task
opens (names.unique), and a follow-up's description may be empty (the task keeps its own).
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

from .names import NAME, RESERVED

# The brief's parts a hand-off must write, besides its checklist (call.HandOff.brief()).
PARTS = ("description", "words", "picture", "assumed", "cast")


def hand_offs(hand_offs: Sequence, catalog) -> None:
    """Each hand-off's name is a task's name (names.NAME), not `cast` (RESERVED), and not an
    action's name (the engine's ops); every part of its brief is written and its checklist has an
    item; no two share a name. `catalog` is the actions now (actions.Catalog: get()). A name an
    engine class has is made free when its task opens (names.unique), never sent back."""
    names = [h.name.strip() for h in hand_offs]
    for h in hand_offs:
        name = h.name.strip()
        if not NAME.match(name):
            raise ValueError(f"hand-off name {name!r}: lowercase letters, digits and underscores, starting "
                             "with a letter, 2 to 32 characters")
        if name in RESERVED:
            raise ValueError(f"{name} is never a task's name: name the thing, then whose")
        if catalog.get(name) is not None:
            raise ValueError(f"{name} is an action's name: add whose it is, as frost_bow_alex")
        missing = [k for k in PARTS if not getattr(h, k).strip()]
        if missing:
            raise ValueError(f"the hand-off {name}'s brief has no {', '.join(missing)}: write every part")
        if not any(x.strip() for x in h.done_when):
            raise ValueError(f"the hand-off {name} has no done_when: the checklist the implementer checks")
    if len(set(names)) < len(names):
        raise ValueError("two hand-offs share a name: one name a thing")


def follow_ups(follow_ups: Sequence, tasks: Collection[str] | None = None) -> None:
    """One follow-up a task, each naming one, each with a brief unless it cancels (a cancel sends
    the cancel words, and needs none). With `tasks`, the names under TASKS now, the task must be
    one of them: the brain's last step, which no next step could fix after a refusal, and every
    router answer, whose follow-up is carried out with no step after it. Without it a name not
    under TASKS is left to acts.py, whose refusal the brain's next step reads."""
    followed = [f.task.strip() for f in follow_ups]
    if len(set(followed)) < len(followed):
        raise ValueError("a task is followed up twice: one follow-up a task, saying everything")
    for f in follow_ups:
        if not f.task.strip():
            raise ValueError("a follow-up names no task: its name as TASKS shows it")
        if not f.cancel and not f.brief.strip():
            raise ValueError(f"the follow-up of {f.task} has no brief: what its implementer changes")
        if tasks is not None and f.task.strip() not in tasks:
            raise ValueError(f"task {f.task.strip()!r} is not under TASKS: follow up one that is, or hand "
                             "it off as new")
