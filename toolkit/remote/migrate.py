# migrate.py: the box's side of `vfg engine migrate`, a one-time read of what the old system kept
# (the game engine spec, part 2: "the old system is removed"). It writes nothing. Appended after
# common.py, ARGS and remote/places.py (the module `places`):
#   {"vfg": "/opt/vfg", "mc": "/opt/mc"}
# It prints one JSON object:
#   {"places": {name: {"pos": [x, y, z], "dim": "minecraft:overworld", "by": "castle_steve"}},
#    "places_error": "<why>"?, "store": {"version", "incantations": [entry, ...]}, "store_error": "<why>"?}
# "places" is Minecraft's command storage everhear:places, read over RCON; "store" the old
# incantation store, <vfg>/incantations.json, as it lies ({"version": 0, "incantations": []} when
# there is none). One that cannot be read is empty, with why beside it: vfg migrates the rest.


class Server(object):
    """common.py's RCON client as the callable places.py takes: a command in, the reply out."""

    def __init__(self):
        self.r = Rcon(ARGS["mc"])

    def __call__(self, command):
        return self.r.command(command)

    def close(self):
        self.r.close()


def old_places():
    try:
        server = Server()
    except RconDown as e:
        return {}, "minecraft did not answer RCON: %s" % e
    try:
        return places.all(server), None
    except (RconDown, places.PlaceError, ValueError) as e:
        return {}, "everhear:places could not be read: %s" % e
    finally:
        server.close()


def old_store():
    try:
        with open(os.path.join(ARGS["vfg"], "incantations.json"), encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {"version": 0, "incantations": []}, None
    except (OSError, ValueError) as e:
        return {"version": 0, "incantations": []}, "the old incantation store cannot be read: %s" % e
    if not isinstance(data, dict) or not isinstance(data.get("incantations"), list):
        return {"version": 0, "incantations": []}, "the old incantation store is not {version, incantations}"
    return data, None


got = {}
got["places"], why = old_places()
if why:
    got["places_error"] = why
got["store"], why = old_store()
if why:
    got["store_error"] = why
print(json.dumps(got, ensure_ascii=False))
