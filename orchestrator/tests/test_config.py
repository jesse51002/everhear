import os

from helpers import HERE, ORCH

from orchestrator.config import load_config


def test_the_repo_defaults_to_the_one_this_orchestrator_is_in():
    repo = os.path.realpath(os.path.join(ORCH, ".."))
    assert load_config({}).repo == repo
    assert load_config({"VFG_REPO": ""}).repo == repo


def test_vfg_repo_wins():
    d = os.path.realpath(HERE)
    assert load_config({"VFG_REPO": d}).repo == d
