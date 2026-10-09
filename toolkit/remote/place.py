# place.py: vfg place save, list and forget. Appended after common.py, ARGS and
# remote/places.py, which is the module `places` here: the same code a handler runs
# through mc.rcon runs here through common.py's RCON client. ARGS:
#   {"mc": "/opt/mc", "op": "save", "name": "castle", "player": "Steve", "at": null, "dim": null, "by": "T7"}
#   {"mc": "/opt/mc", "op": "save", "name": "castle", "player": null, "at": ["197", "68", "251.5"],
#    "dim": "overworld" | null, "by": ""}
#   {"mc": "/opt/mc", "op": "list", "json": false}
#   {"mc": "/opt/mc", "op": "forget", "name": "castle"}
import re

DATA_RE = re.compile(r"^\S+ has the following entity data: (.*)$", re.S)


class Server(object):
    """common.py's RCON client as the callable places.py takes: a command in, the reply out."""

    def __init__(self):
        try:
            self.r = Rcon(ARGS["mc"])
        except RconDown as e:
            die(5, str(e))

    def __call__(self, command):
        try:
            return self.r.command(command)
        except RconDown as e:
            die(5, str(e))

    def close(self):
        self.r.close()


def short(dim):
    return dim[len("minecraft:"):] if dim.startswith("minecraft:") else dim


def entity_data(rcon, player, path):
    """One value of an online player's data: Pos is [x, y, z], Dimension a string."""
    reply = rcon("data get entity %s %s" % (player, path))
    if reply.startswith(("No entity was found", "No player was found")):
        die(1, "%s is not online" % player)
    m = DATA_RE.match(reply)
    if not m:
        die(1, "minecraft answered: %s" % (" ".join(reply.split()) or "nothing"))
    try:
        return places.snbt(m.group(1))
    except ValueError as e:
        die(1, "cannot read %s's %s: %s" % (player, path, e))


def save(rcon):
    name = ARGS["name"]
    if ARGS.get("player"):
        pos = entity_data(rcon, ARGS["player"], "Pos")
        dim = entity_data(rcon, ARGS["player"], "Dimension")
        if not (isinstance(pos, list) and len(pos) == 3) or not isinstance(dim, str):
            die(1, "cannot read where %s stands" % ARGS["player"])
    else:
        pos, dim = [float(v) for v in ARGS["at"]], ARGS.get("dim") or "overworld"
    p = places.save(rcon, name, pos, dim, ARGS.get("by") or "")
    print("saved %s at %d %d %d in %s" % (name, p["pos"][0], p["pos"][1], p["pos"][2], short(p["dim"])))


def show(rcon):
    found = places.all(rcon)
    if ARGS.get("json"):
        print(json.dumps(found, sort_keys=True, ensure_ascii=False))
    elif not found:
        print("none")
    else:
        for name in sorted(found):
            print(places.line(name, found[name]))


def forget(rcon):
    if not places.forget(rcon, ARGS["name"]):
        die(1, "no place named %s" % ARGS["name"])
    print("forgot %s" % ARGS["name"])


def main():
    op = {"save": save, "list": show, "forget": forget}.get(ARGS["op"])
    if op is None:
        die(2, "unknown place op %r" % ARGS["op"])
    rcon = Server()
    try:
        op(rcon)
    except places.PlaceError as e:
        die(1, str(e))
    except ValueError as e:
        die(2, str(e))
    finally:
        rcon.close()


main()
