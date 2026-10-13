"""Refs: what a drafted Minecraft command is checked against before it runs.

Per server version, cached in the brain's folder, `$VFG_BRAIN_DIR/refs/<version>/`: the server's own command tree
(`vfg action refs`), as usage lines like `give <targets> <item> [<count>]`, and each command's
minecraft.wiki page as plain text from the wiki's API, cut to its Java Edition Usage, Syntax,
Arguments and Examples (CC BY-NC-SA: each cached file names its source on line 1). The cache holds
commands.json, usage.json, menu.txt, wiki/<name>.txt, raw/<name>.txt (the page as fetched),
index.json and, written last, complete.json; without it the next load() finishes the fetch.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import os
import re
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

FORMAT = 2  # bump when usage lines or wiki text change shape: the cache is rebuilt from its raw files
API = "https://minecraft.wiki/api.php"
WIKI = "https://minecraft.wiki/w/"
LICENSE = "CC BY-NC-SA 3.0, minecraft.wiki"
USER_AGENT = "EverHear-brain/0.1 (+https://everhearlabs.com)"
REQUEST_GAP_S = 1.0
REFS_TIMEOUT_S = 300
GIVE_UP_AFTER = 5  # consecutive failed pages: the wiki is unreachable, stop asking it
SECTION_CAP = 1800  # characters of one wiki section kept in the cache
WIKI_CAP = 4000  # characters of one command's wiki text in a reference
EXECUTE_CAP = 6000  # execute's, cut to the subcommands in use


class RefsError(Exception):
    """No references at all: `vfg` failed and nothing is cached. Safe to log."""


# ---------------------------------------------------------------- the command tree -> usage lines

_ROOT = "root"  # the redirect of `execute run`, which the report leaves out


def _redirect(node: dict) -> str | tuple[str, ...] | None:
    if "redirect" in node:
        return tuple(node["redirect"]) or _ROOT
    if node.get("type") == "literal" and not node.get("children") and not node.get("executable"):
        return _ROOT  # a literal that leads nowhere is a redirect to the root: `execute run`
    return None


def aliases(tree: dict) -> dict[str, str]:
    """Top-level commands that are another one under a second name: {"tp": "teleport"}."""
    out = {}
    for name, node in tree.get("children", {}).items():
        red = _redirect(node)
        if isinstance(red, tuple) and len(red) == 1 and red[0] in tree["children"] and red[0] != name:
            out[name] = red[0]
    return out


class _Usage:
    """One command's subtree as a few readable lines. Literals are words, arguments `<name>`, a
    part after a point where the command can already run is in `[...]`, children with the same
    continuation fold into `(a|b)`, and children whose paths meet again a little deeper fold into
    one alternation before the shared part: `data modify` is a few lines, not a hundred."""

    def __init__(self, cmd: str) -> None:
        self.cmd = cmd
        self._keys: dict[int, str] = {}
        self._trivial: set[str] = set()

    def key(self, node: dict) -> str:
        """What follows a node, as a string: names, kinds and run points, not parsers."""
        k = self._keys.get(id(node))
        if k is None:
            kids = self.kids(node)
            k = json.dumps([bool(node.get("executable")), _redirect(node),
                            sorted((n, c["type"], self.key(c)) for n, c in kids.items())])  # fmt: skip
            self._keys[id(node)] = k
            if not kids:
                self._trivial.add(k)
        return k

    @staticmethod
    def tok(name: str, node: dict) -> str:
        return name if node["type"] == "literal" else f"<{name}>"

    @staticmethod
    def kids(node: dict) -> dict:
        """The children, less `minecraft:x` where `x` is there too (gamerule has both)."""
        ch = node.get("children", {})
        return {n: c for n, c in ch.items() if not (n.startswith("minecraft:") and n[10:] in ch)}

    def lines(self, node: dict) -> list[str]:
        if isinstance(_redirect(node), tuple):
            return []  # an alias: its target's lines are used
        forms = self.tails(node, 0) if node.get("children") else [()]
        return [_render(self.cmd, form) for form in forms]

    def tails(self, node: dict, depth: int) -> list[tuple]:
        """The ways to go on after `node`: tuples of (text, starts_optional_part)."""
        x = bool(node.get("executable"))
        red = _redirect(node)
        if red is not None:
            text = "<command>" if red == _ROOT else "..." if red == (self.cmd,) else "-> " + " ".join(red)
            return [((text, x),)]
        kids = self.kids(node)
        if not kids:
            return [()]
        out = [f for group in self._groups(kids, depth) for f in self._group_forms(group, depth)]
        if x and len(out) > 1 and not any(o for f in out for _, o in f):
            # several plain endings after a point that can already run: one [a|b c|d]
            return [(("(" + "|".join(" ".join(t for t, _ in f) for f in out) + ")", True),)]
        return [((f[0][0], True),) + f[1:] for f in out] if x else out

    def _cands(self, node: dict, depth: int = 0) -> dict[str, int]:
        """Continuations every path from `node` passes through, with their depth."""
        out = {self.key(node): depth}
        kids = self.kids(node)
        if kids and not node.get("executable") and _redirect(node) is None and depth < 3:
            common: dict[str, int] | None = None
            for c in kids.values():
                cc = self._cands(c, depth + 1)
                common = cc if common is None else {k: max(v, cc[k]) for k, v in common.items() if k in cc}
            for k, v in (common or {}).items():
                out.setdefault(k, v)
        return out

    def _allowed(self, cands: dict[str, int], depth: int) -> dict[str, int]:
        # the command's own subcommands stay one per line; meeting again only at the end is no shared part
        return {k: d for k, d in cands.items()
                if not (depth == 0 and (d > 0 or k in self._trivial)) and not (k in self._trivial and d > 0)}  # fmt: skip

    def _groups(self, kids: dict, depth: int) -> list[tuple[list[tuple[str, dict]], dict[str, int]]]:
        groups: list[tuple[list[tuple[str, dict]], dict[str, int]]] = []
        for name, kid in kids.items():
            c = self._allowed(self._cands(kid), depth)
            for i, (members, common) in enumerate(groups):
                inter = {k: max(v, c[k]) for k, v in common.items() if k in c}
                if inter:
                    groups[i] = (members + [(name, kid)], inter)
                    break
            else:
                groups.append(([(name, kid)], c))
        return groups

    def _group_forms(self, group, depth: int) -> list[tuple]:
        members, common = group
        if len(members) == 1:
            name, kid = members[0]
            return [((self.tok(name, kid), False),) + t for t in self.tails(kid, depth + 1)]
        join = min(common, key=lambda k: (common[k], k))
        alts: list[str] = []
        at = None
        for name, kid in members:
            text, a = self._prefix(name, kid, join)
            if text not in alts:
                alts.append(text)
            at = at or a
        head = alts[0] if len(alts) == 1 else "(" + "|".join(alts) + ")"
        return [((head, False),) + t for t in self.tails(at, depth + common[join] + 1)]

    def _prefix(self, name: str, node: dict, join: str) -> tuple[str, dict]:
        """From `node` down to the nodes whose continuation is `join`, as one alternation."""
        tok = self.tok(name, node)
        if self.key(node) == join:
            return tok, node
        parts: list[str] = []
        at = None
        for cn, c in self.kids(node).items():
            text, a = self._prefix(cn, c, join)
            if text not in parts:
                parts.append(text)
            at = at or a
        return f"{tok} {parts[0] if len(parts) == 1 else '(' + '|'.join(parts) + ')'}", at


def _render(cmd: str, form: tuple) -> str:
    groups: list[list[str]] = [[cmd]]
    opt = [False]
    for text, starts in form:
        if starts:
            groups.append([])
            opt.append(True)
        groups[-1].append(text)
    out = []
    for g, o in zip(groups, opt, strict=True):
        s = " ".join(g)
        if o and len(g) == 1 and s.startswith("(") and _sub_words(s) == [s]:
            s = s[1:-1]  # [a|b], not [(a|b)]
        out.append(f"[{s}]" if o else s)
    return " ".join(out)


def usage_lines(tree: dict) -> dict[str, list[str]]:
    """Every top-level command's usage lines. An alias gets its target's, renamed."""
    out = {name: _Usage(name).lines(node) for name, node in tree.get("children", {}).items()}
    for alias, target in aliases(tree).items():
        out[alias] = [alias + line[len(target) :] for line in out[target]]
    return out


# ---------------------------------------------------------------- which commands a draft uses

_QUALIFIED = {"if": 1, "unless": 1, "store": 2}  # which word after these names its kind: `if entity`, `store result score`
_PAIRS = {"[": "]", "{": "}", "(": ")"}


def tokenize(command: str) -> list[str]:
    """Split on spaces outside brackets, braces and quotes: one selector, one NBT, one token."""
    toks, cur, stack, quote_ch, escaped = [], "", [], "", False
    for ch in command:
        if quote_ch:
            quote_ch = "" if ch == quote_ch and not escaped else quote_ch
            escaped = ch == "\\" and not escaped
        elif ch in "\"'" and (stack or not cur or cur[-1] in "=:,"):
            quote_ch = ch
        elif ch in _PAIRS:
            stack.append(_PAIRS[ch])
        elif stack and ch == stack[-1]:
            stack.pop()
        elif ch.isspace() and not stack:
            toks += [cur] if cur else []
            cur = ""
            continue
        cur += ch
    return toks + ([cur] if cur else [])


def used_in(tree: dict, commands: Iterable[str]) -> tuple[set[str], set[str]]:
    """The commands a draft uses, looking inside `execute … run` (nested too), and the execute
    subcommands before each `run` (not the `as` of `positioned as`), with each condition's or
    store's kind: `if entity`, `store score`."""
    names: set[str] = set()
    subs: set[str] = set()
    kids = tree.get("children", {}).get("execute", {}).get("children", {})
    for c in commands:
        toks = tokenize(c.strip())
        while toks and toks[0].lstrip("/"):
            names.add(name := toks[0].lstrip("/").removeprefix("minecraft:"))
            if name != "execute":
                break
            toks = toks[1:]
            cut = toks.index("run") if "run" in toks else len(toks)
            for j, t in enumerate(toks[:cut]):
                if t in kids and not (t == "as" and j and toks[j - 1] in ("positioned", "rotated")):
                    subs.add(t)
                    if j < (k := j + _QUALIFIED.get(t, 0)) < cut:
                        subs.add(f"{t} {toks[k]}")
            subs.update(["run"] if cut < len(toks) else [])
            toks = toks[cut + 1 :]
    return names, subs


# ---------------------------------------------------------------- the wiki: fetching


def page_title(name: str) -> str:
    return f"Commands/{name}"


def page_url(title: str) -> str:
    return WIKI + quote(title.strip().replace(" ", "_"), safe="/:")


class _Pacer:
    """At most one request every `gap` seconds."""

    def __init__(self, gap: float) -> None:
        self.gap, self._next = gap, 0.0

    async def wait(self) -> None:
        now = time.monotonic()
        if self._next > now:
            await asyncio.sleep(self._next - now)
        self._next = max(now, self._next) + self.gap


@dataclass
class Page:
    status: str  # "ok", "absent" (no such page) or "failed" (try again another time)
    title: str  # the page read, after redirects
    url: str
    text: str = ""
    why: str = ""
    redirected: bool = False


async def fetch_page(http: httpx.AsyncClient, pacer: _Pacer, title: str) -> Page:
    """One wiki page as plain text (the API follows `tp` -> `teleport`). One retry for a 429,
    a 5xx or a dropped connection."""
    params = {"action": "query", "prop": "extracts", "explaintext": "1", "redirects": "1",
              "titles": title, "format": "json", "formatversion": "2"}  # fmt: skip
    why = ""
    for attempt in range(2):
        if attempt:
            await asyncio.sleep(min(5.0, pacer.gap * 3))
        await pacer.wait()
        try:
            resp = await http.get(API, params=params, headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=20)
        except httpx.HTTPError as e:
            why = f"{type(e).__name__}: {e}".rstrip(": ")
            continue
        if resp.status_code != 200:
            why = f"HTTP {resp.status_code}"
            if resp.status_code in (429, 500, 502, 503, 504):
                continue
            break
        try:
            query = resp.json()["query"]
            page = query["pages"][0]
        except (ValueError, KeyError, IndexError, TypeError):
            why = "not an API answer"
            continue
        final = page.get("title", title)
        if page.get("missing") or page.get("invalid") or not page.get("extract"):
            return Page("absent", final, page_url(final), why="no such page")
        return Page("ok", final, page_url(final), page["extract"], redirected=bool(query.get("redirects")))
    return Page("failed", title, page_url(title), why=why)


# ---------------------------------------------------------------- the wiki: plain text -> Java Edition text

_KEEP = {"usage", "syntax", "arguments", "argument", "examples", "example", "detail", "details"}
_DROP = {"history", "see also", "external links", "references", "navigation", "trivia", "gallery", "issues",
         "notes", "videos", "result", "results", "output", "outputs"}  # fmt: skip
_LABEL = re.compile(r"^(Java|Bedrock) Edition(?: \(.*\))?:?$")  # "Bedrock Edition (JSON)" over its own lines
_SUBLABELS = {"syntax", "arguments", "result", "example", "examples", "technical"}  # execute's per-subcommand parts
_BE_HEAD = re.compile(r"^BE\s*[:：]")  # an argument only Bedrock has
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z/])")


def wiki_text(extract: str, name: str) -> tuple[str, str]:
    """A command page's plain text -> (its Java Edition text with `## Section` headings, its first
    sentence). `name` picks the command's own part of a page holding several (ban, ban-ip, banlist)."""
    parts = re.split(r"^(={2,6}) (.*?) \1\s*$", extract, flags=re.M)
    lead = parts[0]
    secs = [(len(parts[i]), parts[i + 1].strip(), parts[i + 2]) for i in range(1, len(parts), 3)]
    own = next((i for i, s in enumerate(secs) if s[0] == 2 and s[1].lower() == name.lower()), None)
    if own is not None:
        if _java(secs[own][2]):
            lead = secs[own][2]
        rest = secs[own + 1 :]
        end = next((i for i, s in enumerate(rest) if s[0] <= 2), len(rest))
        secs = [(lv - 1, t, b) for lv, t, b in rest[:end]]
    first = _first_sentence(" ".join(_java(lead).split()))
    out: list[str] = []
    keep_level = drop_level = 0
    for level, title, body in secs:
        low = title.lower()
        if drop_level and level > drop_level:
            continue
        drop_level = 0
        if keep_level and level <= keep_level:
            keep_level = 0
        if low in _DROP or "bedrock" in low:
            drop_level = level
        elif keep_level or low in _KEEP:
            keep_level = keep_level or level
            out += ["#" * level + " " + title, _cap(_java(body), SECTION_CAP)]
        elif level <= 3:
            out.append("#" * level + " " + title)  # a group: kept when something under it is
    lines = [x for x in "\n".join(out).split("\n") if x.strip()]
    kept = []
    for i, line in enumerate(lines):  # a heading with nothing under it goes
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if line.startswith("#") and (not nxt or (nxt.startswith("#") and _level(nxt) <= _level(line))):
            continue
        kept.append(line)
    return "\n".join(kept), first


def _level(line: str) -> int:
    return len(line) - len(line.lstrip("#"))


def _java(text: str) -> str:
    """The Java Edition lines of a section: what follows a Bedrock label goes, and so do Bedrock-only
    arguments, sentences that mention Bedrock, and the `BE:` tail of an argument line."""
    out: list[str] = []
    be_block = be_arg = after_je_arg = False
    for raw in text.split("\n"):
        s = raw.strip()
        label = _LABEL.match(s)
        if label:
            be_block = label.group(1) == "Bedrock"
            continue
        if be_block and (not s or s.lower() in _SUBLABELS or s.startswith("JE:")):
            be_block = False
        if be_block:
            continue
        if _BE_HEAD.match(s):
            be_arg = not after_je_arg  # right under a Java argument, it shares that one's description
            continue
        if s.startswith("JE:") or re.match(r"^<[^<>]+>: \S+$", s):
            s = re.sub(r"BE\s*[:：].*$", "", s.removeprefix("JE:")).strip()
            be_arg, after_je_arg = False, True
        elif s:
            after_je_arg = False
            if s.lower() in _SUBLABELS:
                be_arg = False
            if be_arg:
                continue
        s = _clean(s)
        if s:
            out.append(s)
    return "\n".join(_drop_orphans(out))


def _clean(line: str) -> str:
    if line.startswith("In Bedrock Edition"):
        return ""
    line = line.replace("\u200c", "")
    line = re.sub(r"(?:\s+or\s+|/)[^.,;/]*?\[(?:Bedrock Edition|BE)\s+only\]", "", line)  # "double[JE only] or float[BE only]"
    line = re.sub(r",? or [^.,;]*? in Bedrock Edition", "", line)  # "packs in Java Edition, or ... in Bedrock Edition"
    line = re.sub(r"\s+or\s+[a-z][A-Za-z ]*: [A-Za-z][\w<>]*", "", line)  # "<targets> or player: target"
    line = re.sub(r"\s+(?:or|and)\s+[^.;]*?<[A-Za-z]\w*: [^<>]+>(?:\s*<[^<>]+>)*", "", line)  # "or as <origin: target>"
    line = re.sub(r"\[(?:Java Edition|JE)\s+only\]|\[(?:needs|verify|more info|check)[^\]]*\]", "", line)
    line = re.sub(r" in Java Edition\b", "", re.sub(r"\bIn Java Edition,\s*(\w)", lambda m: m.group(1).upper(), line))
    sents = [x for x in _SENTENCE.split(line)  # Bedrock's, and the type boilerplate "Must be a [Int]32-bit ..."
             if "Bedrock" not in x and "[BE" not in x and not re.match(r"Must be an? \[", x)]  # fmt: skip
    return re.sub(r"\s{2,}", " ", " ".join(sents)).strip()


def _drop_orphans(lines: list[str]) -> list[str]:
    """An example's description whose commands were all Bedrock's goes too."""
    return [x for i, x in enumerate(lines)
            if not (x.endswith(":") and (i + 1 == len(lines) or lines[i + 1].endswith(":")))]  # fmt: skip


def _cap(text: str, cap: int) -> str:
    if len(text) <= cap:
        return text
    cut = text.rfind("\n", 0, cap)
    return text[: cut if cut > cap // 2 else cap].rstrip() + "\n…"


def _first_sentence(text: str) -> str:
    depth = 0
    for i, ch in enumerate(text):
        depth += (ch in "([") - (ch in ")]")
        if ch in ".!?" and depth <= 0 and (i + 1 == len(text) or text[i + 1] == " "):
            if text[: i + 1].rsplit(" ", 1)[-1].lower() not in ("e.g.", "i.e.", "etc.", "vs."):
                return text[: i + 1]
    return text


# ---------------------------------------------------------------- Refs


class Refs:
    """One server version's references, ready for prompts."""

    def __init__(self, version: str, tree: dict, *, wiki: dict[str, str] | None = None,
                 first: dict[str, str] | None = None, usage: dict[str, list[str]] | None = None) -> None:  # fmt: skip
        self.version, self.tree = version, tree
        self.wiki, self.first = dict(wiki or {}), dict(first or {})
        self.aliases = aliases(tree)
        self.usage = usage if usage is not None else usage_lines(tree)

    def names(self) -> set[str]:
        return set(self.tree.get("children", {}))

    def menu(self, exclude: Iterable[str] = ()) -> str:
        """One line per command: `give — Gives a specified amount of a certain item to ...`."""
        lines = []
        for name in sorted(self.names() - set(exclude)):
            if name in self.aliases:
                lines.append(f"{name} — the same as {self.aliases[name]}.")
            else:
                lines.append(f"{name} — {self.first[name]}" if self.first.get(name) else name)
        return "\n".join(lines)

    def used(self, commands: list[str]) -> tuple[set[str], set[str]]:
        """(the top-level commands the draft uses, looking inside `execute … run`, nested too; the
        execute subcommands it uses, e.g. {"as", "at", "run"}). A condition or a store also names its
        kind, `if entity` or `store score`, beside `if` or `store`: reference() then shows only that
        kind's part, and a changed kind is new."""
        return used_in(self.tree, commands)

    def reference(self, names: set[str], execute_subs: set[str] | frozenset[str] = frozenset()) -> str:
        """For each command: its usage lines from this server's tree, what each argument takes, then
        the wiki's Java Edition text. For `execute`, only the subcommands given (all when none are)."""
        blocks, shown, known = [], set(), self.names()
        subs = set(execute_subs)
        for name in sorted(names):
            canon = self.aliases.get(name, name)
            if canon in shown:
                continue
            shown.add(canon)
            if canon not in known:
                close = difflib.get_close_matches(name, sorted(known), n=3, cutoff=0.75)
                hint = f" Did you mean: {', '.join(close)}?" if close else ""
                blocks.append(f"## {name}\nNot a command on this server.{hint}")
                continue
            also = sorted(a for a in names if self.aliases.get(a) == canon)
            usage = self.usage.get(canon, [])
            wiki = self.wiki.get(canon) or (self.wiki.get(also[0], "") if also else "")
            if canon == "execute" and subs:
                usage = [u for u in usage if _wanted(u.split(" ", 1)[-1], subs)]
                wiki = _execute_wiki(wiki, subs)
            parts = [f"## {canon}" + (f" (also written {', '.join(also)})" if also else ""), "Syntax on this server:", *usage]
            if canon == "execute":
                parts.append("(`...` = more subcommands follow; `[...]` = may end here or go on; `run <command>` ends it)")
            if wiki:
                wiki = _cap(wiki, EXECUTE_CAP) if canon == "execute" else _fit(wiki, WIKI_CAP)
                parts += ["Wiki (Java Edition):", re.sub(r"^(#+) ", r"#\1 ", wiki, flags=re.M)]
            blocks.append("\n".join(parts))
        return "\n\n".join(blocks)


def _fit(wiki: str, cap: int) -> str:
    """At most `cap` characters: Usage goes first when it is too long, then the end is cut."""
    if len(wiki) > cap:
        wiki = re.sub(r"^## Usage\n(?:(?!## ).*\n?)*", "", wiki, flags=re.M).strip()
    return _cap(wiki, cap)


# ---------------------------------------------------------------- execute's subcommands

def _sub_words(text: str) -> list[str]:
    """Split on spaces outside parentheses: `(if|unless) data (block|entity) <x>` -> 4 words."""
    out, cur, depth = [], "", 0
    for ch in text.strip() + " ":
        depth += (ch == "(") - (ch == ")")
        if ch == " " and depth == 0:
            out += [cur] if cur else []
            cur = ""
        else:
            cur += ch
    return out


def _alts(word: str) -> set[str]:
    """`(block <t>|entity <t>)` -> {block, entity}; `as` -> {as}."""
    return {a.split(" ", 1)[0] for a in word.removeprefix("(").removesuffix(")").split("|") if a}


def _wanted(text: str, selected: set[str]) -> bool:
    """Is a usage line (without `execute `) or a wiki heading about the selected subcommands?
    `(if|unless) entity <entities> [...]` is about `if` and `unless`, of the kind `entity`."""
    words = _sub_words(text)
    subs = _alts(words[0]) if words else set()
    at = max((_QUALIFIED.get(x, 0) for x in subs), default=0)
    kinds = _alts(words[at]) if at and len(words) > at else set()
    for sub in subs & selected:
        asked = {x.split(" ", 1)[1] for x in selected if x.startswith(sub + " ")}
        if not kinds or not asked or kinds & asked:
            return True
    return False


_GROUPS = {"condition": {"if", "unless"}, "store": {"store"}}  # "### Condition subcommands" says what if/unless share


def _execute_wiki(wiki: str, selected: set[str]) -> str:
    """Its Usage and the sections of the subcommands in use (`#### as`, `#### (if|unless) block`, the
    Condition intro for if/unless). The full syntax tree, the examples and the general parts go."""
    out, top, keep = [], "", False
    for line in wiki.split("\n"):
        m = re.match(r"^(#{2,6}) (.*)$", line)
        if m:
            level, title = len(m.group(1)), m.group(2)
            top = title.lower() if level == 2 else top
            if level == 2:
                keep = top == "usage"
            elif level == 3:
                keep = top == "syntax" and bool(_GROUPS.get(title.lower().split(" ", 1)[0], set()) & selected)
            else:
                keep = top == "syntax" and _wanted(title, selected)
            if level > 2 and not keep:
                continue
        if keep or (m and len(m.group(1)) == 2):
            out.append(line)
    return "\n".join(out).strip()


# ---------------------------------------------------------------- load(): the cache, `vfg`, the wiki

Vfg = Callable[..., Awaitable[Any]]  # vfg(*args, timeout=...) -> VfgResult(code, stdout, stderr)
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")


async def load(dispatch_dir: Path, vfg: Vfg, *, http: httpx.AsyncClient | None = None,
               log: Callable[[str], Any] = print, request_gap: float = REQUEST_GAP_S) -> Refs:  # fmt: skip
    """The references for the server's version: from `refs/<version>/` when it is complete, else
    from `vfg action refs` and the wiki (one request a second), written there. When `vfg` fails, the
    newest complete cache is used; with none, RefsError."""
    root = Path(dispatch_dir) / "refs"
    out = await _ask(vfg, log, "action", "refs", "--version", timeout=30)
    version = out.strip().splitlines()[-1].strip() if out and out.strip() else None
    if out is not None and not _VERSION.match(version or ""):
        log(f"refs: vfg action refs --version printed no version: {out.strip()[:80]!r}")
        version = None
    d = root / version if version else None
    if d is not None and _is_complete(d):
        return _read_complete(d, log)
    tree = _read_json(d / "commands.json") if d is not None else None
    if tree is not None:
        log(f"refs: {version} was left part-fetched; finishing it")
    elif d is not None:
        log(f"refs: fetching the command tree of {version} (vfg action refs)")
        tree = _tree(await _ask(vfg, log, "action", "refs", timeout=REFS_TIMEOUT_S), log)
    if tree is None:
        cached = _newest_complete(root)
        if cached is None:
            raise RefsError(f"no command references for {version or 'the server'}: vfg failed and nothing is cached")
        log(f"refs: using the cached {cached.name}" + (f" instead of {version}" if version else ""))
        return _read_complete(cached, log)
    _write(d / "commands.json", json.dumps(tree))
    client = http or httpx.AsyncClient()
    try:
        return await _fetch_wiki(d, version, tree, client, log, request_gap)
    finally:
        if http is None:
            await client.aclose()


async def _ask(vfg: Vfg, log, *args: str, timeout: float) -> str | None:
    """`vfg <args>`'s stdout, or None (logged) when it failed."""
    try:
        r = await vfg(*args, timeout=timeout)
    except Exception as e:  # the runner itself broke: the same as a failed call
        log(f"refs: vfg {' '.join(args)}: {type(e).__name__}: {e}")
        return None
    if r.code == 0:
        return r.stdout or ""
    lines = (r.stderr or r.stdout or "").strip().splitlines()
    log(f"refs: vfg {' '.join(args)} failed ({r.code}): {lines[-1] if lines else 'no output'}")
    return None


def _tree(out: str | None, log) -> dict | None:
    """`vfg action refs`'s `{"version", "commands"}` -> the command tree."""
    try:
        tree = json.loads(out)["commands"] if out is not None else None
        if tree is not None and not (isinstance(tree, dict) and isinstance(tree.get("children"), dict) and tree["children"]):
            raise TypeError("no commands in it")
        return tree
    except (ValueError, KeyError, TypeError) as e:
        log(f"refs: vfg action refs gave no command tree: {type(e).__name__}: {e}")
        return None


async def _fetch_wiki(d: Path, version: str, tree: dict, http: httpx.AsyncClient, log, gap: float) -> Refs:
    index: dict[str, dict] = _read_json(d / "index.json") or {}
    names = sorted(tree["children"])
    todo = [n for n in names if index.get(n, {}).get("status") not in ("ok", "absent") or not _raw_ok(d, n, index)]
    if todo:
        log(f"refs: fetching {len(todo)} wiki pages for {version}, one a second")
    pacer, failed_in_row, stopped = _Pacer(gap), 0, False
    try:
        for n in todo:
            if failed_in_row >= GIVE_UP_AFTER:
                index[n] = {"status": "failed", "why": "wiki unreachable", "title": page_title(n), "url": page_url(page_title(n))}
                continue
            page = await fetch_page(http, pacer, page_title(n))
            failed_in_row = failed_in_row + 1 if page.status == "failed" else 0
            index[n] = {"status": page.status, "title": page.title, "url": page.url,
                        "redirected": page.redirected, "why": page.why}  # fmt: skip
            if page.status == "ok":
                _write(d / "raw" / f"{n}.txt", page.url + "\n" + page.text)
            _write(d / "index.json", json.dumps(index, indent=1, sort_keys=True))
    except Exception as e:  # what was fetched is kept; the rest is fetched at the next start
        log(f"refs: the wiki fetch stopped: {type(e).__name__}: {e}")
        stopped = True
    refs = _derive(d, version, tree, index, log)
    missing = [n for n in names if index.get(n, {}).get("status") not in ("ok", "absent")]
    if missing:
        log(f"refs: {len(missing)} wiki pages not fetched ({', '.join(missing[:8])}{'…' if len(missing) > 8 else ''}); "
            "those commands have their syntax alone until the next start fetches them")  # fmt: skip
    elif not stopped:
        _mark_complete(d, version, index)
    return refs


def _derive(d: Path, version: str, tree: dict, index: dict, log, *, write: bool = True) -> Refs:
    """commands.json + raw pages -> Refs; with `write`, also usage.json, wiki/<name>.txt, menu.txt."""
    wiki, first = {}, {}
    for n in sorted(tree["children"]):
        e = index.get(n, {})
        if e.get("status") != "ok" or not _raw_ok(d, n, index):
            continue
        try:
            wiki[n], first[n] = wiki_text((d / "raw" / f"{n}.txt").read_text().split("\n", 1)[1], n)
        except Exception as ex:  # one odd page must not cost the others
            log(f"refs: could not read the wiki page for {n}: {type(ex).__name__}: {ex}")
            continue
        if write:
            _write(d / "wiki" / f"{n}.txt", f"{e['url']} ({LICENSE})\n{first[n]}\n{wiki[n]}\n")
    refs = Refs(version, tree, wiki=wiki, first=first)
    if write:
        _write(d / "usage.json", json.dumps(refs.usage, indent=1, sort_keys=True))
        _write(d / "menu.txt", refs.menu() + "\n")
    return refs


def _mark_complete(d: Path, version: str, index: dict) -> None:
    mark = {"format": FORMAT, "version": version, "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "pages": sum(e.get("status") == "ok" for e in index.values()),
            "absent": sorted(n for n, e in index.items() if e.get("status") == "absent"),
            "redirected": sorted(n for n, e in index.items() if e.get("redirected"))}  # fmt: skip
    _write(d / "complete.json", json.dumps(mark, indent=1))


def _is_complete(d: Path) -> bool:
    return bool(_read_json(d / "complete.json")) and (d / "commands.json").is_file() and (d / "index.json").is_file()


def _newest_complete(root: Path) -> Path | None:
    done = [p for p in root.iterdir() if p.is_dir() and _is_complete(p)] if root.is_dir() else []
    return max(done, key=lambda p: (p / "complete.json").stat().st_mtime) if done else None


def _read_complete(d: Path, log) -> Refs:
    """A complete cache, read again from its raw pages; rewritten when an older parser wrote it."""
    tree = _read_json(d / "commands.json")
    if tree is None:
        raise RefsError(f"the cached references in {d} are unreadable")
    stale = (_read_json(d / "complete.json") or {}).get("format") != FORMAT
    if stale:
        log(f"refs: rebuilding the cached {d.name} for this brain")
    index = _read_json(d / "index.json") or {}
    refs = _derive(d, d.name, tree, index, log, write=stale)
    if stale:
        _mark_complete(d, d.name, index)
    return refs


def _raw_ok(d: Path, n: str, index: dict) -> bool:
    return index.get(n, {}).get("status") != "ok" or (d / "raw" / f"{n}.txt").is_file()


def _read_json(p: Path) -> Any:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def _write(p: Path, text: str) -> None:
    """Whole or not at all: a crash never leaves half a file to be trusted."""
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    tmp.write_text(text)
    os.replace(tmp, p)
