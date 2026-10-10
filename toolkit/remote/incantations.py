# incantations.py: the box's side of vfg incantation list, and of the checks vfg makes before an
# incantation_* action runs (toolkit/vfg, run_incantation). Appended after common.py and ARGS:
#   {"vfg": "/opt/vfg", "op": "list"}
#   {"vfg": "/opt/vfg", "op": "list", "stamps": [{"root": "scripts", "stamps": {"_lib": "<sha256>", "command": ...}}]}
# It prints one JSON object: {"store": the store as scripts/_lib/incantations.py keeps it in
# <vfg>/incantations.json ({"version": 0, "incantations": []} when there is none), "stale": the
# folders "stamps" names that the box does not have as stamped, ["scripts/command", ...]}.
# The store is read as it lies, with no lock: every write to it is a tmp file and a rename.
# The stamp rule is scripts.py's (stale): a folder is stale when its .vfg-stamp is missing or
# another; vfg then deploys it with scripts.py's deploy.
import re

ROOT_RE = re.compile(r"^(scripts|made/[a-z0-9][a-z0-9_-]{0,47}/scripts)$")
TOP_RE = re.compile(r"^(_lib|[a-z0-9][a-z0-9_-]{0,47})$")
STAMP_RE = re.compile(r"^[0-9a-f]{64}$")
BASE = ARGS["vfg"]


def store():
    try:
        with open(os.path.join(BASE, "incantations.json"), encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {"version": 0, "incantations": []}
    except (OSError, ValueError) as e:
        die(1, "the incantation store cannot be read: %s" % e)
    if not isinstance(data, dict) or not isinstance(data.get("incantations"), list):
        die(1, "the incantation store is not {\"version\", \"incantations\"}")
    return data


def stale(entries):
    out = []
    for t in entries or []:
        root = t.get("root") or ""
        for top, want in sorted((t.get("stamps") or {}).items()):
            if not (ROOT_RE.match(root) and TOP_RE.match(top)):
                die(1, "refused: %s/%s is not a deployed folder" % (root, top))
            try:
                with open(os.path.join(BASE, *root.split("/") + [top, ".vfg-stamp"]), encoding="ascii") as f:
                    have = f.read().strip()
            except (OSError, ValueError):
                have = None
            if not (STAMP_RE.match(want or "") and have == want):
                out.append("%s/%s" % (root, top))
    return out


if ARGS.get("op") != "list":
    die(2, "unknown op %r" % ARGS.get("op"))
print(json.dumps({"store": store(), "stale": stale(ARGS.get("stamps"))}, ensure_ascii=False))
