"""Incantations: phrases bound to an action for a player, and the plain matching that finds them.

Python 3.9+, standard library only. On the box the bridge imports it (Layer 2 on each line, the
drop rule, the cast endpoint's lookup) and so does the incantations script (the store); on the
PC vfg imports the same file for the bind checks and the code aliases.

    store = incantations.Store()           # /opt/vfg/incantations.json, or store_path()
    entry, replaced = store.put({"player": "Steve", "phrase": "fire ball", "action": ...})
    store.for_player("Steve")              # Steve's own, then the @a ones
    store.lookup("Steve", "fireball")      # by phrase or alias, as the spotter heard it

    incantations.match_line("uh come a hammer ha", entries)   # Layer 2: [Match], best first
    incantations.drop("fire ball now", entry)                 # the drop rule: True
    incantations.bind_warnings("fire ball", aliases, others)  # ['"fire ball" sounds like ...']

Words are normalised one way everywhere (normalize): lowercase, accents and apostrophes
dropped (don't -> dont), every other mark, hyphen and underscore a space.

Phonemes come from CMUdict (cmudict.gz beside this file, its licence in cmudict.LICENSE): the
first pronunciation of each word, stress removed, keys normalised as above. A word it lacks goes
through small English letter-to-sound rules (_rules), which only need to be consistent: a phrase
and a transcript go through the same function. The dictionary is one sorted text kept in memory
as it is (about 2.9 MB) and searched by bisection, read on first use or by warm().

Layer 2 (match_line) aligns each target's phonemes whole against any run of the line's, with
word boundaries ignored, so "come a hammer ha" lines up with "kamehameha". The cost of one edit
(_sub_cost, _indel_cost) is 1, except:
    0.3  a vowel for another vowel (ER counts as one), a voiced consonant for its unvoiced pair
         (B/P D/T G/K V/F Z/S DH/TH ZH/SH JH/CH), ER for R, N for NG
    0.6  a consonant for another of its kind (stops, fricatives, nasals, liquids, glides)
    0.25 an HH put in or left out; 0.5 an AH (the unstressed "uh")
and the distance is the cost over the target's phoneme count: 0 is exact, 0.25 the default
allowance (one consonant wrong in "fire ball", or four vowels in "kamehameha").

The store, /opt/vfg/incantations.json (store_path), is written whole: a temp file then
os.replace, under the flock incantations.lock beside it, every write bumping `version`.
"""
import fcntl
import gzip
import json
import os
import re
import time
import unicodedata
from collections import namedtuple
from datetime import datetime, timezone
from functools import lru_cache

HERE = os.path.dirname(os.path.abspath(__file__))
DICT_PATH = os.environ.get("VFG_CMUDICT") or os.path.join(HERE, "cmudict.gz")

FILLERS = frozenset("uh um er ah oh hmm mm".split())
ALLOWANCE = 0.25          # match_line's default: Layer 2 on a line
DROP_ALLOWANCE = 0.5      # the drop rule's: the spotter already heard it
DROP_SPAN_RATIO = (0.6, 1.6)   # the drop rule's phoneme match: its run's phonemes over the target's
DROP_SPAN_WORDS = 1       # ... and at most the target's words + 1, unless within ALLOWANCE
# A line with one of these left beside the phrase is never dropped: it asks something of the spell.
KEEP_WORDS = frozenset("stop remove cancel delete unbind dont not no never undo end".split())
SOUNDS_LIKE = 0.34        # bind_warnings: a phrase or alias this close to another's
SHORT_PHONES = 6          # bind_warnings: a phrase with fewer is short
MIN_ALIAS_PHONES = 4      # merge_aliases drops a shorter alias
ALIAS_CAP = 12
MAX_PHRASE = 80           # characters, normalised
MAX_LINE_WORDS = 200      # match_line reads the first 200 words of a line (about 1000 characters)
SPAN_PHONES, SPAN_SLACK = 3, 0.1   # _best_end: a match's end may move this far for this much more
EPS = 1e-9                # sums of 0.3s are not exact: a distance on the allowance is within it
DEFAULT_COOLDOWN_S = 3.0
PLAYER_RE = re.compile(r"^[A-Za-z0-9_]{1,16}$")
EVERYONE = "@a"


# ------------------------------------------------------------------ words

def normalize(text):
    """The drop rule's normalisation: lowercase, accents and apostrophes dropped, every other
    mark (hyphens and underscores too) a space, whitespace collapsed."""
    s = unicodedata.normalize("NFKD", str(text or "")).lower()
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"['\u2018\u2019]", "", s)
    return " ".join(re.sub(r"[\W_]+", " ", s).split())


def words(text):
    return normalize(text).split()


def check_phrase(phrase):
    """The phrase normalised; ValueError unless it is two words at least."""
    p = normalize(phrase)
    if len(p.split()) < 2:
        raise ValueError("%r is not an incantation: a phrase is two words at least" % (phrase,))
    if len(p) > MAX_PHRASE:
        raise ValueError("%r is too long for an incantation: %d characters at most" % (phrase, MAX_PHRASE))
    return p


def split_players(player):
    """"Steve, Alex" -> ["Steve", "Alex"]; "@a" -> ["@a"] (and so does any list with @a in it).
    ValueError on anything else: an incantation is for named players or @a."""
    out, seen = [], set()
    for p in str(player or "").split(","):
        p = p.strip()
        if not p:
            continue
        if p.lower() == EVERYONE:
            return [EVERYONE]
        if not PLAYER_RE.match(p):
            raise ValueError("%r is not a player: a name (1 to 16 of A-Z, a-z, 0-9 and _), "
                             "several separated by commas, or @a" % p)
        if p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    if not out:
        raise ValueError("an incantation needs a player: a name, several separated by commas, or @a")
    return out


def applies_to(entry, name):
    """True when the entry is for everyone (@a) or for `name` (case-insensitive)."""
    p = str(entry.get("player") or "")
    return p == EVERYONE or p.lower() == str(name or "").lower()


def store_path():
    """VFG_INCANTATIONS, else incantations.json beside VFG_RUN_DIR (the box's root, as
    instances.here() finds it), else /opt/vfg/incantations.json."""
    if os.environ.get("VFG_INCANTATIONS"):
        return os.environ["VFG_INCANTATIONS"]
    run = os.environ.get("VFG_RUN_DIR")
    if run:
        return os.path.join(os.path.dirname(run.rstrip("/")), "incantations.json")
    return "/opt/vfg/incantations.json"


def entry_id(player, phrase):
    """steve.fire_ball; @a.fire_ball."""
    return "%s.%s" % (str(player).lower(), normalize(phrase).replace(" ", "_"))


def targets(entry):
    """The entry's phrase and aliases, normalised, each once."""
    out = []
    for t in [entry.get("phrase")] + list(entry.get("aliases") or []):
        n = normalize(t)
        if n and n not in out:
            out.append(n)
    return out


# ------------------------------------------------------------------ the store

class StoreError(Exception):
    """The store's file is there but cannot be read, so it is not changed; the message names it."""


def now_iso(t=None):
    d = datetime.fromtimestamp(time.time() if t is None else t, timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (d.microsecond // 1000)


class Store(object):
    """The box's incantations, one JSON file: {"version": n, "incantations": [entry, ...]}.
    Reads take no lock (a write replaces the file whole); every change is a read, change and
    write under incantations.lock."""

    def __init__(self, path=None):
        self.path = path or store_path()
        self.lock_path = os.path.join(os.path.dirname(os.path.abspath(self.path)), "incantations.lock")

    def read(self):
        """The whole file; {"version": 0, "incantations": []} when there is none, and when it
        cannot be read (a change refuses instead: _read_strict)."""
        try:
            return self._read_strict()
        except StoreError:
            return {"version": 0, "incantations": []}

    def _read_strict(self):
        """The file as a change reads it: empty only when there is no file; StoreError when it
        is there but unreadable, so a change never writes an empty store over it."""
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return {"version": 0, "incantations": []}
        except (OSError, ValueError) as e:
            raise StoreError("%s cannot be read (%s): fix or move it; nothing was changed" % (self.path, e))
        items = data.get("incantations") if isinstance(data, dict) else None
        if not (isinstance(items, list) and all(isinstance(e, dict) for e in items)
                and isinstance(data.get("version"), int)):
            raise StoreError('%s is not {"version": n, "incantations": [entry, ...]}: fix or move it; '
                             "nothing was changed" % self.path)
        return {"version": data["version"], "incantations": items}

    def version(self):
        return self.read()["version"]

    def for_player(self, name):
        """The entries that apply to `name`: their own first, then the @a ones."""
        items = self.read()["incantations"]
        own = [e for e in items if e.get("player") != EVERYONE and applies_to(e, name)]
        return own + [e for e in items if e.get("player") == EVERYONE]

    def find(self, player, phrase):
        """The entry under exactly this player key (case-insensitive) and phrase, or None."""
        return _find(self.read()["incantations"], player, phrase)[1]

    def lookup(self, name, heard):
        """The entry of `name`'s whose phrase or an alias, normalised, is `heard`: their own
        before an @a one; None when there is none."""
        h = normalize(heard)
        if not h:
            return None
        for e in self.for_player(name):
            if h in targets(e):
                return e
        return None

    def put(self, entry):
        """Store an entry, replacing the one with the same player and phrase: (the entry as
        stored, replaced?). The phrase is normalised and the id filled; ValueError for a bad
        player (one name or @a: split_players first) or phrase."""
        e = dict(entry)
        player = str(e.get("player") or "").strip()
        if player.lower() == EVERYONE:
            player = EVERYONE
        elif not PLAYER_RE.match(player):
            raise ValueError("%r is not one player: a name, or @a" % (e.get("player"),))
        e["player"] = player
        e["phrase"] = check_phrase(e.get("phrase"))
        e["id"] = entry_id(player, e["phrase"])
        e.setdefault("aliases", [])
        e.setdefault("cooldown_s", DEFAULT_COOLDOWN_S)
        e.setdefault("created", now_iso())
        with _Change(self) as ch:
            i, old = _find(ch.items, player, e["phrase"])
            if old is None:
                ch.items.append(e)
            else:
                ch.items[i] = e
        return dict(e), old is not None

    def update(self, player, phrase, merge_values=False, **changes):
        """Change fields of one entry (values, cooldown_s, aliases ...): the entry as stored.
        With merge_values, a values change is merged over the values the store holds as it
        writes, under its lock, so two edits of different values close together both land.
        KeyError when there is none; ValueError for a change to id, player or phrase."""
        bad = sorted(set(changes) & {"id", "player", "phrase"})
        if bad:
            raise ValueError("an incantation's %s cannot change: remove it and create it again" % bad[0])
        with _Change(self) as ch:
            i, old = _find(ch.items, player, phrase)
            if old is None:
                raise KeyError("%s has no incantation %r" % (player, normalize(phrase)))
            new = dict(old)
            new.update(changes)
            if merge_values and isinstance(changes.get("values"), dict):
                new["values"] = dict(old.get("values") or {}, **changes["values"])
            ch.items[i] = new
        return dict(new)

    def remove(self, player, phrase):
        """Remove one entry: the entry removed, or None (and no write) when there was none."""
        with _Change(self) as ch:
            i, old = _find(ch.items, player, phrase)
            if old is None:
                ch.write = False
            else:
                del ch.items[i]
        return old


class _Change(object):
    """Read, change, write: under incantations.lock the file is read, its entries handed out
    (.items) to change, and written back with version + 1, unless .write was set False or the
    change raised."""

    def __init__(self, store):
        self.store = store
        self.write = True

    def __enter__(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.store.path)), exist_ok=True)
        self.fd = os.open(self.store.lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        try:
            self.data = self.store._read_strict()
        except StoreError:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            raise
        self.items = self.data["incantations"]
        return self

    def __exit__(self, kind, *exc):
        try:
            if kind is None and self.write:
                self.data["version"] += 1
                _write_json(self.store.path, self.data)
        finally:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)


def _find(items, player, phrase):
    key = str(player or "").strip().lower()
    p = normalize(phrase)
    for i, e in enumerate(items):
        if str(e.get("player") or "").lower() == key and normalize(e.get("phrase")) == p:
            return i, e
    return -1, None


def _write_json(path, data):
    """Atomically: a temp file beside it, synced to disk, then os.replace."""
    tmp = "%s.tmp.%d" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


# ------------------------------------------------------------------ phonemes

PHONES = ("AA AE AH AO AW AY EH ER EY IH IY OW OY UH UW "
          "B CH D DH F G HH JH K L M N NG P R S SH T TH V W Y Z ZH").split()
PHONE_ID = dict((p, i) for i, p in enumerate(PHONES))
VOWELS = frozenset(PHONES[:15])
_KIND = {}
for _k, _ps in (("stop", "P B T D K G"), ("affricate", "CH JH"), ("fricative", "F V TH DH S Z SH ZH"),
                ("nasal", "M N NG"), ("liquid", "L R"), ("glide", "W Y"), ("aspirate", "HH")):
    for _p in _ps.split():
        _KIND[_p] = _k
_PAIRS = set()
for _a, _b in (("B", "P"), ("D", "T"), ("G", "K"), ("V", "F"), ("Z", "S"), ("DH", "TH"), ("ZH", "SH"),
               ("JH", "CH"), ("ER", "R"), ("N", "NG")):
    _PAIRS.update(((_a, _b), (_b, _a)))


def _sub_cost(a, b):
    if a == b:
        return 0.0
    if (a in VOWELS and b in VOWELS) or (a, b) in _PAIRS:
        return 0.3
    if a not in VOWELS and b not in VOWELS and _KIND[a] == _KIND[b]:
        return 0.6
    return 1.0


def _indel_cost(p):
    return {"HH": 0.25, "AH": 0.5}.get(p, 1.0)


# _SUB[a][b] and _INDEL[a], by phone id.
_SUB = [[_sub_cost(a, b) for b in PHONES] for a in PHONES]
_INDEL = [_indel_cost(p) for p in PHONES]

_dict = None              # the dictionary's text, bytes: "word PH ON ES\n", sorted by word


def warm():
    """Read the dictionary now (the bridge calls this as it starts), not on first use."""
    _load()


def _load():
    global _dict
    if _dict is None:
        try:
            with open(DICT_PATH, "rb") as f:
                _dict = gzip.decompress(f.read())
        except OSError:
            _dict = b""   # no dictionary: every word goes through the rules
    return _dict


def build_dictionary(src, out=DICT_PATH):
    """Make cmudict.gz from CMUdict's own cmudict.dict (the cmusphinx/cmudict repo, or PyPI's
    cmudict package, data/cmudict.dict): each word's first pronunciation, stress removed, its key
    normalised (apostrophes dropped, a word with any other mark left out, the spelling without
    an apostrophe kept over one with), sorted. Returns how many words it holds.

        python3 -c 'import incantations; incantations.build_dictionary("cmudict.dict")'
    """
    plain, apos = {}, {}
    with open(src, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            word, _, pron = line.partition(" ")
            if not pron or re.search(r"\(\d+\)$", word):
                continue      # a comment, or a second pronunciation
            key = word.lower().replace("'", "")
            if re.match(r"^[a-z0-9]+$", key):
                (apos if "'" in word else plain).setdefault(key, " ".join(re.sub(r"\d", "", p) for p in pron.split()))
    for k, v in apos.items():
        plain.setdefault(k, v)
    body = "".join("%s %s\n" % (k, plain[k]) for k in sorted(plain))
    with open(out, "wb") as f, gzip.GzipFile("", "wb", 9, f, mtime=0) as z:
        z.write(body.encode("ascii"))   # no name or time in the header: the same bytes every build
    return len(plain)


def _cmu(word):
    """CMUdict's phonemes for one normalised word, as a space-joined string, or None."""
    text = _load()
    try:
        key = word.encode("ascii")
    except UnicodeEncodeError:
        return None
    lo, hi = 0, len(text)
    while lo < hi:   # lo is always a line's start; every line before it sorts below key
        mid = (lo + hi) // 2
        s = text.rfind(b"\n", lo, mid) + 1 or lo
        e = text.find(b" ", s)
        k = text[s:e]
        if k == key:
            return text[e + 1:text.find(b"\n", e)].decode("ascii")
        if k < key:
            lo = text.find(b"\n", e) + 1
        else:
            hi = s
    return None


_DIGITS = "zero one two three four five six seven eight nine".split()


@lru_cache(maxsize=8192)
def _word_ids(word):
    """One normalised word's phonemes, as a tuple of phone ids."""
    found = _cmu(word)
    if found is not None:
        return tuple(PHONE_ID[p] for p in found.split())
    out = []
    for run in re.findall(r"[0-9]+|[^0-9]+", word):
        if run.isdigit():
            for d in run:
                out.extend(_word_ids(_DIGITS[int(d)]))
        else:
            found = _cmu(run)
            out.extend(PHONE_ID[p] for p in (found.split() if found is not None else _rules(run)))
    return tuple(out)


def phonemes(word):
    """CMUdict's first pronunciation of a word, stress removed; else the letter-to-sound rules'.
    A word that normalises to several ("fire-ball") gives them all, run together."""
    return [PHONES[i] for w in words(word) for i in _word_ids(w)]


def phones(text):
    """Every word's phonemes, joined, with no word boundaries."""
    return [PHONES[i] for w in words(text) for i in _word_ids(w)]


@lru_cache(maxsize=4096)
def _target_ids(text):
    """A normalised target's phone ids, as a tuple."""
    return tuple(i for w in text.split() for i in _word_ids(w))


# ---- the letter-to-sound rules, for words CMUdict lacks

_LV = frozenset("aeiouy")
_START, _END, _CONS, _ANY = "start", "end", "cons", "any"
# (letters, phonemes, where): tried longest first at each place; where is _START (only at the
# word's start), _END (only at its end), _CONS (before a consonant or the end) or _ANY.
_GRAPHEMES = [
    ("tion", "SH AH N", _ANY), ("sion", "ZH AH N", _ANY), ("eigh", "EY", _ANY), ("ough", "AO", _ANY),
    ("augh", "AO", _ANY), ("tch", "CH", _ANY), ("sch", "S K", _ANY), ("igh", "AY", _ANY),
    ("ch", "CH", _ANY), ("sh", "SH", _ANY), ("th", "TH", _ANY), ("ph", "F", _ANY), ("wh", "W", _ANY),
    ("ck", "K", _ANY), ("ng", "NG", _ANY), ("qu", "K W", _ANY), ("kn", "N", _START), ("wr", "R", _START),
    ("gn", "N", _START), ("gn", "N", _END), ("mb", "M", _END), ("gh", "G", _START), ("gh", "", _ANY),
    ("ee", "IY", _ANY), ("ea", "IY", _ANY), ("oo", "UW", _ANY), ("ou", "AW", _ANY), ("ow", "OW", _ANY),
    ("oa", "OW", _ANY), ("ai", "EY", _ANY), ("ay", "EY", _ANY), ("ey", "IY", _END), ("ei", "EY", _ANY),
    ("ey", "EY", _ANY), ("oi", "OY", _ANY), ("oy", "OY", _ANY), ("au", "AO", _ANY), ("aw", "AO", _ANY),
    ("ew", "UW", _ANY), ("ue", "UW", _ANY), ("ui", "UW", _ANY), ("ie", "IY", _ANY),
    ("ar", "AA R", _CONS), ("er", "ER", _CONS), ("ir", "ER", _CONS), ("ur", "ER", _CONS),
    ("or", "AO R", _CONS), ("ah", "AA", _CONS), ("eh", "EH", _CONS), ("oh", "OW", _CONS),
    ("uh", "AH", _CONS),
]
_LONG = {"a": "EY", "e": "IY", "i": "AY", "o": "OW", "u": "UW"}
_SHORT = {"a": "AE", "e": "EH", "i": "IH", "o": "AA", "u": "AH"}
_FINAL = {"a": "AH", "e": "IY", "i": "IY", "o": "OW", "u": "UW"}
_PLAIN = {"b": "B", "d": "D", "f": "F", "j": "JH", "k": "K", "l": "L", "m": "M", "n": "N", "p": "P",
          "q": "K", "r": "R", "t": "T", "v": "V", "w": "W", "z": "Z"}


def _rules(w):
    """English letter-to-sound, roughly: kamehameha -> K AE M EH HH AE M EH HH AH. Letters
    other than a-z are skipped."""
    w = re.sub(r"[^a-z]", "", w)
    n = len(w)
    out = []
    i = 0
    while i < n:
        for letters, ph, where in _GRAPHEMES:
            j = i + len(letters)
            if w.startswith(letters, i) and (
                    where == _ANY or (where == _START and i == 0) or (where == _END and j == n)
                    or (where == _CONS and (j == n or w[j] not in _LV))):
                out.extend(ph.split())
                i = j
                break
        else:
            out.extend(_letter(w, i, n))
            i += 1
    return out


def _letter(w, i, n):
    c = w[i]
    prev = w[i - 1] if i else ""
    nxt = w[i + 1] if i + 1 < n else ""
    if c in "aeiou":
        if c == "e" and i == n - 1 and i > 0:
            # a final e: silent after another vowel's syllable (fire, game), else "ee" (ke)
            return [] if any(ch in _LV for ch in w[:i]) else ["IY"]
        if i + 3 == n and w[n - 1] == "e" and nxt not in _LV:
            return [_LONG[c]]     # a magic e: game, bike
        if i == n - 1:
            return [_FINAL[c]]
        return [_SHORT[c]]
    if c == "y":
        if i == 0 or (nxt and nxt in "aeiou"):
            return ["Y"]
        if i == n - 1:
            return ["IY"] if any(ch in _LV for ch in w[:i]) else ["AY"]
        return ["IH"]
    if c == prev:
        return []                 # a doubled consonant is said once
    if c == "c":
        return ["S"] if nxt in ("e", "i", "y") else ["K"]
    if c == "g":
        return ["JH"] if nxt in ("e", "i", "y") and i > 0 else ["G"]
    if c == "h":
        return ["HH"] if nxt in _LV or i == 0 else []
    if c == "x":
        return ["Z"] if i == 0 else ["K", "S"]
    if c == "s":
        return ["Z"] if i == n - 1 and n > 2 and prev in "bdglmnrvaeiouy" and prev != "s" else ["S"]
    return [_PLAIN[c]]


# ------------------------------------------------------------------ Layer 2

class Match(namedtuple("Match", "entry target start end distance")):
    """One entry found in a line: `target` the phrase or alias matched; `start`, `end` the
    words of the line it covers (words(text), end exclusive); `distance` 0 (exact) to 1."""
    __slots__ = ()


def _line(text):
    """The line's phone ids, and for each the index of its word in words(text); fillers left
    out, and only the first MAX_LINE_WORDS words read."""
    ids, word_of = [], []
    for k, w in enumerate(words(text)[:MAX_LINE_WORDS]):
        if w in FILLERS:
            continue
        for p in _word_ids(w):
            ids.append(p)
            word_of.append(k)
    return ids, word_of


@lru_cache(maxsize=1024)
def _tables(t):
    """A target's costs, by phone id: (each of its phones left out, [each phone of the line
    against each of its phones])."""
    return [_INDEL[a] for a in t], [[_SUB[a][b] for a in t] for b in range(len(PHONES))]


def _best_end(t, line):
    """The cheapest cost of the target `t` (phone ids) aligned whole against any run of `line`,
    and the index just past that run: (cost, end). Semi-global edit distance: the line is free
    before and after the run. One column of costs at a time, no traceback. An end up to
    SPAN_PHONES further on that costs at most SPAN_SLACK more is taken instead, so a last
    phoneme is matched loosely rather than left out ("ha" in "come a hammer ha")."""
    m = len(t)
    dels, cols = _tables(t)
    prev = [0.0] * (m + 1)
    for i in range(m):
        prev[i + 1] = prev[i] + dels[i]
    ends = [prev[m]]
    cur = [0.0] * (m + 1)
    rng = range(m)
    for b in line:
        sub = cols[b]
        ins = _INDEL[b]
        up = 0.0
        diag = 0.0
        for i in rng:
            left = prev[i + 1]
            v = diag + sub[i]
            x = left + ins
            if x < v:
                v = x
            x = up + dels[i]
            if x < v:
                v = x
            cur[i + 1] = v
            diag = left
            up = v
        ends.append(up)
        prev, cur = cur, prev
    best = min(ends)
    end = ends.index(best)
    for j in range(end + 1, min(end + SPAN_PHONES, len(line)) + 1):
        if ends[j] <= best + SPAN_SLACK:
            end = j
    return ends[end], end


def _span(t, line, end):
    """Where the cheapest run of `t` ending at `end` starts: the same alignment again over
    line[:end], keeping each cell's start."""
    m = len(t)
    prev = [0.0] * (m + 1)
    pstart = [0] * (m + 1)
    for i in range(m):
        prev[i + 1] = prev[i] + _INDEL[t[i]]
    for j in range(1, end + 1):
        b = line[j - 1]
        cur = [0.0] * (m + 1)
        cstart = [j] * (m + 1)
        for i in range(1, m + 1):
            a = t[i - 1]
            v, s = prev[i - 1] + _SUB[a][b], pstart[i - 1]
            if prev[i] + _INDEL[b] < v:
                v, s = prev[i] + _INDEL[b], pstart[i]
            if cur[i - 1] + _INDEL[a] < v:
                v, s = cur[i - 1] + _INDEL[a], cstart[i - 1]
            cur[i], cstart[i] = v, s
        prev, pstart = cur, cstart
    return pstart[m]


def _distance(a, b):
    """How unlike two normalised targets sound, 0 to 1: the closer of a found in b and b in a."""
    ta, tb = _target_ids(a), _target_ids(b)
    if not ta or not tb:
        return 1.0
    return min(_best_end(ta, tb)[0] / len(ta), _best_end(tb, ta)[0] / len(tb))


def _found(ids, entry, allowance):
    """Each of the entry's targets (once per sound) whose best run in the line's phone ids is
    within `allowance`: [(distance, target, its ids, end)], best first."""
    out, seen = [], set()
    for target in targets(entry):
        t = _target_ids(target)
        if not t or t in seen:
            continue
        seen.add(t)
        cost, end = _best_end(t, ids)
        d = cost / len(t)
        if d <= allowance + EPS and end > 0:
            out.append((d, target, t, end))
    out.sort(key=lambda f: f[0])
    return out


def match_line(text, entries, allowance=ALLOWANCE):
    """Layer 2: each entry whose phrase or an alias sounds like a run of the line, within
    `allowance`, across word boundaries; its best match only, best first. Fillers are skipped,
    and only the first MAX_LINE_WORDS words read."""
    ids, word_of = _line(text)
    if not ids:
        return []
    out = []
    for e in entries:
        found = _found(ids, e, allowance)
        if found:
            d, target, t, end = found[0]
            start = _span(t, ids, end)
            out.append(Match(e, target, word_of[min(start, end - 1)], word_of[end - 1] + 1, round(d, 3)))
    out.sort(key=lambda mt: (mt.distance, mt.start))
    return out


# ------------------------------------------------------------------ the drop rule

def words_left(text, entry):
    """The drop rule's count: the line's words, fillers never counted, less the phrase's.
    The phrase is found by its exact words (the phrase or an alias, as a run of words: every
    such run is taken out), else by Layer 2 at DROP_ALLOWANCE (the words it covers) when that
    run is plausibly the phrase (_plausible). A line it is not found in has nothing taken out,
    so every word of it is left: it goes on to Jev and the brain, which see the cast."""
    return _left(text, entry)[0]


def _left(text, entry):
    """words_left's count, and the words left (every word of the line when the phrase is not
    found in it)."""
    ws = [w for w in words(text) if w not in FILLERS]
    runs = []
    for target in targets(entry):
        r = [w for w in target.split() if w not in FILLERS] or target.split()
        if r not in runs:
            runs.append(r)
    runs.sort(key=len, reverse=True)
    left, i, found = [], 0, False
    while i < len(ws):
        for r in runs:
            if ws[i:i + len(r)] == r:
                i += len(r)
                found = True
                break
        else:
            left.append(ws[i])
            i += 1
    if found:
        return len(left), left
    ids, word_of = _line(" ".join(ws))
    for d, target, t, end in _found(ids, entry, DROP_ALLOWANCE):
        start = min(_span(t, ids, end), end - 1)
        first, last = word_of[start], word_of[end - 1] + 1
        if _plausible(d, target, t, end - start, last - first):
            return len(ws) - (last - first), ws[:first] + ws[last:]
    return len(ws), ws


def _plausible(d, target, t, phones_run, covered):
    """Whether a run the drop rule found by phonemes is plausibly the phrase, not a real ask
    that a loose match swallowed whole: its phonemes are DROP_SPAN_RATIO of the target's, and
    it covers at most the target's words + DROP_SPAN_WORDS, unless it is as close as Layer 2's
    own ALLOWANCE (a split rendering: "come a hammer ha" for "kamehameha")."""
    lo, hi = DROP_SPAN_RATIO
    if not lo <= phones_run / float(len(t)) <= hi:
        return False
    return covered <= len(target.split()) + DROP_SPAN_WORDS or d <= ALLOWANCE + EPS


def drop(text, entry):
    """True when the line is only the incantation: at most one word left, and none of the
    words left is one of KEEP_WORDS ("stop fire ball" goes on to the brain)."""
    n, left = _left(text, entry)
    return n <= 1 and not KEEP_WORDS.intersection(left)


# ------------------------------------------------------------------ aliases and bind checks

def _split(word):
    """A compound word as two dictionary words of 3 letters at least, "fireball" -> "fire ball",
    or None. A dictionary word splits only where its parts say it the same way."""
    whole = _cmu(word)
    best = None
    for k in range(3, len(word) - 2):
        a, b = word[:k], word[k:]
        pa, pb = _cmu(a), _cmu(b)
        if pa is None or pb is None:
            continue
        if whole is not None and (pa + " " + pb) != whole:
            continue
        if best is None or abs(len(a) - len(b)) < abs(len(best[0]) - len(best[1])):
            best = (a, b)
    return None if best is None else best[0] + " " + best[1]


def code_aliases(phrase):
    """The phrase joined ("fire ball" -> "fireball"; for three words or more, also any two
    neighbours that join into a dictionary word), and the split forms of its compound words
    ("fireball blast" -> "fire ball blast", each word alone and then all of them)."""
    ws = words(phrase)
    out = []
    if len(ws) > 1:
        out.append("".join(ws))
    if len(ws) > 2:
        for i in range(len(ws) - 1):
            if _cmu(ws[i] + ws[i + 1]) is not None:
                out.append(" ".join(ws[:i] + [ws[i] + ws[i + 1]] + ws[i + 2:]))
    splits = [_split(w) for w in ws]
    for i, s in enumerate(splits):
        if s:
            out.append(" ".join(ws[:i] + [s] + ws[i + 1:]))
    if sum(1 for s in splits if s) > 1:
        out.append(" ".join(s or w for s, w in zip(splits, ws)))
    p = " ".join(ws)
    return [a for k, a in enumerate(out) if a != p and a not in out[:k]]


def merge_aliases(phrase, *lists, cap=ALIAS_CAP):
    """Aliases from every list, in order: normalised, each once, never the phrase itself, none
    of fewer than MIN_ALIAS_PHONES phonemes; `cap` at most."""
    seen = {normalize(phrase)}
    out = []
    for lst in lists:
        for a in lst or []:
            n = normalize(a)
            if not n or n in seen or len(_target_ids(n)) < MIN_ALIAS_PHONES:
                continue
            seen.add(n)
            out.append(n)
            if len(out) >= cap:
                return out
    return out


def bind_warnings(phrase, aliases, others):
    """Why a phrase may misfire, one plain sentence each: it is short (fewer than SHORT_PHONES
    phonemes); it, or an alias, sounds like another entry's phrase or alias (distance at most
    SOUNDS_LIKE, either inside the other); or it, or an alias, starts with another entry's
    first word. `others`: the player's other entries (the phrase's own entry is skipped)."""
    p = normalize(phrase)
    out = []
    n = len(_target_ids(p))
    if n < SHORT_PHONES:
        out.append('"%s" is short (%d speech sounds), so it may fire by accident: a third word helps.' % (p, n))
    mine = [p] + [a for a in (normalize(x) for x in aliases or []) if a and a != p]
    for o in others or []:
        op = normalize(o.get("phrase"))
        if not op or op == p:
            continue
        theirs = targets(o)
        hit = None
        for a in mine:
            for b in theirs:
                d = _distance(a, b)
                if d <= SOUNDS_LIKE + EPS and (hit is None or d < hit[0]):
                    hit = (d, a, b)
        if hit is not None:
            _, a, b = hit
            said = '"%s"' % a if a == p else 'Its alias "%s"' % a
            like = '"%s"' % b if b == op else '"%s", an alias of "%s"' % (b, op)
            out.append("%s sounds like %s, so one may fire for the other." % (said, like))
            continue
        first = op.split()[0]
        for a in mine:
            if a.split()[0] == first:
                said = '"%s"' % a if a == p else 'Its alias "%s"' % a
                out.append('%s starts with "%s", as "%s" does, so one may fire for the other.' % (said, first, op))
                break
    return out
