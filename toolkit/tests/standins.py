"""The actions for the vfg tests: the brain's menu, the game engine's ops (actions/engine_*.py), as
the repo has them, over its own actions/_types.py.

    standins.install(repo)            # actions/__init__.py, _types.py and every engine action
    standins.install(repo, ["engine_grant"])
    standins.install_types(repo)      # what an action needs to load, and no action

There is no script and no copy any more (the game engine spec, part 2): every action is one of
the engine's ops, which vfg sends to the engine.
"""
import os
import shutil

TESTS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(TESTS))
ACTIONS = sorted(n[:-3] for n in os.listdir(os.path.join(REPO, "actions"))
                 if n.startswith("engine_") and n.endswith(".py"))


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def install_types(repo):
    """What an action needs to load: actions/__init__.py and the real actions/_types.py."""
    write(os.path.join(repo, "actions", "__init__.py"), "")
    shutil.copy(os.path.join(REPO, "actions", "_types.py"), os.path.join(repo, "actions", "_types.py"))


def install(repo, names=None):
    """The real actions/_types.py and the repo's engine actions (those in `names`, else all)."""
    install_types(repo)
    for name in ACTIONS:
        if names is None or name in names:
            shutil.copy(os.path.join(REPO, "actions", name + ".py"), os.path.join(repo, "actions", name + ".py"))
