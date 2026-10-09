"""refs.py, with no network: the real 26.3 `commands.json` (from the server jar's data
generator) and minecraft.wiki's API answers for five command pages and the `tp` redirect, plus a
fake `vfg` and a fake HTTP transport. The wiki text is CC BY-NC-SA (minecraft.wiki); each answer
names its page in `query.pages[0].title`."""

from __future__ import annotations

import gzip
import json
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

from dispatcher import refs as R
from dispatcher.refs import Refs, RefsError, load, tokenize, usage_lines, wiki_text

FIX = Path(__file__).parent / "fixtures" / "refs"
PAGES = ("give", "execute", "teleport", "effect", "spreadplayers")


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


# ------------------------------------------------------------------ usage lines


def test_usage_give_summon(usage):
    assert usage["give"] == ["give <targets> <item> [<count>]"]
    assert usage["summon"] == ["summon <entity> [<pos>] [<nbt>]"]


def test_usage_effect(usage):
    assert usage["effect"] == [
        "effect clear [<targets>] [<effect>]",
        "effect give <targets> <effect> [infinite|<seconds>] [<amplifier>] [<hideParticles>]",
    ]


def test_usage_teleport_and_its_alias(usage):
    assert usage["teleport"] == [
        "teleport <destination>",
        "teleport <location>",
        "teleport <targets> <destination>",
        "teleport <targets> <location> [facing entity <facingEntity>] [<facingAnchor>]",
        "teleport <targets> <location> [facing <facingLocation>]",
        "teleport <targets> <location> [<rotation>]",
    ]
    assert usage["tp"] == [line.replace("teleport", "tp", 1) for line in usage["teleport"]]


def test_usage_attribute(usage):
    assert usage["attribute"] == [
        "attribute <target> <attribute> base get [<scale>]",
        "attribute <target> <attribute> base reset",
        "attribute <target> <attribute> base set <value>",
        "attribute <target> <attribute> get [<scale>]",
        "attribute <target> <attribute> modifier add <id> <value> (add_multiplied_base|add_multiplied_total|add_value)",
        "attribute <target> <attribute> modifier remove <id>",
        "attribute <target> <attribute> modifier value get <id> [<scale>]",
    ]


def test_usage_data_folds_the_modify_tree(usage):
    lines = usage["data"]
    assert len(lines) == 7  # not the hundred-odd paths of the tree
    assert "data get (block|entity|storage) <target> [<path>] [<scale>]" in lines
    assert ("data modify (block|entity|storage) <target> <targetPath> (append|insert <index>|merge|prepend|set) "
            "value <value>") in lines  # fmt: skip
    assert ("data modify (block|entity|storage) <target> <targetPath> (append|insert <index>|merge|prepend|set) "
            "from (block|entity|storage) <source> [<sourcePath>]") in lines  # fmt: skip


def test_usage_execute_lists_each_subcommand_once(usage):
    lines = usage["execute"]
    for line in (
        "execute (as|at) <targets> ...",
        "execute run <command>",
        "execute positioned <pos> ...",
        "execute positioned as <targets> ...",
        "execute (if|unless) entity <entities> [...]",
        "execute (if|unless) block <pos> <block> [...]",
        "execute (if|unless) score <target> <targetObjective> matches <range> [...]",
        "execute (if|unless) data (block|entity|storage) <source> <path> [...]",
        "execute store (result|success) score <targets> <objective> ...",
        "execute on (attacker|controller|leasher|origin|owner|passengers|target|vehicle) ...",
        "execute summon <entity> ...",
    ):
        assert line in lines
    assert len(lines) < 40 and max(map(len, lines)) < 140


def test_usage_every_command(usage, tree):
    assert set(usage) == set(tree["children"])
    for name, lines in usage.items():
        assert lines and all(line == name or line.startswith(name + " ") for line in lines), name
        assert sum(map(len, lines)) < 2000, name
    assert not any("minecraft:" in line for line in usage["gamerule"])  # each rule once, not twice


# ------------------------------------------------------------------ used()


def test_used_execute_run(refs):
    assert refs.used(["execute as @a at @s run give @s minecraft:diamond 1"]) == ({"execute", "give"}, {"as", "at", "run"})


def test_used_nested_runs(refs):
    names, subs = refs.used(["execute as @a run execute at @s run execute if entity @s[tag=vip] run say hi"])
    assert names == {"execute", "say"}
    assert subs == {"as", "at", "if", "if entity", "run"}


def test_used_selectors_with_spaces_and_a_leading_slash(refs):
    cmd = "/execute as @e[type=zombie, distance=..10] at @s positioned ~ ~2 ~ run tp @s ~ ~ ~"
    assert refs.used([cmd]) == ({"execute", "tp"}, {"as", "at", "positioned", "run"})
    assert refs.used(["/give Steve diamond 64", "  /effect give @a speed 30 1"]) == ({"give", "effect"}, set())


def test_used_braces_and_quotes(refs):
    assert refs.used(['execute if data entity @s {Tags:["run fast"]} run kill @s']) == (
        {"execute", "kill"},
        {"if", "if data", "run"},
    )
    assert refs.used(['tellraw @a {"text":"execute run give"}']) == ({"tellraw"}, set())
    assert refs.used(["summon zombie ~ ~ ~ {CustomName:'\"run away\"'}"]) == ({"summon"}, set())


def test_used_store_and_a_draft_off_the_tree(refs):
    assert refs.used(["execute store result score @s n run time query daytime"]) == (
        {"execute", "time"},
        {"store", "store score", "run"},
    )
    # `@a` is not a subcommand: the plain scan still finds the command after `run`
    assert refs.used(["execute @a at @s run give @s dirt"]) == ({"execute", "give"}, {"at", "run"})


def test_tokenize():
    assert tokenize("give @e[type=zombie, distance=..10] stone") == ["give", "@e[type=zombie, distance=..10]", "stone"]
    assert tokenize('say "a b" it\'s') == ["say", '"a b"', "it's"]


# ------------------------------------------------------------------ the wiki


def test_wiki_give():
    text, first = wiki_text(raw("give"), "give")
    assert first == "Gives a specified amount of a certain item to one or more players."
    for kept in ("## Usage", "## Syntax", "give <targets> <item> [<count>]", "## Arguments", "<count>: integer",
                 "Specifies the number of items to give.", "## Examples", "/give @p pig_spawn_egg"):  # fmt: skip
        assert kept in text
    for gone in ("History", "Bedrock", "player: target", "amount: int", "item_lock", "keep_on_death", "BE:", "JE:",
                 "only]", "[Int]", "See also", "## Result"):  # fmt: skip
        assert gone not in text


def test_wiki_teleport_from_the_tp_redirect():
    assert answer("tp")["query"]["redirects"] == [{"from": "Commands/tp", "to": "Commands/teleport"}]
    text, first = wiki_text(raw("tp"), "tp")
    assert first == "Teleports entities (players, mobs, etc.)."
    assert "teleport <targets> <location> facing entity <facingEntity> [<facingAnchor>]" in text
    assert "<facingAnchor>: entity_anchor\nSpecifies the entity's eyes or feet" in text
    assert "Must be a rotation consisting of two double number elements" in text  # "or float[BE only]" cut
    for gone in ("checkForBlocks", "victim", "Bedrock", "History", "in nether run", "always facing the entity's feet"):
        assert gone not in text


def test_wiki_effect():
    text, first = wiki_text(raw("effect"), "effect")
    assert first == "Adds or removes status effects of players and other entities."
    assert "effect give <targets> <effect> infinite [<amplifier>] [<hideParticles>]" in text
    assert "<seconds>: integer" in text and "It must be between 0 and 1000000 (inclusive)." in text
    for gone in ("effect <player: target>", "2147483647", "NBT editor", "Bedrock"):
        assert gone not in text


def test_wiki_spreadplayers():
    text, first = wiki_text(raw("spreadplayers"), "spreadplayers")
    assert first == "Teleports entities to random surface locations within an area."
    assert "<center>: vec2\nSpecifies the center of the region to spread targets to." in text
    assert "<maxHeight>: integer\nSpecifies the maximum height for the target positions." in text
    assert "spreadplayers 0 0 200 500 true @a" in text
    for gone in ("Bedrock", "RelativeFloat", "victim", "one block above the highest solid block", "magma block"):
        assert gone not in text


def test_wiki_execute():
    text, first = wiki_text(raw("execute"), "execute")
    assert first.startswith("Executes another command but allows changing the executor")
    for kept in ("#### as", "as <targets> -> execute", "#### (if|unless) entity", "#### store (result|success) score",
                 "### Examples"):  # fmt: skip
        assert kept in text
    for gone in ("chainedCommand", "ExecuteChainedOption", "Option_If_Unless", "origin: target", "Bedrock", "History"):
        assert gone not in text


# ------------------------------------------------------------------ reference() and menu()


def test_reference_give(refs):
    ref = refs.reference({"give"})
    assert ref.startswith("## give\nSyntax on this server:\ngive <targets> <item> [<count>]\nWiki (Java Edition):\n")
    assert "### Arguments\n<targets>: entity" in ref and "/give @p pig_spawn_egg" in ref
    assert len(ref) < 5000


def test_reference_execute_only_the_subcommands_used(refs):
    _, subs = refs.used(["execute as @a at @s run give @s minecraft:diamond 1"])
    ref = refs.reference({"execute"}, subs)
    assert "execute (as|at) <targets> ..." in ref and "execute run <command>" in ref
    assert "as <targets> -> execute" in ref and "at <targets> -> execute" in ref
    for gone in ("execute (if|unless)", "execute store", "align", "#### (if|unless)", "Stored values"):
        assert gone not in ref
    assert len(ref) < 4000


def test_reference_execute_if_entity_only(refs):
    ref = refs.reference({"execute"}, {"if", "if entity", "run"})
    assert "execute (if|unless) entity <entities> [...]" in ref
    assert "execute (if|unless) block" not in ref
    assert "# (if|unless) entity\n" in ref and "# (if|unless) biome\n" not in ref  # the sections, by heading


def test_reference_alias_unknown_and_missing_wiki(refs):
    one = refs.reference({"tp", "teleport"})
    assert one.count("## teleport") == 1 and "## teleport (also written tp)" in one
    assert "Teleports" not in one.split("Wiki (Java Edition):")[0]
    assert "Not a command on this server. Did you mean: give?" in refs.reference({"gvie"})
    say = refs.reference({"say"})  # no wiki page in this fixture: syntax alone
    assert "say <message>" in say and "Wiki" not in say


def test_menu(refs):
    lines = refs.menu().split("\n")
    assert "give — Gives a specified amount of a certain item to one or more players." in lines
    assert "tp — the same as teleport." in lines
    assert "say" in lines  # no page: the name alone
    assert len(lines) == len(refs.names()) == 94
    assert "fill" not in refs.menu(exclude={"fill", "setblock"}).split("\n")


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


async def test_load_fetches_a_new_version(tmp_path, small):
    vfg, wiki, logs = FakeVfg(small), Wiki(), []
    async with wiki.client() as http:
        refs = await load(tmp_path, vfg, http=http, log=logs.append, request_gap=0)
    assert refs.version == "26.3" and refs.names() == set(NAMES)
    assert vfg.calls == [(("action", "refs", "--version"), 30), (("action", "refs"), 300)]
    assert sorted(wiki.seen) == ["Commands/effect", "Commands/give", "Commands/say", "Commands/teleport", "Commands/tp"]
    assert wiki.agents == {R.USER_AGENT}
    d = tmp_path / "refs" / "26.3"
    for f in ("commands.json", "usage.json", "menu.txt", "complete.json", "wiki/give.txt", "wiki/tp.txt"):
        assert (d / f).is_file(), f
    assert json.loads((d / "commands.json").read_text()) == small
    assert json.loads((d / "usage.json").read_text())["give"] == ["give <targets> <item> [<count>]"]
    give = (d / "wiki" / "give.txt").read_text().split("\n")
    assert give[0] == "https://minecraft.wiki/w/Commands/give (CC BY-NC-SA 3.0, minecraft.wiki)"
    assert give[1] == "Gives a specified amount of a certain item to one or more players."
    assert (d / "wiki" / "tp.txt").read_text().startswith("https://minecraft.wiki/w/Commands/teleport (")
    assert not (d / "wiki" / "say.txt").exists()
    mark = json.loads((d / "complete.json").read_text())
    assert mark["absent"] == ["say"] and mark["redirected"] == ["tp"] and mark["pages"] == 4
    assert "give — Gives a specified amount" in (d / "menu.txt").read_text()
    assert "/give @p pig_spawn_egg" in refs.reference({"give"})


async def test_load_uses_a_complete_cache(tmp_path, small):
    async with Wiki().client() as http:
        first = await load(tmp_path, FakeVfg(small), http=http, log=lambda m: None, request_gap=0)
    vfg = FakeVfg(None)  # would fail `vfg action refs`
    async with Wiki(broken_after=0).client() as http:  # and the wiki is never asked
        again = await load(tmp_path, vfg, http=http, log=lambda m: None, request_gap=0)
    assert vfg.calls == [(("action", "refs", "--version"), 30)]
    assert again.menu() == first.menu() and again.reference({"give"}) == first.reference({"give"})
    assert again.usage == first.usage


async def test_load_fetches_again_for_a_new_version(tmp_path, small):
    async with Wiki().client() as http:
        await load(tmp_path, FakeVfg(small), http=http, log=lambda m: None, request_gap=0)
        newer = await load(tmp_path, FakeVfg(small, "26.4"), http=http, log=lambda m: None, request_gap=0)
    assert newer.version == "26.4"
    assert (tmp_path / "refs" / "26.3" / "complete.json").is_file()
    assert (tmp_path / "refs" / "26.4" / "complete.json").is_file()


async def test_a_fetch_that_fails_part_way_is_not_marked_complete(tmp_path, small):
    logs: list[str] = []
    async with Wiki(broken_after=2).client() as http:
        refs = await load(tmp_path, FakeVfg(small), http=http, log=logs.append, request_gap=0)
    d = tmp_path / "refs" / "26.3"
    assert not (d / "complete.json").exists()
    assert any("the wiki fetch stopped" in m for m in logs)
    # what came in is used; the rest has its syntax alone
    assert "Wiki (Java Edition)" in refs.reference({"give"})
    tele = refs.reference({"teleport"})
    assert "teleport <targets> <location> [<rotation>]" in tele and "Wiki" not in tele
    # the next start finishes it: no second `vfg action refs`, only the missing pages
    vfg, wiki = FakeVfg(small), Wiki()
    async with wiki.client() as http:
        done = await load(tmp_path, vfg, http=http, log=logs.append, request_gap=0)
    assert vfg.calls == [(("action", "refs", "--version"), 30)]
    assert sorted(wiki.seen) == ["Commands/say", "Commands/teleport", "Commands/tp"]
    assert (d / "complete.json").is_file()
    assert "Wiki (Java Edition)" in done.reference({"teleport"})


async def test_a_page_that_fails_leaves_its_syntax_alone(tmp_path, small):
    logs: list[str] = []
    async with Wiki(unavailable=frozenset({"effect"})).client() as http:
        refs = await load(tmp_path, FakeVfg(small), http=http, log=logs.append, request_gap=0)
    assert not (tmp_path / "refs" / "26.3" / "complete.json").exists()
    assert any("1 wiki pages not fetched (effect)" in m for m in logs)
    eff = refs.reference({"effect"})
    assert eff.startswith("## effect\nSyntax on this server:\neffect clear [<targets>] [<effect>]") and "Wiki" not in eff
    assert "effect\n" in refs.menu() + "\n" and "give — Gives" in refs.menu()


async def test_the_wiki_unreachable_stops_asking(tmp_path, tree):
    big = {"type": "root", "children": dict(list(tree["children"].items())[:12])}
    wiki = Wiki(unavailable=frozenset(big["children"]))
    async with wiki.client() as http:
        refs = await load(tmp_path, FakeVfg(big), http=http, log=lambda m: None, request_gap=0)
    assert len(wiki.seen) == 2 * R.GIVE_UP_AFTER  # each failed page tried twice, then no more
    assert refs.names() == set(big["children"])


async def test_vfg_failing_with_no_cache_raises(tmp_path):
    with pytest.raises(RefsError, match="nothing is cached"):
        await load(tmp_path, FakeVfg(None, version_fails=True), log=lambda m: None, request_gap=0)
    with pytest.raises(RefsError, match="nothing is cached"):
        await load(tmp_path, FakeVfg(None, refs_fails=True), log=lambda m: None, request_gap=0)
    assert not (tmp_path / "refs" / "26.3").exists()


async def test_vfg_failing_uses_the_newest_cache(tmp_path, small):
    async with Wiki().client() as http:
        await load(tmp_path, FakeVfg(small), http=http, log=lambda m: None, request_gap=0)
    logs: list[str] = []
    refs = await load(tmp_path, FakeVfg(None, version_fails=True), log=logs.append, request_gap=0)
    assert refs.version == "26.3" and "give" in refs.names()
    assert any("using the cached 26.3" in m for m in logs)


async def test_an_older_cache_format_is_rebuilt_without_the_network(tmp_path, small):
    async with Wiki().client() as http:
        await load(tmp_path, FakeVfg(small), http=http, log=lambda m: None, request_gap=0)
    d = tmp_path / "refs" / "26.3"
    mark = json.loads((d / "complete.json").read_text())
    (d / "complete.json").write_text(json.dumps({**mark, "format": R.FORMAT - 1}))
    (d / "wiki" / "give.txt").write_text("stale\n")
    async with Wiki(broken_after=0).client() as http:
        refs = await load(tmp_path, FakeVfg(None), http=http, log=lambda m: None, request_gap=0)
    assert refs.first["give"].startswith("Gives a specified amount")
    assert (d / "wiki" / "give.txt").read_text().startswith("https://minecraft.wiki/w/Commands/give (")
    assert json.loads((d / "complete.json").read_text())["format"] == R.FORMAT


async def test_fetch_page_follows_redirects_and_knows_a_missing_page():
    def handler(request: httpx.Request) -> httpx.Response:
        if "hop" not in request.url.params:  # an HTTP redirect first
            return httpx.Response(301, headers={"location": str(request.url.copy_add_param("hop", "1"))})
        if request.url.params["titles"] == "Commands/tp":
            return httpx.Response(200, json=answer("tp"))
        return httpx.Response(200, json={"query": {"pages": [{"title": "Commands/nothing", "missing": True}]}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        page = await R.fetch_page(http, R._Pacer(0), "Commands/tp")
        gone = await R.fetch_page(http, R._Pacer(0), "Commands/nothing")
    assert page.status == "ok" and page.title == "Commands/teleport" and page.redirected
    assert page.url == "https://minecraft.wiki/w/Commands/teleport" and page.text.startswith("Teleports entities")
    assert gone.status == "absent"


async def test_pacer_keeps_a_gap_between_requests():
    pacer = R._Pacer(0.05)
    start = time.monotonic()
    for _ in range(4):
        await pacer.wait()
    assert time.monotonic() - start >= 0.14
