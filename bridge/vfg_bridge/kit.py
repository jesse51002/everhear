"""The kit's ``incantations`` and ``instances`` modules, imported from VFG_KIT (/opt/vfg/scripts/_lib).

They are imported on first use, and imported again whenever the kit's deployed stamp
(``.vfg-stamp``, which vfg writes with each deploy) changes, so a new kit needs no
restart.  A kit without them (none deployed yet, or one from before incantations) is
logged once, and ``modules()`` answers None until a deploy changes the stamp.
"""

import importlib.util
import logging
import os
import sys
import threading

log = logging.getLogger("vfg_bridge")

NAMES = ("instances", "incantations")  # instances first: incantations may import it
STAMP = ".vfg-stamp"
# What this bridge calls in them: a kit without one is older than the bridge (deploy.sh sends both,
# so only a hand deploy can part them), and is not used: casts answer 503 saying so.
NEEDS = {"incantations": ("cast_meta",), "instances": ("Box",)}


class Kit:
    def __init__(self, path, logger=None):
        self.path = path
        self.log = logger or log
        self._lock = threading.Lock()
        self._stamp = self      # never a stamp: the first call loads
        self._mods = None
        self._seen = (None, None)  # the stamp file's key, and what it said
        self.why = None            # why modules() answers None, for a 503

    def stamp(self):
        """What .vfg-stamp says, read again only when its file changes (vfg replaces it whole)."""
        path = os.path.join(self.path, STAMP)
        try:
            st = os.stat(path)
        except OSError:
            return None
        key = (st.st_ino, st.st_size, st.st_mtime_ns)
        seen, text = self._seen
        if key != seen:
            try:
                with open(path, encoding="ascii") as f:
                    text = f.read().strip()
            except (OSError, ValueError):
                return None
            self._seen = (key, text)
        return text

    def modules(self):
        """(incantations, instances), or None when the kit does not have them."""
        stamp = self.stamp()
        if stamp == self._stamp:
            return self._mods
        with self._lock:
            if stamp != self._stamp:
                first = self._stamp is self
                self._mods = self._load()
                self._stamp = stamp
                if self._mods is not None and not first:
                    self.log.info("the kit in %s changed (stamp %s); its modules are reloaded",
                                  self.path, (stamp or "none")[:12])
                    threading.Thread(target=self.warm, name="warm", daemon=True).start()
            return self._mods

    def _load(self):
        """Fresh module objects, so a call still running on the old ones finishes on them."""
        missing = [n for n in NAMES if not os.path.isfile(os.path.join(self.path, n + ".py"))]
        if missing:
            self.why = "the kit in %s has no %s" % (self.path, " or ".join(missing))
            self.log.warning("%s: casts answer 503, and lines skip Layer 2 and the drop rule", self.why)
            return None
        if self.path not in sys.path:
            sys.path.append(self.path)  # for what the two import from the kit themselves
        importlib.invalidate_caches()
        loaded = {}
        try:
            for name in NAMES:
                spec = importlib.util.spec_from_file_location(name, os.path.join(self.path, name + ".py"))
                module = importlib.util.module_from_spec(spec)
                sys.modules[name] = module
                spec.loader.exec_module(module)
                loaded[name] = module
        except Exception:
            self.why = "the kit in %s did not import" % self.path
            self.log.exception("%s: casts answer 503, and lines skip Layer 2 and the drop rule", self.why)
            return None
        old = ["%s.%s" % (n, f) for n, fs in sorted(NEEDS.items()) for f in fs if not hasattr(loaded[n], f)]
        if old:
            self.why = ("the kit in %s is older than this bridge (no %s): deploy it again, "
                        "vfg script deploy --kit (deploy.sh does)" % (self.path, ", ".join(old)))
            self.log.warning("%s; until then casts answer 503, and lines skip Layer 2 and the drop rule",
                             self.why)
            return None
        self.why = None
        return loaded["incantations"], loaded["instances"]

    def warm(self):
        """Load the pronunciation dictionary now, not on the first line; for a thread."""
        try:
            mods = self.modules()
            if mods is not None:
                mods[0].warm()
        except Exception:
            self.log.exception("warming the incantations module failed")
