"""content.py: the engine's content classes as files, read on the PC (the game engine spec).

Python 3.9+, standard library only. `vfg route` (route.py) and `vfg prefix` (prefix.py) read them
through it. Nothing here imports a class or touches EC2: each file is parsed, never run.

Where they are:

    <repo>/engine/library/<id>.py           the library: the server's base content, and the examples
                                            implementers read (engine/WRITING.md)
    <repo>/made/<server>/content/<id>.py    a class this server's implementers wrote

One class per file. A class's id is its `id = "..."`, else its name in snake_case, as the engine
gives it (vfg_engine/content.py), and its kind is the content base it subclasses: Item, Ability,
Mark, Actor or Scenario.

    lib = Content(repo, server)       server is VFG_SERVER ("main"); None: the library's alone
    lib.classes()                     every class, the library's then the server's, each by id
    lib.get(id)                       the library's, else the server's, else None

    A Class is one file's class:
      .id .name .kind ("ability"|"item"|"mark"|"actor"|"scenario") .source ("library"|"made")
      .path      the .py, absolute          .rel   from the repo: engine/library/fireball.py
      .text      the whole source           .first_line  its docstring's first line, else the file's
      .config    its `class Config` as written ("" with none)
      .args      its `class Args` as written ("" with none)
      .problem   why it could not be read, or None
"""
import ast
import os
import re

KINDS = {"Item": "item", "Ability": "ability", "Mark": "mark", "Actor": "actor", "Scenario": "scenario"}
ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def snake(name):
    """FireStaff -> fire_staff, IceQueenBoss -> ice_queen_boss: an id when a class sets none."""
    s = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s).lower()


class Class(object):
    __slots__ = ("id", "name", "kind", "source", "path", "rel", "text", "first_line", "config", "args", "problem")

    def __init__(self, source, path, rel):
        self.source, self.path, self.rel = source, path, rel
        self.id = os.path.splitext(os.path.basename(path))[0]
        self.name, self.kind, self.text, self.first_line, self.config, self.args = None, None, "", "", "", ""
        self.problem = None

    def __repr__(self):
        return "<Class %s %s %s>" % (self.kind, self.id, self.source)


def _doc_line(node):
    doc = ast.get_docstring(node) if node is not None else None
    return doc.strip().split("\n", 1)[0].strip() if doc else ""


def _base(node):
    for b in node.bases:
        name = b.id if isinstance(b, ast.Name) else b.attr if isinstance(b, ast.Attribute) else ""
        if name in KINDS:
            return KINDS[name]
    return None


def _id_of(node):
    for b in node.body:
        targets = b.targets if isinstance(b, ast.Assign) else [b.target] if isinstance(b, ast.AnnAssign) else []
        if any(isinstance(t, ast.Name) and t.id == "id" for t in targets) \
                and isinstance(getattr(b, "value", None), ast.Constant) and isinstance(b.value.value, str):
            return b.value.value
    return snake(node.name)


def _segment(text, node):
    """A node's lines as written, its indentation kept."""
    lines = text.splitlines(True)
    start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
    return "".join(lines[start - 1:node.end_lineno])


def parse(path, source, rel):
    """One file's class, read without running it; .problem says why when it cannot be."""
    c = Class(source, path, rel)
    try:
        with open(path, encoding="utf-8") as f:
            c.text = f.read()
        tree = ast.parse(c.text)
    except (OSError, SyntaxError, ValueError) as e:
        c.problem = "%s: %s" % (type(e).__name__, e)
        return c
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and _base(n)]
    if len(classes) != 1:
        c.problem = "%s content classes in it, not one" % ("no" if not classes else len(classes))
        return c
    node = classes[0]
    c.name, c.kind, c.id = node.name, _base(node), _id_of(node)
    c.first_line = _doc_line(node) or _doc_line(tree)
    for b in node.body:
        if isinstance(b, ast.ClassDef) and b.name == "Config":
            c.config = _segment(c.text, b)
        elif isinstance(b, ast.ClassDef) and b.name == "Args":
            c.args = _segment(c.text, b)
    if not c.first_line and c.config:   # a Config's summary says what it does
        m = re.search(r"^\s*summary\s*(?::[^=]*)?=\s*([\"'])(.*?)\1", c.config, re.M)
        c.first_line = m.group(2) if m else ""
    return c


class Content(object):
    """The library's classes and this server's, read from their files."""

    def __init__(self, repo, server="main"):
        self.repo, self.server = repo, server
        self.library = os.path.join(repo, "engine", "library")
        self.made = os.path.join(repo, "made", server, "content") if server else None

    def _in(self, folder, source):
        out = []
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            return out
        for fn in names:
            stem, ext = os.path.splitext(fn)
            path = os.path.join(folder, fn)
            if ext != ".py" or fn.startswith((".", "_")) or not ID_RE.match(stem) or not os.path.isfile(path):
                continue
            out.append(parse(path, source, os.path.relpath(path, self.repo)))
        return out

    def classes(self):
        """The library's, then this server's, each by file name; one whose id the library has is
        left out of the server's."""
        lib = self._in(self.library, "library")
        have = set(c.id for c in lib)
        return lib + [c for c in (self._in(self.made, "made") if self.made else []) if c.id not in have]

    def get(self, cid):
        for c in self.classes():
            if c.id == cid:
                return c
        return None
