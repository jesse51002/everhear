"""library.py: the script library and this server's folder, read on the PC.

Python 3.9+, standard library only. `vfg script new|run|set|save|list|check`,
`vfg preset save` and `vfg state` use it, and so do the router (route.py) and
the implementer's static prompt (prefix.py). Nothing here touches EC2.

Where things are (scripts/README.md):

    <repo>/scripts/<name>/main.py                 a library script (one written the old way has a
                                                  README.md and no VALUES)
    <repo>/scripts/_presets/<name>.py             a library preset
    <repo>/made/<server>/scripts/<name>/main.py   a script this server's implementers made
    <repo>/made/<server>/presets/<name>.py        a preset lifted out of one of them

Reading:

    lib = Library(repo, server)        server is VFG_SERVER ("main")
    lib.scripts()                      every script, the library's then the server's, each by name
    lib.presets()                      every preset, in the same order
    lib.script(name), lib.preset(name) the library's, else the server's, else None
    lib.taken(name, kind)              the Item already using that name, or None

    An Item is one script or preset:
      .name .kind ("script"|"preset") .source ("library"|"made")
      .path      the .py, absolute          .folder  a script's folder (None for a preset)
      .rel       the path from the repo, for showing: scripts/galick_gun/main.py
      .text      the whole source           .first_line  docstring line 1 (old-style: README line 1)
      .asks      ASKS as a list, or None    .values  VALUES as a dict, or None
      .holds     the presets a script holds, from its "# --- preset: <name>" markers
      .functions its top-level function names, in order
      .legacy    True when old-style        .problem  why it could not be read, or None

Values (the PC checks every value before anything is sent):

    given, notes = parse_given(item.values, ["cooldown=0", "player=Alex"])
    value, note = coerce(key, entry, raw)      raw is a word from the command line, or a
                                               typed value from JSON; numbers out of range
                                               are clamped and `note` says so; ValueProblem
                                               for anything else
    argv_word(value)                           the form a value takes on the box's command line
    defaults(values), differs(values, current)

Checking: check(item, lib) -> ["<rel>:<line>: <problem>", ...], empty when it passes.

Writing: render_values(...), replace_values(text, entries), add_preset(text, preset),
lift(item, function, name) build the text `vfg script new|save` and `vfg preset save`
write; vfg does the writing.
"""
import ast
import io
import json
import math
import os
import re
import sys
import tokenize

TYPES = ("int", "number", "bool", "enum", "str", "player", "block", "mob", "item",
         "particle", "sound", "list")
ENTRY_KEYS = ("type", "default", "min", "max", "of", "live")
SCRIPT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
PRESET_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
PLAYER_RE = re.compile(r"^[A-Za-z0-9_]{1,16}$")
# What kit.values (scripts/_lib/kit/values.py) takes for each kind of id, so the PC never
# sends a value the box would log and drop.
_ID = r"(?:[a-z0-9_.-]+:)?[a-z0-9_./-]+"
ID_RES = {
    "block": re.compile(r"^#?%s(\[[^\]]*\])?(\{.*\})?$" % _ID),
    "mob": re.compile(r"^%s$" % _ID),
    "item": re.compile(r"^#?%s(\[.*\])?$" % _ID),
    "particle": re.compile(r"^%s(\{.*\})?$" % _ID),
    "sound": re.compile(r"^%s$" % _ID),
}
SELECTORS = ("@a", "@r", "@p")
MARKER_RE = re.compile(r"^# --- preset: ([a-z][a-z0-9_]*)\b")
RESERVED_KEYS = ("as",)   # `as=<copy>` on vfg action run
BOOL_WORDS = {"true": True, "yes": True, "on": True, "1": True, "y": True,
              "false": False, "no": False, "off": False, "0": False, "n": False}
STDLIB = getattr(sys, "stdlib_module_names", None)   # 3.10+; on 3.9 imports go unchecked
# A preset's values class (class <CamelName>(Values), beside its function) and its imports
# are read on the PC only, by lib/py/vfg_actions.py: never copied into a script.
PC_ONLY = ("actions", "pydantic")
# What scripts/_lib deploys on the box beside the standard library: the kit, mc, and the
# saved places, the refused commands and the copies (the core scripts' places, commands and
# instances).
ON_THE_BOX = ("kit", "mc", "places", "commands", "instances")


class ValueProblem(Exception):
    """A value that does not fit its VALUES entry; the message is one line."""


# ------------------------------------------------------------------ reading

class Item(object):
    """One script or preset, read once; see the module docstring."""

    def __init__(self, name, kind, source, path, rel, folder=None):
        self.name, self.kind, self.source = name, kind, source
        self.path, self.rel, self.folder = path, rel, folder
        self.text = ""
        self.tree = None
        self.first_line = ""
        self.asks = None
        self.values = None
        self.values_node = None
        self.holds = []
        self.functions = []
        self.legacy = False
        self.readme_line = None
        self.problem = None

    def __repr__(self):
        return "<%s %s %s>" % (self.kind, self.source, self.name)


def parse(path, kind, name=None, source="library", rel=None):
    """Read one script's main.py or one preset's .py into an Item.

    Never raises for what is in the file: a file that is not Python 3.9 gets
    .problem, a VALUES that is not a literal leaves .values None."""
    folder = os.path.dirname(path) if kind == "script" else None
    if name is None:
        name = os.path.basename(folder) if kind == "script" else os.path.splitext(os.path.basename(path))[0]
    it = Item(name, kind, source, os.path.abspath(path), rel or path, folder)
    try:
        with open(path, encoding="utf-8") as f:
            it.text = f.read()
    except (OSError, UnicodeDecodeError) as e:
        it.problem = "cannot read it (%s)" % (getattr(e, "strerror", None) or e)
        return it
    if folder:
        readme = os.path.join(folder, "README.md")
        if os.path.isfile(readme):
            try:
                with open(readme, encoding="utf-8", errors="replace") as f:
                    it.readme_line = f.readline().strip()
            except OSError:
                it.readme_line = ""
    it.holds = []
    for line in it.text.splitlines():
        m = MARKER_RE.match(line)
        if m and m.group(1) not in it.holds:
            it.holds.append(m.group(1))
    try:
        it.tree = ast.parse(it.text, filename=it.rel, feature_version=(3, 9))
    except SyntaxError as e:
        it.problem = "line %s: not valid Python 3.9: %s" % (e.lineno, e.msg)
        it.legacy = it.readme_line is not None
        it.first_line = it.readme_line or ""
        return it
    body = it.tree.body
    doc = ast.get_docstring(it.tree, clean=True) if body and _is_doc(body[0]) else None
    if doc:
        it.first_line = doc.strip().splitlines()[0].strip()
    it.functions = [n.name for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for node in body:
        target = _assigned(node)
        if target == "ASKS":
            try:
                asks = ast.literal_eval(node.value)
                if isinstance(asks, (list, tuple)):
                    it.asks = list(asks)
            except (ValueError, SyntaxError, TypeError):
                pass
        elif target == "VALUES" and it.values_node is None:
            it.values_node = node.value
            try:
                values = ast.literal_eval(node.value)
                if isinstance(values, dict):
                    it.values = values
            except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
                pass
    it.legacy = it.readme_line is not None and it.values_node is None
    if it.legacy and not it.first_line:
        it.first_line = it.readme_line
    return it


def _is_doc(node):
    return (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str))


def _assigned(node):
    """The one plain name a top-level statement assigns, or None."""
    if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
        return node.targets[0].id
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
        return node.target.id
    return None


def load_gone(path):
    """The kit's GONE ({id: what to use instead}) from kit/world.py, read without running it."""
    try:
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in tree.body:
            if _assigned(node) == "GONE":
                gone = ast.literal_eval(node.value)
                return gone if isinstance(gone, dict) else {}
    except (OSError, SyntaxError, ValueError, TypeError):
        pass
    return {}


class Library(object):
    """The library in <repo>/scripts and this server's folder in <repo>/made/<server>."""

    def __init__(self, repo, server="main"):
        self.repo = repo
        self.server = server
        self.scripts_dir = os.path.join(repo, "scripts")
        self.presets_dir = os.path.join(self.scripts_dir, "_presets")
        self.made = os.path.join(repo, "made", server)
        self.made_scripts = os.path.join(self.made, "scripts")
        self.made_presets = os.path.join(self.made, "presets")
        self._cache = {}
        self.gone = load_gone(os.path.join(self.scripts_dir, "_lib", "kit", "world.py"))

    def rel(self, path):
        r = os.path.relpath(path, self.repo)
        return path if r.startswith("..") else r

    def _read(self, path, kind, name, source):
        try:
            st = os.stat(path)
            stamp = (st.st_mtime_ns, st.st_size)
        except OSError:
            stamp = None
        key = (path, kind)
        hit = self._cache.get(key)
        if hit and hit[0] == stamp:
            return hit[1]
        it = parse(path, kind, name, source, self.rel(path))
        self._cache[key] = (stamp, it)
        return it

    def _scripts_in(self, root, source):
        out = []
        try:
            names = sorted(os.listdir(root))
        except OSError:
            return out
        for name in names:
            main = os.path.join(root, name, "main.py")
            if name.startswith((".", "_")) or not SCRIPT_RE.match(name) or not os.path.isfile(main):
                continue
            out.append(self._read(main, "script", name, source))
        return out

    def _presets_in(self, root, source):
        out = []
        try:
            names = sorted(os.listdir(root))
        except OSError:
            return out
        for fn in names:
            name, ext = os.path.splitext(fn)
            path = os.path.join(root, fn)
            if ext != ".py" or not PRESET_RE.match(name) or not os.path.isfile(path):
                continue
            out.append(self._read(path, "preset", name, source))
        return out

    def scripts(self):
        """Every script: the library's by name, then this server's by name."""
        return self._scripts_in(self.scripts_dir, "library") + self._scripts_in(self.made_scripts, "made")

    def presets(self):
        """Every preset: the library's by name, then this server's by name."""
        return self._presets_in(self.presets_dir, "library") + self._presets_in(self.made_presets, "made")

    def script(self, name):
        """A script by name: the library's, else this server's, else None."""
        if not SCRIPT_RE.match(name or ""):
            return None
        for root, source in ((self.scripts_dir, "library"), (self.made_scripts, "made")):
            main = os.path.join(root, name, "main.py")
            if os.path.isfile(main):
                return self._read(main, "script", name, source)
        return None

    def preset(self, name):
        """A preset by name: the library's, else this server's, else None."""
        if not PRESET_RE.match(name or ""):
            return None
        for root, source in ((self.presets_dir, "library"), (self.made_presets, "made")):
            path = os.path.join(root, name + ".py")
            if os.path.isfile(path):
                return self._read(path, "preset", name, source)
        return None

    def taken(self, name, kind):
        """What already uses `name` for a script or a preset, in either place: a path, or None."""
        if kind == "script":
            for root in (self.scripts_dir, self.made_scripts):
                if os.path.lexists(os.path.join(root, name)):
                    return self.rel(os.path.join(root, name))
            return None
        for root in (self.presets_dir, self.made_presets):
            if os.path.lexists(os.path.join(root, name + ".py")):
                return self.rel(os.path.join(root, name + ".py"))
        return None

    def made_script_dir(self, name):
        return os.path.join(self.made_scripts, name)

    def made_preset_path(self, name):
        return os.path.join(self.made_presets, name + ".py")


# ------------------------------------------------------------------- values

def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _fmt(x):
    if isinstance(x, bool):
        return "true" if x else "false"
    if isinstance(x, float) and x.is_integer() and abs(x) < 1e15:
        return "%d" % x
    if isinstance(x, (list, dict)):
        return json.dumps(x, ensure_ascii=False, separators=(",", ":"))
    return "%s" % (x,)


def coerce(key, entry, raw, gone=None):
    """`raw` as the value `entry` declares: (value, note or None).

    `raw` is a word typed after a script's name, or a value read from JSON. A
    number out of range is clamped and the note says so; anything else that
    does not fit raises ValueProblem with one line saying what would. `gone` is
    the kit's ids that 26.3 no longer has (Library.gone)."""
    typ = entry.get("type")
    note = None
    if raw is None:
        return None, None
    word = isinstance(raw, str)
    if typ in ("int", "number"):
        if isinstance(raw, bool):
            raise ValueProblem("%s takes a number, not %s" % (key, _fmt(raw)))
        try:
            x = float(raw.strip()) if word else float(raw)
        except (ValueError, TypeError):
            raise ValueProblem("%s takes a %s, not %r" % (key, "whole number" if typ == "int" else "number",
                                                          raw))
        if math.isnan(x) or math.isinf(x):
            raise ValueProblem("%s takes a finite number" % key)
        if typ == "int" and not x.is_integer():
            raise ValueProblem("%s takes a whole number, not %s" % (key, _fmt(raw)))
        lo, hi = entry.get("min"), entry.get("max")
        if _num(lo) and x < lo:
            note = "%s=%s is below its min: clamped to %s" % (key, _fmt(x), _fmt(lo))
            x = float(lo)
        elif _num(hi) and x > hi:
            note = "%s=%s is above its max: clamped to %s" % (key, _fmt(x), _fmt(hi))
            x = float(hi)
        value = int(x) if (typ == "int" or x.is_integer()) else x
        return value, note
    if typ == "bool":
        if isinstance(raw, bool):
            return raw, None
        if word and raw.strip().lower() in BOOL_WORDS:
            return BOOL_WORDS[raw.strip().lower()], None
        raise ValueProblem("%s is true or false, not %r" % (key, raw))
    if typ == "enum":
        options = entry.get("of") or []
        for o in options:
            if raw == o or (word and raw == _fmt(o)):
                return o, None
        if word:
            low = [o for o in options if _fmt(o).lower() == raw.strip().lower()]
            if len(low) == 1:
                return low[0], None
        raise ValueProblem("%s is one of %s, not %r" % (key, ", ".join(_fmt(o) for o in options), raw))
    if typ == "list":
        items = raw
        if word:
            s = raw.strip()
            if s.startswith("["):
                try:
                    items = json.loads(s)
                except ValueError:
                    raise ValueProblem("%s is a list: a,b,c or JSON like [\"a\",\"b\"]" % key)
            else:
                items = [p.strip() for p in s.split(",") if p.strip()]
        if not isinstance(items, (list, tuple)):
            raise ValueProblem("%s is a list, not %r" % (key, raw))
        of = entry.get("of")
        out = []
        for i, item in enumerate(items):
            if isinstance(of, (list, tuple)):
                v, _ = coerce("%s[%d]" % (key, i), {"type": "enum", "of": list(of)}, item, gone)
            elif isinstance(of, str) and of in TYPES and of not in ("list", "enum"):
                v, _ = coerce("%s[%d]" % (key, i), {"type": of}, item, gone)
            else:
                v = item
            out.append(v)
        return out, None
    # The rest are words: str, and the names a command takes.
    if not isinstance(raw, str):
        raise ValueProblem("%s takes a word, not %s" % (key, _fmt(raw)))
    if any(ord(c) < 32 or ord(c) == 127 for c in raw):
        raise ValueProblem("%s holds no newline or control character" % key)
    if typ == "player":
        s = raw.strip()
        if s in SELECTORS:
            return s, None
        names = [n.strip() for n in s.split(",") if n.strip()]
        if names and all(PLAYER_RE.match(n) for n in names):
            return ",".join(names), None
        raise ValueProblem("%s is @a, @r, @p or player names separated by commas, not %r" % (key, raw))
    if typ in ID_RES:
        s = raw.strip() if typ in ("block", "item", "particle") else raw.strip().lower()
        if not ID_RES[typ].match(s):
            raise ValueProblem("%s is a %s id like minecraft:%s, not %r"
                               % (key, typ, {"block": "stone", "mob": "zombie", "item": "diamond_sword",
                                             "particle": "flame", "sound": "entity.wither.spawn"}[typ], raw))
        bare = s.split("[")[0].split("{")[0]
        short = bare.replace("minecraft:", "")
        tag = "#minecraft:" + bare.lstrip("#").replace("minecraft:", "") if bare.startswith("#") else None
        for gone_key in ((short,) if typ == "mob" else ()) + ((tag,) if tag else ()):
            if gone_key in (gone or {}):
                raise ValueProblem("%s: %s is gone in 26.3, use %s" % (key, raw, gone[gone_key]))
        return s, None
    return raw, None


def parse_given(values, words, gone=None):
    """Check `key=value` words against VALUES, in order: ({key: value}, [notes]).

    An unknown key, a word without `=` or a value that does not fit raises
    ValueProblem; a number out of range is clamped and a note says so."""
    given, notes = {}, []
    names = list((values or {}).keys())
    for w in words:
        if "=" not in w:
            raise ValueProblem("%r is not key=value; its values are %s" % (w, ", ".join(names) or "none"))
        key, raw = w.split("=", 1)
        key = key.strip()
        if key not in (values or {}):
            raise ValueProblem("%s is not one of its values: %s" % (key, ", ".join(names) or "none"))
        entry = values[key] if isinstance(values[key], dict) else {}
        value, note = coerce(key, entry, raw, gone)
        given[key] = value
        if note:
            notes.append(note)
    return given, notes


def argv_word(value):
    """A value as the box's command line carries it: `key=<this>`.

    Numbers as written, true/false, a list as compact JSON, words as they are;
    kit.values reads it back by the type its VALUES declares."""
    return _fmt(value)


def defaults(values):
    """{key: default} for every entry."""
    return {k: (e.get("default") if isinstance(e, dict) else None) for k, e in (values or {}).items()}


def differs(values, current):
    """The keys of `current` whose value is not its default, in VALUES order then the rest."""
    d = defaults(values)
    out = {}
    for k in list((values or {}).keys()) + [k for k in current if k not in (values or {})]:
        if k in current and (k not in d or current[k] != d[k]) and k not in out:
            out[k] = current[k]
    return out


def show(pairs):
    """{key: value} as `key=value key=value`."""
    return " ".join("%s=%s" % (k, _fmt(v)) for k, v in pairs.items())


# ----------------------------------------------------------------- checking

def short_forms(name):
    """The prefixes a preset's value and helper names may start with: its name,
    its first words, or the first three letters or more of its first word."""
    words = name.split("_")
    out = set("_".join(words[:i]) for i in range(1, len(words) + 1))
    out |= set(words[0][:i] for i in range(3, len(words[0]) + 1))
    return out


def prefixed(preset, key):
    return any(key.startswith(p + "_") and len(key) > len(p) + 1 for p in short_forms(preset))


def _reads(tree):
    """Every v["key"] and v.get("key") read: [(key, line)]."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "v":
            sl = node.slice
            if sl.__class__.__name__ == "Index":   # Python 3.8
                sl = sl.value
            if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                out.append((sl.value, node.lineno))
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and node.func.attr == "get" and isinstance(node.func.value, ast.Name)
              and node.func.value.id == "v" and node.args
              and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
            out.append((node.args[0].value, node.lineno))
    return out


def _fstring_problems(text):
    """f-strings Python 3.12 reads and 3.9 does not: a quote reused inside {},
    a backslash or a comment there. ast.parse(feature_version=(3, 9)) lets them by."""
    if not hasattr(tokenize, "FSTRING_START"):
        return []
    out, stack = [], []

    def quote(s):
        s = s.lstrip("rRbBuUfF")
        return s[:3] if s[:3] in ('"""', "'''") else s[:1]
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.FSTRING_START:
                q = quote(tok.string)
                if any(q[0] == o[0] for o in stack):
                    out.append((tok.start[0], "an f-string inside an f-string with the same quote: "
                                               "Python 3.9 cannot read it"))
                stack.append(q)
            elif tok.type == tokenize.FSTRING_END:
                if stack:
                    stack.pop()
            elif stack and tok.type == tokenize.STRING:
                if any(quote(tok.string)[0] == o[0] for o in stack):
                    out.append((tok.start[0], "a string inside an f-string's {} uses the f-string's "
                                              "quote: Python 3.9 cannot read it"))
                elif "\\" in tok.string:
                    out.append((tok.start[0], "a backslash inside an f-string's {}: Python 3.9 cannot "
                                              "read it"))
            elif stack and tok.type == tokenize.COMMENT:
                out.append((tok.start[0], "a comment inside an f-string: Python 3.9 cannot read it"))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    return out


def check_entry(key, entry, gone=None):
    """What is wrong with one VALUES entry: [message]."""
    if not isinstance(entry, dict):
        return ["%s: an entry is a dict like {\"type\": \"int\", \"default\": 1}" % key]
    out = []
    extra = [k for k in entry if k not in ENTRY_KEYS]
    if extra:
        out.append("%s: %s is not an entry key (%s)" % (key, ", ".join(map(str, extra)), ", ".join(ENTRY_KEYS)))
    typ = entry.get("type")
    if typ not in TYPES:
        out.append("%s: type %r is not one of %s" % (key, typ, ", ".join(TYPES)))
        return out
    if "default" not in entry:
        out.append("%s: no default" % key)
    if "live" in entry and not isinstance(entry["live"], bool):
        out.append("%s: live is True or False" % key)
    for lim in ("min", "max"):
        if lim in entry:
            if typ not in ("int", "number"):
                out.append("%s: %s goes with int and number, not %s" % (key, lim, typ))
            elif not _num(entry[lim]):
                out.append("%s: %s is a number" % (key, lim))
    if _num(entry.get("min")) and _num(entry.get("max")) and entry["min"] > entry["max"]:
        out.append("%s: min %s is above max %s" % (key, _fmt(entry["min"]), _fmt(entry["max"])))
    of = entry.get("of")
    if typ == "enum":
        if not isinstance(of, (list, tuple)) or not of:
            out.append("%s: an enum needs its options in \"of\"" % key)
            return out
    elif typ == "list":
        if of is not None and not (isinstance(of, (list, tuple)) and of) \
                and not (isinstance(of, str) and of in TYPES and of not in ("list", "enum")):
            out.append("%s: a list's \"of\" is its options, or one type like \"player\"" % key)
            return out
    elif "of" in entry:
        out.append("%s: \"of\" goes with enum and list, not %s" % (key, typ))
    if "default" in entry and entry["default"] is not None and not out:
        d = entry["default"]
        try:
            value, note = coerce(key, entry, d, gone)
            if note:
                out.append("%s: default %s is outside its limits (%s..%s)"
                           % (key, _fmt(d), _fmt(entry.get("min", "")), _fmt(entry.get("max", ""))))
            elif typ in ("int", "number") and not _num(d):
                out.append("%s: default %r is not a number" % (key, d))
            elif typ == "int" and not isinstance(d, int):
                out.append("%s: default %s is not a whole number" % (key, _fmt(d)))
        except ValueProblem as e:
            out.append("%s (its default)" % e)
    return out


def check(item, lib=None, fields=None):
    """Every problem with one script or preset, as "<rel>:<line>: <what>"; [] passes.

    A script ported to its action has no VALUES: `fields` are the fields of the actions over
    it, which its v[...] reads are checked against (None: no action runs it). A preset with a
    values class may have no VALUES either."""
    rel = item.rel
    out = []

    def bad(line, msg):
        out.append("%s:%s: %s" % (rel, line, msg))

    if item.problem:
        out.append("%s:%s" % (rel, item.problem[len("line "):]) if item.problem.startswith("line ")
                   else "%s: %s" % (rel, item.problem))
        return out
    tree = item.tree
    for line, msg in _fstring_problems(item.text):
        bad(line, msg)
    if item.legacy and item.source == "library":
        # Old-style: README.md beside it says what it does; it reads plain arguments.
        if not item.readme_line:
            bad(1, "its README.md's first line should say what it does")
        _check_imports(tree, bad)
        return out
    body = tree.body
    if not body or not _is_doc(body[0]):
        bad(1, "line 1 must be a docstring saying what it does in one line")
    elif not item.first_line:
        bad(1, "its docstring's first line is empty")
    elif item.first_line.upper().startswith("TODO"):
        bad(body[0].lineno, "its first line is still a TODO: say what it does")
    elif item.kind == "script" and not re.match(r"^[^:\n]{1,80}: \S", item.first_line):
        bad(body[0].lineno, "a script's first line is \"<Title>: what it does\"")
    if item.kind == "script":
        asks_node = next((n for n in body if _assigned(n) == "ASKS"), None)
        if asks_node is None:
            bad(1, "no ASKS: a list of what players said, in their own words")
        elif item.asks is None or not all(isinstance(a, str) and a.strip() for a in item.asks):
            bad(asks_node.lineno, "ASKS is a list of strings: what players said")
        elif not item.asks:
            bad(asks_node.lineno, "ASKS is empty: put what players said in it, in their own words")
    _check_imports(tree, bad, pc_only=item.kind == "preset")
    vnodes = [n for n in body if _assigned(n) == "VALUES"]
    declared = "VALUES does not declare it"
    if not vnodes and item.kind == "script" and fields is not None:
        values = dict((k, None) for k in fields)       # ported: its action's fields
        declared = "its action has no field of that name"
    elif not vnodes and item.kind == "preset" and values_class_fields(item) is not None:
        values = dict((k, None) for k in values_class_fields(item))
        declared = "its values class has no field of that name"
    elif not vnodes and item.kind == "script":
        bad(1, "no VALUES and no action runs it: a script's values are its action's fields "
               "(made/<server>/actions/<name>.py; vfg script new writes one)")
        values = {}
    elif not vnodes:
        bad(1, "no VALUES (VALUES = {} when it has none)")
        values = {}
    else:
        if len(vnodes) > 1:
            bad(vnodes[1].lineno, "VALUES is assigned twice")
        node = vnodes[0]
        values = item.values
        if values is None:
            bad(node.lineno, "VALUES is not a literal: write it out, with no names or calls in it")
            values = {}
        if isinstance(node.value, ast.Dict):
            seen = {}
            for k in node.value.keys:
                if k is None:
                    bad(node.lineno, "VALUES has a ** in it: write every entry out")
                    continue
                if not (isinstance(k, ast.Constant) and isinstance(k.value, str)):
                    continue
                if k.value in seen:
                    bad(k.lineno, "%s is in VALUES twice (lines %d and %d): two presets ask for one name"
                        % (k.value, seen[k.value], k.lineno))
                else:
                    seen[k.value] = k.lineno
        lines = _key_lines(node)
        for key, entry in values.items():
            line = lines.get(key, node.lineno)
            if not isinstance(key, str) or not KEY_RE.match(key):
                bad(line, "%r is not a value name (a-z, 0-9 and _, starting with a letter)" % (key,))
                continue
            if key in RESERVED_KEYS:
                bad(line, "%s is taken by vfg action run (as=<copy>)" % key)
            if item.kind == "preset" and not prefixed(item.name, key):
                bad(line, "%s does not start with the preset's name: %s_..." % (key, item.name))
            elif item.kind == "preset" and lib is not None:
                other = theirs(item.name, key, lib)
                if other:
                    bad(line, "%s starts with %s_, another preset's name: the two in one script would "
                              "ask for one value; use %s_... or a short form no other preset has"
                        % (key, other, item.name))
            for msg in check_entry(key, entry, lib.gone if lib is not None else None):
                bad(line, msg)
    for key, line in _reads(tree):
        if key not in values:
            bad(line, "v[%s] is read but %s" % (json.dumps(key), declared))
    if not vnodes and item.kind == "script":
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "VALUES" and isinstance(node.ctx, ast.Load):
                bad(node.lineno, "VALUES is used, but its values are its action's now: v = values(), "
                                 "and iterate v")
                break
    if item.kind == "preset":
        _check_preset_shape(item, bad)
    else:
        seen = {}
        for node in body:
            for name in _defines(node):
                if name in seen:
                    bad(node.lineno, "%s is defined twice (lines %d and %d): two presets with one name?"
                        % (name, seen[name], node.lineno))
                else:
                    seen[name] = node.lineno
    return out


def theirs(preset, key, lib):
    """Another preset whose name `key` starts with, so a script holding both could be asked
    for the same value twice (projectile_lands' projectile_kind is projectile's), or None."""
    for q in sorted(set(p.name for p in lib.presets()) - set([preset]), key=len, reverse=True):
        if key.startswith(q + "_") and (len(q) > len(preset) or not key.startswith(preset + "_")):
            return q
    return None


def _key_lines(node):
    lines = {}
    if isinstance(node.value, ast.Dict):
        for k in node.value.keys:
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                lines.setdefault(k.value, k.lineno)
    return lines


def _defines(node):
    """The names a top-level statement defines that two copied presets could clash on:
    functions, classes and UPPERCASE constants."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [node.name]
    name = _assigned(node)
    if name and name not in ("VALUES", "ASKS") and name.lstrip("_").isupper():
        return [name]
    return []


def _check_imports(tree, bad, pc_only=False):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods = [node.module or ""]
            if node.level:
                bad(node.lineno, "a relative import: the box runs main.py on its own")
                continue
        else:
            continue
        for mod in mods:
            parts = mod.split(".")
            if "_presets" in parts:
                bad(node.lineno, "imports from _presets: copy the preset in (vfg script new --preset)")
            elif parts[0] in ON_THE_BOX or (pc_only and parts[0] in PC_ONLY):
                continue
            elif STDLIB is not None and parts[0] not in STDLIB:
                bad(node.lineno, "imports %s, which is not on the box: kit, mc, the rest of scripts/_lib "
                                 "and the standard library only" % parts[0])


def _check_preset_shape(item, bad):
    name = item.name
    body = item.tree.body
    fn = next((n for n in body if isinstance(n, ast.FunctionDef) and n.name == name), None)
    if fn is None:
        bad(1, "no function named like the file: def %s(..., v)" % name)
    else:
        args = fn.args.posonlyargs + fn.args.args
        if not args or args[-1].arg != "v":
            bad(fn.lineno, "%s takes the values v as its last argument: def %s(..., v)" % (name, name))
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name != name \
                and not _values_class(node, name):
            if not (node.name.startswith("_") and prefixed(name, node.name[1:])):
                bad(node.lineno, "%s: a preset's helpers are private and carry its name: _%s_..."
                    % (node.name, name))


# ------------------------------------------------------------------ writing

def literal(x):
    """A Python literal for x, double-quoted, the way VALUES is written."""
    if isinstance(x, bool) or x is None:
        return repr(x)
    if isinstance(x, int):
        return repr(x)
    if isinstance(x, float):
        if math.isnan(x) or math.isinf(x):
            raise ValueError("not a literal: %r" % x)
        return repr(x)
    if isinstance(x, str):
        return json.dumps(x, ensure_ascii=False)
    if isinstance(x, (list, tuple)):
        return "[" + ", ".join(literal(i) for i in x) + "]"
    if isinstance(x, dict):
        return "{" + ", ".join("%s: %s" % (literal(k), literal(v)) for k, v in x.items()) + "}"
    raise ValueError("not a literal: %r" % (x,))


class Entry(object):
    """One VALUES entry as written: its key, its dict, and the comments around it."""

    def __init__(self, key, entry, before=None, after=None):
        self.key, self.entry = key, entry
        self.before = list(before or [])   # whole-line comments above it
        self.after = after                 # a comment at the end of its line


def read_entries(text, node):
    """The entries of a VALUES literal with their comments: ([Entry], closing comments)."""
    lines = text.splitlines()
    d = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else node
    entries, pending = [], []
    if not isinstance(d, ast.Dict):
        return entries, pending

    def comments(first, last):
        got = []
        for i in range(first, last + 1):
            if 1 <= i <= len(lines) and lines[i - 1].strip().startswith("#"):
                got.append(lines[i - 1].strip())
        return got
    open_rest = lines[d.lineno - 1][d.col_offset + 1:].strip() if d.lineno <= len(lines) else ""
    if open_rest.startswith("#"):
        pending.append(open_rest)
    prev_end = d.lineno
    pairs = list(zip(d.keys, d.values))
    for i, (k, val) in enumerate(pairs):
        key = ast.literal_eval(k) if k is not None else None
        try:
            entry = ast.literal_eval(val)
        except (ValueError, SyntaxError, TypeError):
            entry = None
        before = pending + comments(prev_end + 1, k.lineno - 1 if k is not None else val.lineno - 1)
        pending = []
        after = None
        nxt = pairs[i + 1][0] if i + 1 < len(pairs) else None
        if nxt is None or nxt.lineno != val.end_lineno:
            rest = lines[val.end_lineno - 1][val.end_col_offset:].strip()
            rest = rest.lstrip(",").strip()
            if rest.startswith("#"):
                after = rest
        entries.append(Entry(key, entry, before, after))
        prev_end = val.end_lineno
    pending = comments(prev_end + 1, d.end_lineno - 1)
    return entries, pending


def render_values(entries, closing=()):
    """`{...}` written out, one entry per line, keys lined up, comments kept."""
    if not entries and not closing:
        return "{}"
    width = max([len(literal(e.key)) + 1 for e in entries] or [0])
    out = ["{"]
    for e in entries:
        for c in e.before:
            out.append("    " + c)
        line = "    %s %s," % ((literal(e.key) + ":").ljust(width), literal(e.entry))
        if e.after:
            line += "  " + e.after
        out.append(line)
    for c in closing:
        out.append("    " + c)
    out.append("}")
    return "\n".join(out)


def values_node(text):
    """The top-level VALUES assignment of a source text, or None."""
    tree = ast.parse(text, feature_version=(3, 9))
    return next((n for n in tree.body if _assigned(n) == "VALUES"), None)


def replace_values(text, entries, closing=()):
    """`text` with its VALUES literal rewritten from entries (Entry list)."""
    node = values_node(text)
    if node is None:
        raise ValueError("no VALUES")
    v = node.value
    lines = text.splitlines(True)
    start = sum(len(l) for l in lines[:v.lineno - 1]) + _col(lines[v.lineno - 1], v.col_offset)
    end = sum(len(l) for l in lines[:v.end_lineno - 1]) + _col(lines[v.end_lineno - 1], v.end_col_offset)
    return text[:start] + render_values(entries, closing) + text[end:]


def _col(line, byte_col):
    """ast columns are UTF-8 byte offsets; this is the character offset."""
    return len(line.encode("utf-8")[:byte_col].decode("utf-8", errors="ignore"))


def _values_class(node, preset):
    """Is a top-level statement the preset's values class, class <CamelName>(Values)?"""
    return isinstance(node, ast.ClassDef) \
        and node.name == "".join(p[:1].upper() + p[1:] for p in preset.split("_")) \
        and any(getattr(b, "id", getattr(b, "attr", None)) == "Values" for b in node.bases)


def _pc_only(imp):
    """Is an import (imports_of form) one only the PC has (PC_ONLY)?"""
    kind, mod, names, _node = imp
    roots = [mod.split(".")[0]] if kind == "from" else [n.split(".")[0] for n, _a in names]
    return all(r in PC_ONLY for r in roots)


def preset_body(preset):
    """The code a preset gives a script: everything but its docstring, its imports, its
    VALUES and its values class (and the comments right above them), and any `if __name__`
    block."""
    tree = preset.tree
    lines = preset.text.splitlines()
    drop = set()
    for i, node in enumerate(tree.body):
        if (i == 0 and _is_doc(node)) or isinstance(node, (ast.Import, ast.ImportFrom)) \
                or _main_guard(node) or _assigned(node) == "VALUES" or _values_class(node, preset.name):
            first = node.lineno
            while first > 1 and lines[first - 2].strip().startswith("#"):
                first -= 1
            drop.update(range(first, node.end_lineno + 1))
    kept = [lines[n - 1].rstrip() for n in range(1, len(lines) + 1) if n not in drop]
    while kept and not kept[0].strip():
        kept.pop(0)
    while kept and not kept[-1].strip():
        kept.pop()
    # No more than two blank lines in a row, as in any module.
    out, blank = [], 0
    for l in kept:
        blank = blank + 1 if not l.strip() else 0
        if blank <= 2:
            out.append(l)
    return "\n".join(out)


def _main_guard(node):
    return (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__")


def imports_of(tree):
    """Top-level imports: [("from", module, [(name, asname)], node) or ("import", None, [...], node)]."""
    out = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and not node.level:
            out.append(("from", node.module, [(a.name, a.asname) for a in node.names], node))
        elif isinstance(node, ast.Import):
            out.append(("import", None, [(a.name, a.asname) for a in node.names], node))
    return out


def merge_imports(text, wanted):
    """`text` with every import in `wanted` (imports_of form) it lacks added: a name
    joins a one-line `from M import ...` already there, else a new line goes after
    the last top-level import. Returns (text, [the lines added or extended])."""
    added = []
    for kind, mod, names, _node in wanted:
        tree = ast.parse(text, feature_version=(3, 9))
        have = imports_of(tree)
        lines = text.splitlines(True)
        if kind == "import":
            for name, asname in names:
                bound = asname or name.split(".")[0]
                if any(k == "import" and any((a or n.split(".")[0]) == bound and n == name for n, a in ns)
                       for k, _m, ns, _ in have):
                    continue
                line = "import %s%s\n" % (name, " as " + asname if asname else "")
                text = _insert_import(text, tree, line)
                added.append(line.strip())
                tree = ast.parse(text, feature_version=(3, 9))
                have = imports_of(tree)
            continue
        missing = []
        for name, asname in names:
            if any(k == "from" and m == mod and (name, asname) in ns for k, m, ns, _ in have):
                continue
            missing.append((name, asname))
        if not missing:
            continue
        same = [n for k, m, ns, n in have if k == "from" and m == mod
                and n.lineno == n.end_lineno and "(" not in lines[n.lineno - 1]]
        piece = ", ".join(n + (" as " + a if a else "") for n, a in missing)
        if same:
            n = same[0]
            raw = lines[n.lineno - 1]
            body, nl = raw.rstrip("\r\n"), raw[len(raw.rstrip("\r\n")):]
            code, hash_, comment = body.partition("#")
            code = code.rstrip()
            lines[n.lineno - 1] = code + ", " + piece + ("  #" + comment if hash_ else "") + (nl or "\n")
            text = "".join(lines)
            added.append("from %s import %s" % (mod, piece))
        else:
            line = "from %s import %s\n" % (mod, piece)
            text = _insert_import(text, tree, line)
            added.append(line.strip())
    return text, added


def _insert_import(text, tree, line):
    lines = text.splitlines(True)
    imps = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    if imps:
        at = imps[-1].end_lineno
    else:
        at = 0
        for n in tree.body:
            if _is_doc(n) or _assigned(n) == "ASKS":
                at = n.end_lineno
            else:
                break
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    if not imps and at < len(lines) and lines[at].strip():
        line += "\n"   # the first import: a blank line between it and what follows
    lines.insert(at, line)
    return "".join(lines)


def _of_preset(name, preset):
    """Is a module-level name one of `preset`'s: _projectile_n, _CRATER_LOCK, damage_n?"""
    bare = name.lstrip("_").lower()
    return bare == preset or prefixed(preset, bare)


def _belongs(node, preset):
    """Is a top-level statement part of `preset`'s copied code: an assignment to its names
    (_projectile_n = [0]), or a call on one (_crater_queue.clear())?"""
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        targets = [node.target]
    elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
        f = node.value.func
        while isinstance(f, ast.Attribute):
            f = f.value
        targets = [f]
    else:
        return False
    names = [n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)]
    return bool(names) and all(_of_preset(n, preset) for n in names)


def wiring_line(text):
    """The line the wiring starts on, presets go above it: the first top-level statement
    after VALUES that is not a def, a class, an import or an UPPERCASE constant, nor part of
    the preset block it sits in (a `_<preset>...` variable under that preset's marker), with
    the comments right above it. None when there is none (the presets go at the end)."""
    tree = ast.parse(text, feature_version=(3, 9))
    lines = text.splitlines()
    markers = [(i + 1, MARKER_RE.match(l).group(1)) for i, l in enumerate(lines) if MARKER_RE.match(l)]
    # A script ported to its action has no VALUES: its wiring is after its docstring and ASKS.
    after_values = not any(_assigned(n) == "VALUES" for n in tree.body)
    for i, node in enumerate(tree.body):
        if _assigned(node) == "VALUES":
            after_values = True
            continue
        if not after_values or (i == 0 and _is_doc(node)):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom)):
            continue
        name = _assigned(node)
        if name and name.lstrip("_").isupper():
            continue   # the script's own constants stay above the presets
        above = [p for line, p in markers if line < node.lineno]
        if above and _belongs(node, above[-1]):
            continue   # a variable of the preset whose block this is
        at = node.lineno
        if getattr(node, "decorator_list", None):
            at = min(d.lineno for d in node.decorator_list)
        while at > 1 and lines[at - 2].strip().startswith("#") and not MARKER_RE.match(lines[at - 2].strip()):
            at -= 1
        return at
    return None


def marker(preset, server):
    where = "_presets/%s.py" % preset.name if preset.source == "library" \
        else "made/%s/presets/%s.py" % (server, preset.name)
    return "# --- preset: %s (copied from %s) ---" % (preset.name, where)


def add_preset(text, preset, server):
    """`text` (a script) with `preset` copied in: its imports merged, its VALUES entries
    appended to the script's VALUES, its code under its marker before the wiring.

    Returns (text, [the value names added], [the imports added]). ValueError when the
    script has no VALUES, already holds the preset, or a value name is in both."""
    node = values_node(text)
    if any(MARKER_RE.match(l) and MARKER_RE.match(l).group(1) == preset.name for l in text.splitlines()):
        raise ValueError("it already holds %s" % preset.name)
    pnode = next((n for n in preset.tree.body if _assigned(n) == "VALUES"), None)
    if node is None:
        # A script ported to its action: the preset's values go into the action, not here.
        text, imports = merge_imports(text, [i for i in imports_of(preset.tree) if not _pc_only(i)])
        keys = values_class_fields(preset)
        if keys is None:
            keys = [e.key for e in read_entries(preset.text, pnode)[0]] if pnode is not None else []
        return _add_block(text, preset, server), keys, imports
    entries, closing = read_entries(text, node)
    have = set(e.key for e in entries)
    new = read_entries(preset.text, pnode)[0] if pnode is not None else []
    clash = [e.key for e in new if e.key in have]
    if clash:
        raise ValueError("%s is in both its VALUES and %s's" % (", ".join(clash), preset.name))
    if new:
        new[0].before = list(closing) + new[0].before
        closing = []
    text = replace_values(text, entries + new, closing)
    text, imports = merge_imports(text, [i for i in imports_of(preset.tree) if not _pc_only(i)])
    return _add_block(text, preset, server), [e.key for e in new], imports


def _add_block(text, preset, server):
    """The preset's code under its marker, before the script's wiring."""
    block = marker(preset, server) + "\n" + preset_body(preset) + "\n"
    at = wiring_line(text)
    lines = text.splitlines(True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    if at is None:
        while lines and not lines[-1].strip():
            lines.pop()
        text = "".join(lines) + "\n\n" + block
    else:
        head = lines[:at - 1]
        while head and not head[-1].strip():
            head.pop()
        text = "".join(head) + "\n\n" + block + "\n\n" + "".join(lines[at - 1:])
    return text


def set_defaults(text, current):
    """`text` with each VALUES default in `current` ({key: value}) put in, comments kept."""
    node = values_node(text)
    if node is None:
        raise ValueError("no VALUES")
    entries, closing = read_entries(text, node)
    for e in entries:
        if e.key in current and isinstance(e.entry, dict):
            e.entry = dict(e.entry)
            e.entry["default"] = current[e.key]
    return replace_values(text, entries, closing)


SKELETON = '''"""TODO <Title>: say what it does in one line."""
ASKS = []
from kit import result, values

v = values()
'''
# The field a script made from nothing starts with, in its action.
SKELETON_FIELDS = [("player", {"type": "players", "default": "@a", "ge": None, "le": None, "choices": None,
                               "live": False, "description": "who it is for: a name, several separated by "
                                                             "commas, or @a"})]


# ------------------------------------------------------------------ the action

# What `vfg script new` writes beside a new script: made/<server>/actions/<name>.py, one class
# named as the file, inheriting the values class of every preset the script holds (preset(),
# in actions/_types.py) and holding a field for every other value the script reads. Each field
# is written from what actions.json says of one (type, default, ge, le, choices, live,
# description), or from a VALUES entry made into that shape.
TYPE_NAMES = {"player": "Player", "players": "Players", "mob": "MobId", "block": "BlockId", "item": "ItemId",
              "item or empty": "ItemIdOrEmpty", "particle": "ParticleId", "sound": "SoundId",
              "block mix": "BlockMix", "replace rules": "ReplaceRules", "item name": "ItemName",
              "place": "Place", "command": "Command", "int": "int", "number": "float", "bool": "bool", "str": "str"}
VALUES_KINDS = {"int": "int", "number": "number", "bool": "bool", "enum": "enum", "str": "str", "player": "players",
                "block": "block", "mob": "mob", "item": "item", "particle": "particle", "sound": "sound"}
TODO_LINE = "TODO: what it does, in players' words: its line on the menu."
TODO_FIELD = "TODO: what it is, in players' words"
TRIPLE = '"' * 3


def camel(name):
    """tnt_crouch -> TntCrouch: the class an action's file holds."""
    return "".join(p[:1].upper() + p[1:] for p in name.split("_"))


def values_class_fields(preset):
    """The field names of a preset's values class, class <CamelName>(Values), in order; None
    when it has none (its values are still only in its VALUES)."""
    for node in preset.tree.body if preset.tree is not None else []:
        if _values_class(node, preset.name):
            return [n.target.id for n in node.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)]
    return None


def field_of_entry(entry):
    """A VALUES entry as actions.json describes a field: {"type", "default", "ge", "le",
    "choices", "live", "description"}."""
    kind = entry.get("type")
    of = entry.get("of")
    info = {"default": entry.get("default"), "ge": entry.get("min"), "le": entry.get("max"),
            "choices": None, "live": bool(entry.get("live")), "description": ""}
    if kind == "enum":
        info.update(type="enum", choices=list(of or []))
    elif kind == "list":
        if isinstance(of, (list, tuple)):
            info.update(type="list of enum", choices=list(of))
        else:
            info["type"] = "list of " + ("player" if of == "player" else VALUES_KINDS.get(of, "str"))
    else:
        info["type"] = VALUES_KINDS.get(kind, "str")
    return info


def type_text(kind, choices=None):
    """A field's type as an action writes it, from its kind in one word (actions.json's)."""
    if kind == "enum" and choices:
        return "Literal[%s]" % ", ".join(literal(c) for c in choices)
    if kind.startswith("list of "):
        return "list[%s]" % type_text(kind[len("list of "):], choices)
    if kind == "list":
        return "list[str]"
    return TYPE_NAMES.get(kind, "str")


def field_lines(key, info):
    """`    cooldown: Live[float] = Field(2, ge=0, le=60)`, and its description under it."""
    t = type_text(info.get("type") or "str", info.get("choices"))
    if info.get("live"):
        t = "Live[%s]" % t
    default = literal(info.get("default"))
    limits = ["%s=%s" % (k, literal(info[k])) for k in ("ge", "le") if info.get(k) is not None]
    value = "Field(%s)" % ", ".join([default] + limits) if limits else default
    desc = " ".join(str(info.get("description") or "").split()) or TODO_FIELD
    return ["    %s: %s = %s" % (key, t, value), "    %s%s%s" % (TRIPLE, desc.replace(TRIPLE, "'''"), TRIPLE)]


def action_text(name, script, presets=(), fields=(), runs="until_stopped", takes=None, defaults=None,
                fixed=None, line=None):
    """The source of a new action: class <CamelName>(Action, preset("..."), ...) over `script`,
    with `fields` ([(key, info)]) written out. The implementer then edits its line, takes and
    descriptions."""
    used = set(["Action"])
    body = []
    for key, info in fields:
        lines = field_lines(key, info)
        for word in re.findall(r"[A-Za-z_]+", lines[0].split(":", 1)[1]):
            if word in ("Live", "Field", "Literal") or word in TYPE_NAMES.values():
                used.add(word)
        body += lines
    used -= set(["int", "float", "bool", "str"])
    if presets:
        used.add("preset")
    bases = ", ".join(["Action"] + ['preset("%s")' % p for p in presets])
    out = ["from actions._types import %s" % ", ".join(sorted(used)), "", "",
           "class %s(%s):" % (camel(name), bases),
           "    %s%s%s" % (TRIPLE, line or TODO_LINE, TRIPLE), "",
           '    script = "%s"' % script,
           '    runs = "%s"%s' % (runs, "" if runs == "once" else '  # or "once": a script that ends by itself'),
           "    takes = %s" % literal(list(takes or []))]
    if defaults:
        out.append("    defaults = %s" % literal(defaults))
    if fixed:
        out.append("    fixed = %s" % literal(fixed))
    if body:
        out += [""] + body
    return "\n".join(out) + "\n"


def strip_values(text):
    """A script ported to its action: no VALUES, and values() in place of values(VALUES)."""
    node = values_node(text)
    if node is None:
        return text
    lines = text.splitlines(True)
    first = node.lineno
    while first > 1 and lines[first - 2].strip().startswith("#"):
        first -= 1
    while first > 1 and not lines[first - 2].strip():
        first -= 1        # the blank lines above it go; those below it stay, before what follows
    text = "".join(lines[:first - 1] + lines[node.end_lineno:])
    return re.sub(r"\bvalues\(\s*VALUES\s*\)", "values()", text)


# ------------------------------------------------------------------ lifting

def _names_used(nodes):
    used = set()
    for top in nodes:
        for n in ast.walk(top):
            if isinstance(n, ast.Name):
                used.add(n.id)
    return used


def lift(item, function, name):
    """A preset's text lifted out of a script: `function` renamed `name`, the private
    helpers and top-level constants it uses, the imports they need, and the VALUES
    entries they read, renamed to the preset's prefix where they lack it.

    Returns (text, {old: new value names}, [helpers]). ValueError when it cannot."""
    tree = item.tree
    body = tree.body
    defs = {n.name: n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    if function not in defs:
        raise ValueError("%s has no top-level function %s (it has %s)"
                         % (item.name, function, ", ".join(defs) or "none"))
    consts = {}
    for n in body:
        t = _assigned(n)
        if t and t not in ("VALUES", "ASKS", "v"):
            consts[t] = n
    take, todo, public = [function], [function], []
    const_take = []
    while todo:
        f = todo.pop()
        for used in sorted(_names_used([defs[f]])):
            if used in defs and used not in take:
                if not used.startswith("_"):
                    public.append(used)
                    continue
                take.append(used)
                todo.append(used)
            elif used in consts and used not in const_take:
                const_take.append(used)
    if public:
        raise ValueError("%s calls %s, which is public in %s: lift it as its own preset first, "
                         "or make it private (_%s)" % (function, ", ".join(sorted(set(public))),
                                                       item.name, public[0]))
    nodes = [defs[f] for f in take]
    reads = []
    for n in nodes:
        for key, _line in _reads(n):
            if key not in reads:
                reads.append(key)
    values = item.values or {}
    missing = [k for k in reads if k not in values]
    if missing:
        raise ValueError("%s reads %s, which %s's VALUES does not declare"
                         % (function, ", ".join(missing), item.name))
    renames = {}
    for key in reads:
        renames[key] = key if prefixed(name, key) else "%s_%s" % (name, key)
    helper_names = {}
    for f in take[1:]:
        bare = f.lstrip("_")
        helper_names[f] = f if prefixed(name, bare) else "_%s_%s" % (name, bare)
    const_names = {}
    for c in const_take:
        const_names[c] = c
    lines = item.text.splitlines()
    chunks = []
    order = sorted([consts[c] for c in const_take], key=lambda n: n.lineno) \
        + sorted(nodes, key=lambda n: (n is not defs[function], n.lineno))
    for n in order:
        first = n.lineno
        if getattr(n, "decorator_list", None):
            first = min(first, min(d.lineno for d in n.decorator_list))
        while first > 1 and lines[first - 2].strip().startswith("#") \
                and not MARKER_RE.match(lines[first - 2].strip()):
            first -= 1
        chunks.append("\n".join(lines[first - 1:n.end_lineno]))
    code = "\n\n\n".join(chunks)
    names = dict(helper_names)
    names[function] = name
    code = _rename(code, names, renames)
    fn_node = defs[function]
    args = fn_node.args.posonlyargs + fn_node.args.args
    if not args or args[-1].arg != "v":
        code = _add_v(code, name)
    # Imports: the script's own, cut to the names this code uses.
    used = _names_used([ast.parse(code, feature_version=(3, 9))])
    imp_lines = []
    for kind, mod, ns, _node in imports_of(tree):
        keep = [(n, a) for n, a in ns if (a or n.split(".")[0]) in used]
        if not keep:
            continue
        if kind == "from":
            imp_lines.append("from %s import %s" % (mod, ", ".join(n + (" as " + a if a else "") for n, a in keep)))
        else:
            for n, a in keep:
                imp_lines.append("import %s%s" % (n, " as " + a if a else ""))
    doc = ast.get_docstring(fn_node)
    first = doc.strip().splitlines()[0].strip() if doc and doc.strip() else \
        "TODO: say what %s does in one line (lifted from %s.%s)." % (name, item.name, function)
    entries = [Entry(renames[k], values[k]) for k in reads]
    text = '"""%s"""\n' % first.replace('"""', "'''")
    if imp_lines:
        text += "\n".join(imp_lines) + "\n"
    text += "\nVALUES = " + render_values(entries) + "\n\n\n" + code.rstrip() + "\n"
    return text, renames, [helper_names[f] for f in take[1:]]


def _rename(code, names, keys):
    """`code` with function names renamed (a NAME token not after a dot) and the
    strings in v["key"] and v.get("key") renamed, by position, layout kept."""
    toks = list(tokenize.generate_tokens(io.StringIO(code + "\n").readline))
    edits = []
    for i, tok in enumerate(toks):
        prev = toks[i - 1] if i else None
        if tok.type == tokenize.NAME and tok.string in names and not (prev and prev.string == "."):
            if names[tok.string] != tok.string:
                edits.append((tok.start, tok.end, names[tok.string]))
        elif tok.type == tokenize.STRING and prev is not None:
            back = [t.string for t in toks[max(0, i - 4):i]]
            if back[-2:] == ["v", "["] or back[-4:] == ["v", ".", "get", "("]:
                try:
                    val = ast.literal_eval(tok.string)
                except (ValueError, SyntaxError):
                    val = None
                if isinstance(val, str) and keys.get(val, val) != val:
                    edits.append((tok.start, tok.end, json.dumps(keys[val])))
    lines = (code + "\n").splitlines(True)
    for (r0, c0), (r1, c1), new in sorted(edits, reverse=True):
        if r0 == r1:
            lines[r0 - 1] = lines[r0 - 1][:c0] + new + lines[r0 - 1][c1:]
    return "".join(lines)[:-1] if not code.endswith("\n") else "".join(lines)


def _add_v(code, name):
    """`def name(...)` with `v` added as its last parameter; ValueError when it cannot go last."""
    m = re.search(r"\bdef\s+%s\s*\(" % re.escape(name), code)
    if not m:
        return code
    depth, i = 1, m.end()
    while i < len(code) and depth:
        if code[i] == "(":
            depth += 1
        elif code[i] == ")":
            depth -= 1
        i += 1
    close = i - 1
    inside = code[m.end():close]
    if "*" in inside or "=" in inside:
        raise ValueError("%s has defaults or *args, so v cannot go last: give it v as its last "
                         "parameter first" % name)
    if not inside.strip():
        new = "v"
    elif inside.rstrip().endswith(","):
        new = inside.rstrip() + " v"
    else:
        new = inside.rstrip() + ", v"
    return code[:m.end()] + new + code[close:]
