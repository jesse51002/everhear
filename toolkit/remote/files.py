# files.py: vfg watchers and vfg lookout. ARGS:
#   {"vfg": "/opt/vfg", "op": "watchers_set", "watch": {"events": [...], "players": [...], "contains": [...]}}
#   {"vfg": "/opt/vfg", "op": "watchers_show"}
#   {"vfg": "/opt/vfg", "op": "lookout_set", "text": "..."}
#   {"vfg": "/opt/vfg", "op": "lookout_show"}

DEFAULT_WATCH = {"events": ["join", "leave", "death", "advancement", "server"],
                 "players": [], "contains": []}


def main():
    base = ARGS["vfg"]
    if not os.path.isdir(base):
        die(1, "%s missing on EC2" % base)
    op = ARGS["op"]
    watch = os.path.join(base, "watch.json")
    lookout = os.path.join(base, "lookout.md")
    if op == "watchers_set":
        text = json.dumps(ARGS["watch"], ensure_ascii=False)
        atomic_write(watch, text + "\n", 0o644)
        print(text)
    elif op == "watchers_show":
        try:
            with open(watch, encoding="utf-8") as f:
                raw = f.read()
        except FileNotFoundError:
            print(json.dumps(DEFAULT_WATCH))
            warn("watch.json missing — the bridge uses the default")
            return
        try:
            print(json.dumps(json.loads(raw), ensure_ascii=False))
        except ValueError:
            print(" ".join(raw.split()))
            die(1, "watch.json is not valid JSON")
    elif op == "lookout_set":
        text = ARGS["text"]
        atomic_write(lookout, text + "\n", 0o644)
        print("lookout.md: %d characters" % len(text))
    elif op == "lookout_show":
        try:
            with open(lookout, encoding="utf-8") as f:
                sys.stdout.write(f.read())
        except FileNotFoundError:
            warn("lookout.md missing — Jev has nothing extra to look out for")
    else:
        die(2, "unknown files op %r" % op)


main()
