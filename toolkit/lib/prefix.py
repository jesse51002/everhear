"""prefix: the part of an implementer's system prompt that is the same on every spawn.

The provider's cache reuses a prompt's start only when it is the same bytes,
so this is built from files that change only in a coding session, in a fixed
order, with nothing in it that changes between spawns — no date, no count, no
absolute path, nothing from made/ (what implementers make goes in each brief):

  1. how a script and a preset are written: scripts/README.md, whole;
  2. the kit, in full: scripts/_lib/mc.py, then every file in scripts/_lib/kit/
     by name;
  3. every library preset and example script, sorted by name: a preset's first
     line and values (its values class, as written; else its VALUES), a script's
     first line only, since the brief carries every action in full.

`vfg prefix` prints it. The orchestrator puts it after the implementer's rules.

Python 3.9+, standard library only, like toolkit/vfg.
"""
import json
import os

import route

HEAD_HOW = "HOW A SCRIPT AND A PRESET ARE WRITTEN"
HEAD_KIT = ("THE KIT, IN FULL — what every script and preset imports and nobody copies; "
            "it is here so you never open it")
HEAD_LIBRARY = ("THE LIBRARY — every preset and example script, by name: its first line, and a "
                "preset's values. A preset is scripts/_presets/<name>.py, an example script "
                "scripts/<name>/main.py, and its values are its action's (your brief has every "
                "action); the ones picked for your job are in your first message, in full, and any "
                "other you can read.")


def _text(path):
    """A file's text exactly, newline-terminated."""
    with open(path, "rb") as f:
        data = f.read().decode("utf-8", errors="replace")
    return data if data.endswith("\n") else data + "\n"


def _file(repo, rel):
    return "=== %s ===\n%s" % (rel, _text(os.path.join(repo, rel)))


def kit_files(repo):
    """mc.py, then every file in _lib/kit/ by name: no caches, no dotfiles, no subfolders."""
    rels = []
    if os.path.isfile(os.path.join(repo, "scripts", "_lib", "mc.py")):
        rels.append("scripts/_lib/mc.py")
    kit = os.path.join(repo, "scripts", "_lib", "kit")
    for fn in sorted(os.listdir(kit)) if os.path.isdir(kit) else []:
        if fn.startswith(".") or fn.endswith((".pyc", ".pyo")) or not os.path.isfile(os.path.join(kit, fn)):
            continue
        rels.append("scripts/_lib/kit/" + fn)
    return rels


def value_line(name, spec):
    """`crater_radius  int 2..64 = 8, live` — one VALUES entry, compact."""
    if not isinstance(spec, dict):
        return "  %s  %s" % (name, json.dumps(spec, ensure_ascii=False, sort_keys=True))
    kind = str(spec.get("type", "?"))
    if kind == "enum" and isinstance(spec.get("of"), (list, tuple)):
        kind += " " + "|".join(str(o) for o in spec["of"])
    elif kind == "list" and spec.get("of") is not None:
        of = spec["of"]
        kind += " of " + ("|".join(str(o) for o in of) if isinstance(of, (list, tuple)) else str(of))
    lo, hi = spec.get("min"), spec.get("max")
    if lo is not None and hi is not None:
        kind += " %s..%s" % (lo, hi)
    elif lo is not None:
        kind += " >=%s" % lo
    elif hi is not None:
        kind += " <=%s" % hi
    line = "  %s  %s = %s" % (name, kind, json.dumps(spec.get("default"), ensure_ascii=False, sort_keys=True))
    return line + (", live" if spec.get("live") else "")


def class_lines(preset):
    """`  crater_radius: Live[int] = Field(8, ge=2, le=64) — how wide the crater is, in blocks`:
    each field of a preset's values class as written, with the description under it."""
    import ast
    lines = []
    for node in preset.tree.body:
        if not route.library._values_class(node, preset.name):
            continue
        body = node.body
        for i, n in enumerate(body):
            if not isinstance(n, ast.AnnAssign):
                continue
            line = "  " + " ".join((ast.get_source_segment(preset.text, n) or "").split())
            nxt = body[i + 1] if i + 1 < len(body) else None
            if isinstance(nxt, ast.Expr) and isinstance(nxt.value, ast.Constant) and isinstance(nxt.value.value, str):
                line += " — " + " ".join(nxt.value.value.split())
            lines.append(line)
    return lines


def library_lines(repo):
    lib = route.library.Library(repo, "main")
    cards = [c for c in route.load_cards(repo, None) if c["source"] == "library"]
    cards.sort(key=lambda c: (c["name"], c["kind"] != "script"))
    out = []
    for c in cards:
        out.append("%s (%s)" % (c["name"], "example script" if c["kind"] == "script" else "preset"))
        out.append("  " + (c["first"] or "(no first line)"))
        if c["kind"] != "preset":
            continue
        it = lib.preset(c["name"])
        if it is not None and route.library.values_class_fields(it) is not None:
            out += class_lines(it)
        else:
            for name, spec in c["values"].items():
                out.append(value_line(name, spec))
    return out


def build(repo):
    """The prefix, as text: the same bytes for the same library files."""
    parts = [HEAD_HOW, ""]
    readme = "scripts/README.md"
    if os.path.isfile(os.path.join(repo, readme)):
        parts.append(_file(repo, readme))
    parts += [HEAD_KIT, ""]
    for rel in kit_files(repo):
        parts.append(_file(repo, rel))
    parts += [HEAD_LIBRARY, ""]
    parts += library_lines(repo)
    return "\n".join(parts) + "\n"
