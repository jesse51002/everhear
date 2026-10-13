"""prefix: the part of an implementer's system prompt that is the same on every spawn.

The provider's cache reuses a prompt's start only when it is the same bytes, so this is built from
files that change only in a coding session, in a fixed order, with nothing in it that changes
between spawns — no date, no count, no absolute path, nothing from made/ (what implementers make
goes in each brief):

  1. how a content class is written: engine/WRITING.md, whole;
  2. the kit, the helpers every class may import: engine/KIT.md, whole, when it is there;
  3. every library class (engine/library/<id>.py), by id: its kind and first line, then its
     `class Config` and `class Args` as written (the brief gives the ones picked for the job in
     full, and any other is one read away).

`vfg prefix` prints it. An implementer gets it after its rules.

Python 3.9+, standard library only, like toolkit/vfg.
"""
import os

import content

HEAD_HOW = "HOW A CONTENT CLASS IS WRITTEN"
HEAD_KIT = "THE KIT: the helpers every class may import"
HEAD_LIBRARY = ("THE LIBRARY — every library class, by id: its kind and first line, then its Config and Args as "
                "written. A class is engine/library/<id>.py; the ones picked for your job are in your first "
                "message, in full, and any other you can read.")
NO_LIBRARY = "THE LIBRARY: none yet. engine/examples/ holds four working classes to read."


def _text(path):
    """A file's text exactly, newline-terminated."""
    with open(path, "rb") as f:
        data = f.read().decode("utf-8", errors="replace")
    return data if data.endswith("\n") else data + "\n"


def library_block(repo):
    """Every library class: `=== fireball (ability) — Burns, then explodes ===`, then its Config and
    Args as written; a file that does not read says why."""
    lib = content.Content(repo, None)
    parts = []
    for c in lib._in(lib.library, "library"):
        if c.problem:
            parts.append("=== %s — not read: %s ===\n" % (c.rel, c.problem))
            continue
        head = "=== %s (%s) — %s ===\n" % (c.id, c.kind, c.first_line or "(no first line)")
        parts.append(head + "".join(x if x.endswith("\n") else x + "\n" for x in (c.config, c.args) if x))
    return parts


def build(repo):
    """The prefix, as `vfg prefix` prints it."""
    out = []
    writing = os.path.join(repo, "engine", "WRITING.md")
    if os.path.isfile(writing):
        out.append("%s\n=== engine/WRITING.md ===\n%s" % (HEAD_HOW, _text(writing)))
    kit = os.path.join(repo, "engine", "KIT.md")
    if os.path.isfile(kit):
        out.append("%s\n=== engine/KIT.md ===\n%s" % (HEAD_KIT, _text(kit)))
    classes = library_block(repo)
    out.append("%s\n%s" % (HEAD_LIBRARY, "".join(classes)) if classes else NO_LIBRARY + "\n")
    return "\n".join(out)
