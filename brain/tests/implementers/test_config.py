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


# Not in the TypeScript tests: the script library's settings, read as vfg reads them.
def test_the_library_settings_from_the_environment_else_vfgs_config_file():
    d = os.path.realpath(HERE)
    c = load_config({"VFG_REPO": d, "VFG_HOME": "/nonexistent"})
    assert [c.server, c.route_timeout_s, c.subagent_cache_ttl, c.implementer_model] == ["main", 3.0, "5m", "claude-haiku-5-5"]
    import tempfile
    home = tempfile.mkdtemp(prefix="vfg-config-")
    with open(os.path.join(home, "config"), "w") as f:
        f.write("# comment\nVFG_SERVER=lobby\nexport VFG_ROUTE_TIMEOUT_S='2.5'\nVFG_SUBAGENT_CACHE_TTL=\"1h\"\n")
    c = load_config({"VFG_REPO": d, "VFG_HOME": home})
    assert [c.server, c.route_timeout_s, c.subagent_cache_ttl] == ["lobby", 2.5, "1h"]
    c = load_config({"VFG_REPO": d, "VFG_HOME": home, "VFG_SERVER": "main", "VFG_ROUTE_TIMEOUT_S": "4"})
    assert [c.server, c.route_timeout_s, c.subagent_cache_ttl] == ["main", 4.0, "1h"]  # the environment wins
    bad = load_config({"VFG_REPO": d, "VFG_HOME": "/nonexistent", "VFG_SERVER": "../x", "VFG_ROUTE_TIMEOUT_S": "soon",
                       "VFG_SUBAGENT_CACHE_TTL": "forever"})
    assert [bad.server, bad.route_timeout_s, bad.subagent_cache_ttl] == ["main", 3.0, "5m"]
