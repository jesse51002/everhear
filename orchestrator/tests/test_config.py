import os

from helpers import HERE, ORCH

from orchestrator.config import ignored_mode, load_config


def test_the_repo_defaults_to_the_one_this_orchestrator_is_in():
    repo = os.path.realpath(os.path.join(ORCH, ".."))
    assert load_config({}).repo == repo
    assert load_config({"VFG_REPO": ""}).repo == repo


# Not in the TypeScript tests: implementers is the only mode; any other is named, to be warned about.
def test_a_mode_other_than_implementers_is_ignored():
    assert ignored_mode({}) is None
    assert ignored_mode({"VFG_ORCH_MODE": ""}) is None
    assert ignored_mode({"VFG_ORCH_MODE": "implementers"}) is None
    assert ignored_mode({"VFG_ORCH_MODE": "hybrid"}) == "hybrid"
    assert ignored_mode({"VFG_ORCH_MODE": "direct"}) == "direct"
    assert not hasattr(load_config({"VFG_ORCH_MODE": "direct"}), "mode")


def test_vfg_repo_wins():
    d = os.path.realpath(HERE)
    assert load_config({"VFG_REPO": d}).repo == d
