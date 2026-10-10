"""refs.py, with no network: the real 26.3 `commands.json` (from the server jar's data
generator) and minecraft.wiki's API answers for four command pages and the `tp` redirect, plus a
fake `vfg` and a fake HTTP transport. The wiki text is CC BY-NC-SA (minecraft.wiki); each answer
names its page in `query.pages[0].title`."""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

from brain import refs as R
from brain.refs import Refs, RefsError, load, tokenize, usage_lines, wiki_text

FIX = Path(__file__).parent / "fixtures" / "refs"
PAGES = ("give", "execute", "teleport", "effect")


def answer(name: str) -> dict:
    return json.loads((FIX / "wiki" / f"{name}.json").read_text())


def raw(name: str) -> str:
    return answer(name)["query"]["pages"][0]["extract"]


@pytest.fixture(scope="module")
def tree() -> dict:
    with gzip.open(FIX / "commands.json.gz") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def usage(tree) -> dict[str, list[str]]:
    return usage_lines(tree)


@pytest.fixture(scope="module")
def refs(tree) -> Refs:
    wiki, first = {}, {}
    for n in PAGES:
        wiki[n], first[n] = wiki_text(raw(n), n)
    return Refs("26.3", tree, wiki=wiki, first=first)


# ------------------------------------------------------------------ usage lines, used(), the wiki


def test_usage_lines_fold_the_tree_into_a_few_readable_lines(usage, tree):
    assert usage["give"] == ["give <targets> <item> [<count>]"]
    assert "effect give <targets> <effect> [infinite|<seconds>] [<amplifier>] [<hideParticles>]" in usage["effect"]
    assert usage["tp"] == [line.replace("teleport", "tp", 1) for line in usage["teleport"]]
    assert len(usage["data"]) == 7  # not the hundred-odd paths of the tree
    assert "execute (if|unless) entity <entities> [...]" in usage["execute"] and len(usage["execute"]) < 40
    assert set(usage) == set(tree["children"])
    assert all(sum(map(len, lines)) < 2000 for lines in usage.values())


def test_used_finds_every_command_and_execute_subcommand(refs):
    assert refs.used(["execute as @a run execute at @s run execute if entity @s[tag=vip] run say hi"]) == \
        ({"execute", "say"}, {"as", "at", "if", "if entity", "run"})
    assert refs.used(["/execute as @e[type=zombie, distance=..10] at @s positioned ~ ~2 ~ run tp @s ~ ~ ~"]) == \
        ({"execute", "tp"}, {"as", "at", "positioned", "run"})
    assert refs.used(['execute if data entity @s {Tags:["run fast"]} run kill @s']) == \
        ({"execute", "kill"}, {"if", "if data", "run"})
    assert refs.used(['tellraw @a {"text":"execute run give"}', "  /effect give @a speed 30 1"]) == \
        ({"tellraw", "effect"}, set())
    assert refs.used(["execute store result score @s n run time query daytime"]) == \
        ({"execute", "time"}, {"store", "store score", "run"})
    assert tokenize('give @e[type=zombie, distance=..10] "a b"') == ["give", "@e[type=zombie, distance=..10]", '"a b"']


def test_the_wiki_text_keeps_java_usage_and_drops_the_rest():
    text, first = wiki_text(raw("give"), "give")
    assert first == "Gives a specified amount of a certain item to one or more players."
    assert "<count>: integer" in text and "/give @p pig_spawn_egg" in text
    assert not any(gone in text for gone in ("History", "Bedrock", "player: target", "See also"))
    text, first = wiki_text(raw("tp"), "tp")
    assert first == "Teleports entities (players, mobs, etc.)." and "checkForBlocks" not in text


def test_a_reference_shows_only_what_was_used_and_the_menu_every_command(refs):
    give = refs.reference({"give"})
    assert give.startswith("## give\nSyntax on this server:\ngive <targets> <item> [<count>]\nWiki (Java Edition):\n")
    _, subs = refs.used(["execute as @a at @s run give @s minecraft:diamond 1"])
    ex = refs.reference({"execute"}, subs)
    assert "execute (as|at) <targets> ..." in ex and "execute store" not in ex and len(ex) < 4000
    assert "## teleport (also written tp)" in refs.reference({"tp", "teleport"})
    assert "Not a command on this server. Did you mean: give?" in refs.reference({"gvie"})
    lines = refs.menu(exclude={"fill"}).split("\n")
    assert "tp — the same as teleport." in lines and "say" in lines and "fill" not in lines


# ------------------------------------------------------------------ load()


@dataclass
class Result:
    code: int
    stdout: str
    stderr: str


class FakeVfg:
    def __init__(self, tree: dict | None, version: str = "26.3", *, version_fails: bool = False, refs_fails: bool = False):
        self.tree, self.version = tree, version
        self.version_fails, self.refs_fails = version_fails, refs_fails
        self.calls: list[tuple[tuple[str, ...], float]] = []

    async def __call__(self, *args: str, timeout: float = 30) -> Result:
        self.calls.append((args, timeout))
        if args == ("action", "refs", "--version"):
            if self.version_fails:
                return Result(255, "", "ssh: connect to host: Connection refused")
            return Result(0, self.version + "\n", "")
        if args == ("action", "refs"):
            if self.refs_fails or self.tree is None:
                return Result(124, "", "timed out after 300 s")
            return Result(0, json.dumps({"version": self.version, "commands": self.tree}) + "\n", "")
        raise AssertionError(f"unexpected vfg {args}")


class Wiki:
    """A fake minecraft.wiki API: answers from the fixtures, a missing page for the rest."""

    def __init__(self, *, broken_after: int | None = None, unavailable: frozenset[str] = frozenset()):
        self.broken_after, self.unavailable = broken_after, unavailable
        self.seen: list[str] = []
        self.agents: set[str] = set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.broken_after is not None and len(self.seen) >= self.broken_after:
            raise RuntimeError("the network went away")
        assert request.url.path == "/api.php" and request.url.params["prop"] == "extracts"
        title = request.url.params["titles"]
        self.seen.append(title)
        self.agents.add(request.headers.get("user-agent", ""))
        name = title.removeprefix("Commands/")
        if name in self.unavailable:
            return httpx.Response(503)
        if (FIX / "wiki" / f"{name}.json").is_file():
            return httpx.Response(200, json=answer(name))
        return httpx.Response(200, json={"batchcomplete": True, "query": {"pages": [{"ns": 0, "title": title, "missing": True}]}})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))


NAMES = ("give", "effect", "teleport", "tp", "say")


@pytest.fixture
def small(tree) -> dict:
    return {"type": "root", "children": {n: tree["children"][n] for n in NAMES}}


async def test_a_new_version_is_fetched_once_then_read_from_its_cache(tmp_path, small):
    vfg, wiki = FakeVfg(small), Wiki()
    async with wiki.client() as http:
        first = await load(tmp_path, vfg, http=http, log=lambda m: None, request_gap=0)
    assert first.version == "26.3" and vfg.calls == [(("action", "refs", "--version"), 30), (("action", "refs"), 300)]
    assert wiki.agents == {R.USER_AGENT} and len(wiki.seen) == 5
    d = tmp_path / "refs" / "26.3"
    assert (d / "wiki" / "give.txt").read_text().startswith("https://minecraft.wiki/w/Commands/give (CC BY-NC-SA")
    assert json.loads((d / "complete.json").read_text())["absent"] == ["say"]
    vfg = FakeVfg(None)  # would fail `vfg action refs`, and the wiki is never asked
    async with Wiki(broken_after=0).client() as http:
        again = await load(tmp_path, vfg, http=http, log=lambda m: None, request_gap=0)
    assert vfg.calls == [(("action", "refs", "--version"), 30)] and again.menu() == first.menu()


async def test_a_fetch_cut_short_is_finished_at_the_next_start(tmp_path, small):
    logs: list[str] = []
    async with Wiki(broken_after=2).client() as http:
        refs = await load(tmp_path, FakeVfg(small), http=http, log=logs.append, request_gap=0)
    assert not (tmp_path / "refs" / "26.3" / "complete.json").exists()
    assert "Wiki" not in refs.reference({"teleport"})  # its syntax alone, for now
    vfg, wiki = FakeVfg(small), Wiki()
    async with wiki.client() as http:
        done = await load(tmp_path, vfg, http=http, log=logs.append, request_gap=0)
    assert vfg.calls == [(("action", "refs", "--version"), 30)] and len(wiki.seen) == 3
    assert "Wiki (Java Edition)" in done.reference({"teleport"})


async def test_with_vfg_down_the_newest_cache_serves_and_with_none_it_raises(tmp_path, small):
    with pytest.raises(RefsError, match="nothing is cached"):
        await load(tmp_path, FakeVfg(None, version_fails=True), log=lambda m: None, request_gap=0)
    async with Wiki().client() as http:
        await load(tmp_path, FakeVfg(small), http=http, log=lambda m: None, request_gap=0)
    refs = await load(tmp_path, FakeVfg(None, version_fails=True), log=lambda m: None, request_gap=0)
    assert refs.version == "26.3" and "give" in refs.names()
