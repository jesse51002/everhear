"""ImplementerConfig.load: the environment, else ~/.vfg/config as vfg reads it, else the defaults;
the provider, its model, address and key; and the price table."""
import os
from datetime import datetime, timezone

from brain.implementers.config import ROOT, ImplementerConfig, Price, price_at, provider_routing


def test_the_defaults_then_vfgs_config_file_then_the_environment(tmp_path):
    home = tmp_path / "vfg-home"
    c = ImplementerConfig.load({"VFG_HOME": str(home)})
    assert c.repo == os.path.realpath(ROOT) and c.prompt == os.path.join(ROOT, "brain", "prompts", "implementer.md")
    assert (c.home, c.brain_dir) == (str(home), str(home / "brain"))
    assert [c.harness, c.provider, c.model, c.base_url, c.key, c.effort, c.max_turns, c.time_limit_s, c.server,
            c.route_timeout_s, c.cache_ttl] == [
        "claude-code", "openrouter", "deepseek/deepseek-v4.1-flash", "https://openrouter.ai/api", "", "low", 100, 600.0,
        "main", 3.0, "5m"]
    home.mkdir()
    (home / "config").write_text("# comment\nVFG_SERVER=lobby\nexport VFG_ROUTE_TIMEOUT_S='2.5'\n"
                                 "VFG_SUBAGENT_CACHE_TTL=\"1h\"\nVFG_IMPL_PROVIDER=anthropic\n")
    c = ImplementerConfig.load({"VFG_HOME": str(home), "VFG_ROUTE_TIMEOUT_S": "4", "VFG_IMPL_MODEL": "claude-x",
                                "VFG_IMPL_MAX_TURNS": "12", "VFG_IMPLEMENTER_MINUTES": "3",
                                "VFG_BRAIN_DIR": str(tmp_path / "b"), "VFG_REPO": str(tmp_path)})
    assert [c.server, c.route_timeout_s, c.cache_ttl, c.provider, c.model, c.base_url, c.max_turns, c.time_limit_s,
            c.brain_dir, c.repo] == [
        "lobby", 4.0, "1h", "anthropic", "claude-x", "", 12, 180.0, str(tmp_path / "b"), os.path.realpath(tmp_path)]
    bad = ImplementerConfig.load({"VFG_HOME": str(tmp_path / "none"), "VFG_SERVER": "../x",
                                  "VFG_ROUTE_TIMEOUT_S": "soon", "VFG_IMPL_CACHE_TTL": "forever",
                                  "VFG_IMPL_MAX_TURNS": "-1", "VFG_IMPL_PROVIDER": "openai"})
    assert [bad.server, bad.route_timeout_s, bad.cache_ttl, bad.max_turns, bad.provider] == [
        "main", 3.0, "5m", 100, "openrouter"]


def test_the_repo_and_the_brains_folder_from_vfgs_config_file_as_the_brain_reads_them(tmp_path):
    # the brain (brain/config.py) reads both from ~/.vfg/config too: one set there must reach its
    # implementers, or they work in another repo and keep their files in another folder
    home = tmp_path / "vfg-home"
    home.mkdir()
    (home / "config").write_text("VFG_REPO=%s\nVFG_BRAIN_DIR=%s\n" % (tmp_path, tmp_path / "b"))
    c = ImplementerConfig.load({"VFG_HOME": str(home)})
    assert (c.repo, c.brain_dir) == (os.path.realpath(tmp_path), str(tmp_path / "b"))
    c = ImplementerConfig.load({"VFG_HOME": str(home), "VFG_BRAIN_DIR": str(tmp_path / "env")})  # the environment first
    assert c.brain_dir == str(tmp_path / "env")


def test_effort_turns_and_minutes_are_read_from_vfgs_config_file_too(tmp_path):
    home = tmp_path / "vfg-home"
    home.mkdir()
    (home / "config").write_text("VFG_IMPL_EFFORT=medium\nVFG_IMPL_MAX_TURNS=25\nVFG_IMPLEMENTER_MINUTES=4\n")
    c = ImplementerConfig.load({"VFG_HOME": str(home)})
    assert (c.effort, c.max_turns, c.time_limit_s) == ("medium", 25, 240.0)
    c = ImplementerConfig.load({"VFG_HOME": str(home), "VFG_IMPL_MAX_TURNS": "30"})  # the environment first
    assert c.max_turns == 30


def test_the_provider_its_model_its_address_and_its_key(tmp_path):
    home = tmp_path / "vfg-home"
    # DeepSeek's own API when its key is there, its model and its Claude-compatible address
    c = ImplementerConfig.load({"VFG_HOME": str(home), "DEEPSEEK_API_KEY": " sk-ds \n", "OPEN_ROUTER_API": "sk-or"})
    assert (c.provider, c.model, c.base_url, c.key) == (
        "deepseek", "deepseek-flash", "https://api.deepseek.com/anthropic", "sk-ds")
    # the key is never in the settings' repr
    assert "sk-ds" not in repr(c) and "key=" not in repr(c)
    # OpenRouter's key: OPEN_ROUTER_API, else the file VFG_OPENROUTER_KEY names, else ~/.vfg/openrouter.key
    c = ImplementerConfig.load({"VFG_HOME": str(home), "OPEN_ROUTER_API": "sk-or", "VFG_IMPL_BASE_URL": "http://x/"})
    assert (c.provider, c.key, c.base_url) == ("openrouter", "sk-or", "http://x")
    home.mkdir()
    (home / "openrouter.key").write_text("sk-file\n")
    assert ImplementerConfig.load({"VFG_HOME": str(home)}).key == "sk-file"
    (tmp_path / "elsewhere.key").write_text("sk-named")
    (home / "config").write_text("VFG_OPENROUTER_KEY=%s\n" % (tmp_path / "elsewhere.key"))
    assert ImplementerConfig.load({"VFG_HOME": str(home)}).key == "sk-named"
    # asked for by name; VFG_IMPLEMENTER_MODEL is the model's old name
    c = ImplementerConfig.load({"VFG_HOME": str(home), "VFG_IMPL_PROVIDER": "deepseek", "VFG_IMPLEMENTER_MODEL": "m"})
    assert (c.provider, c.model, c.key) == ("deepseek", "m", "")
    c = ImplementerConfig.load({"VFG_HOME": str(home), "VFG_IMPL_PROVIDER": "anthropic", "OPEN_ROUTER_API": "sk-or"})
    assert (c.provider, c.model, c.base_url, c.key) == ("anthropic", "claude-haiku-5-5", "", "")


def at(y, mo, d, h, mi=0) -> float:
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp()


def test_deepseeks_peak_hours_by_utc_weekday_and_hour():
    peak, off = Price(0.30, 0.006, 0.30, 1.20), Price(0.15, 0.003, 0.15, 0.60)
    # 2026-10-12 is a Monday
    for h, want in [(0, off), (1, peak), (3, peak), (4, off), (5, off), (6, peak), (9, peak), (10, off), (23, off)]:
        assert price_at("deepseek-flash", 1000, at(2026, 10, 12, h, 59 if want is peak else 0)) == want, h
    assert price_at("deepseek-flash", 1000, at(2026, 10, 12, 0, 59)) == off  # 00:59, before the peak
    assert price_at("deepseek-flash", 1000, at(2026, 10, 16, 8)) == peak  # Friday
    assert price_at("deepseek-flash", 1000, at(2026, 10, 17, 8)) == off  # Saturday
    assert price_at("deepseek-flash", 1000, at(2026, 10, 18, 2)) == off  # Sunday
    assert price_at("deepseek-flash[1m]", 900_000, at(2026, 10, 12, 2)) == peak  # one price at any length
    # OpenRouter charges its provider's price at every hour: the peak price, always
    for when in (at(2026, 10, 12, 2), at(2026, 10, 12, 12), at(2026, 10, 17, 8)):
        assert price_at("deepseek/deepseek-v4.1-flash", 1000, when) == peak


def test_haikus_step_at_100k_tokens_and_an_unknown_model():
    base = Price(0.10, 0.01, 0.125, 0.50)
    assert price_at("claude-haiku-5-5", 100_000, at(2026, 10, 12, 2)) == base
    assert price_at("claude-haiku-5-5", 100_001, at(2026, 10, 12, 12)) == Price(0.50, 0.05, 0.625, 2.50)
    assert price_at("gpt-x", 10, 0) is None


def test_openrouters_provider_routing_for_the_cache(tmp_path):
    c = ImplementerConfig.load({"VFG_HOME": str(tmp_path), "OPEN_ROUTER_API": "k"})
    assert provider_routing(c) == {"allow_fallbacks": True, "order": ["digitalocean", "ionstream", "together"],
                                   "ignore": ["venice", "morph", "relace"]}
    c = ImplementerConfig.load({"VFG_HOME": str(tmp_path), "OPEN_ROUTER_API": "k",
                                "VFG_IMPL_OR_ORDER": " Together , deepinfra", "VFG_IMPL_OR_IGNORE": "none"})
    assert provider_routing(c) == {"allow_fallbacks": True, "order": ["together", "deepinfra"]}
    assert provider_routing(ImplementerConfig.load({"VFG_HOME": str(tmp_path), "DEEPSEEK_API_KEY": "k"})) is None


def test_the_test_server_setting(tmp_path):
    assert ImplementerConfig.load({"VFG_HOME": str(tmp_path)}).test_box == ""
    assert ImplementerConfig.load({"VFG_HOME": str(tmp_path), "VFG_TEST_BOX": "~/.vfg/test-box"}).test_box == "~/.vfg/test-box"
