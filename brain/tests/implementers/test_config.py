"""ImplementerConfig.load: the environment, else ~/.vfg/config as vfg reads it, else the defaults."""
import os

from brain.implementers.config import ROOT, ImplementerConfig


def test_the_defaults_then_vfgs_config_file_then_the_environment(tmp_path):
    home = tmp_path / "vfg-home"
    c = ImplementerConfig.load({"VFG_HOME": str(home)})
    assert c.repo == os.path.realpath(ROOT) and c.prompt == os.path.join(ROOT, "brain", "prompts", "implementer.md")
    assert (c.home, c.brain_dir) == (str(home), str(home / "brain"))
    assert [c.model, c.effort, c.max_turns, c.time_limit_s, c.server, c.route_timeout_s, c.cache_ttl] == [
        "claude-haiku-5-5", "low", 40, 600.0, "main", 3.0, "5m"]
    home.mkdir()
    (home / "config").write_text("# comment\nVFG_SERVER=lobby\nexport VFG_ROUTE_TIMEOUT_S='2.5'\n"
                                 "VFG_SUBAGENT_CACHE_TTL=\"1h\"\n")
    c = ImplementerConfig.load({"VFG_HOME": str(home), "VFG_ROUTE_TIMEOUT_S": "4", "VFG_IMPL_MODEL": "claude-x",
                                "VFG_IMPL_MAX_TURNS": "12", "VFG_IMPLEMENTER_MINUTES": "3",
                                "VFG_BRAIN_DIR": str(tmp_path / "b"), "VFG_REPO": str(tmp_path)})
    assert [c.server, c.route_timeout_s, c.cache_ttl, c.model, c.max_turns, c.time_limit_s, c.brain_dir, c.repo] == [
        "lobby", 4.0, "1h", "claude-x", 12, 180.0, str(tmp_path / "b"), os.path.realpath(tmp_path)]
    bad = ImplementerConfig.load({"VFG_HOME": str(tmp_path / "none"), "VFG_SERVER": "../x",
                                  "VFG_ROUTE_TIMEOUT_S": "soon", "VFG_IMPL_CACHE_TTL": "forever",
                                  "VFG_IMPL_MAX_TURNS": "-1"})
    assert [bad.server, bad.route_timeout_s, bad.cache_ttl, bad.max_turns] == ["main", 3.0, "5m", 40]
