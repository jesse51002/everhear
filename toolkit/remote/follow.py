# follow.py: vfg watch, on EC2. ARGS:
#   {"after": <seq>|null, "catchup": 100, "feed": "/opt/vfg/feed.log", "tail": null|N}
# Prints feed lines with a seq above `after`, then follows feed.log by seq.
# Protocol lines, besides feed lines:
#   @at N               `after` was null: the follow starts after seq N
#   @reset              the feed was recreated (its highest seq is below `after`)
#   @skip A B N         N lines, seq A to B, were left out (more than `catchup`)
#   @hb                 heartbeat, every 10 s and at the end of the first write
import re

FEED_RE = re.compile(rb"^(LINE|GAME|ORCH|SYS)#(\d+) ")
POLL = 0.1
HEARTBEAT = 10.0


def out(data):
    """One write; a failed write means the PC has gone, so exit."""
    view = memoryview(data)
    try:
        while view:
            n = os.write(1, view)
            view = view[n:]
    except OSError:
        os._exit(0)


def parse(lines):
    entries = []
    for line in lines:
        m = FEED_RE.match(line)
        if m:
            entries.append((int(m.group(2)), line))
    return entries


def split_complete(data):
    """Complete lines, and the unterminated rest."""
    i = data.rfind(b"\n")
    if i < 0:
        return [], data
    return data[:i].split(b"\n"), data[i + 1:]


def opening(entries, after, catchup, tail, parts):
    """What to print from a whole file; returns (lines, last)."""
    highest = max([s for s, _ in entries] or [0])
    if tail is not None:
        tail = max(0, int(tail))
        catchup = max(catchup, tail)
        if tail == 0:
            after = highest
        elif tail >= len(entries):
            after = 0
        else:
            after = entries[-tail - 1][0]
        parts.append(b"@at %d" % after)
    elif after is None:
        after = highest
        parts.append(b"@at %d" % after)
    elif highest < after:
        parts.append(b"@reset")
        after = 0
    pend = [(s, l) for s, l in entries if s > after]
    if len(pend) > catchup:
        skipped = pend[:len(pend) - catchup]
        parts.append(b"@skip %d %d %d" % (skipped[0][0], skipped[-1][0], len(skipped)))
        pend = pend[len(pend) - catchup:]
    last = after
    for s, l in pend:
        if s > last:
            parts.append(l)
            last = s
    return last


def main():
    path = ARGS["feed"]
    after = ARGS.get("after")
    catchup = int(ARGS.get("catchup") or 100)
    tail = ARGS.get("tail")
    last_hb = time.time()

    if not os.path.exists(path):
        # Whatever appears in a feed that did not exist yet is new.
        if after is None and tail is None:
            after = 0
            out(b"@at 0\n")
        while not os.path.exists(path):
            if time.time() - last_hb >= HEARTBEAT:
                out(b"@hb\n")
                last_hb = time.time()
            time.sleep(0.25)

    f = open(path, "rb")
    ino = os.fstat(f.fileno()).st_ino
    data = f.read()
    pos = len(data)
    lines, buf = split_complete(data)
    parts = []
    last = opening(parse(lines), after, catchup, tail, parts)
    parts.append(b"@hb")
    out(b"\n".join(parts) + b"\n")
    last_hb = time.time()

    while True:
        time.sleep(POLL)
        parts = []
        try:
            st = os.stat(path)
        except OSError:
            st = None
        if st is not None and (st.st_ino != ino or st.st_size < pos):
            # Replaced or truncated: read it again from the start.
            f.close()
            try:
                f = open(path, "rb")
            except OSError:
                st = None
            else:
                ino = os.fstat(f.fileno()).st_ino
                data = f.read()
                pos = len(data)
                lines, buf = split_complete(data)
                last = opening(parse(lines), last, catchup, None, parts)
        elif st is not None and st.st_size > pos:
            f.seek(pos)
            data = f.read()
            pos += len(data)
            lines, buf = split_complete(buf + data)
            for s, l in parse(lines):
                if s > last:
                    parts.append(l)
                    last = s
        now = time.time()
        if now - last_hb >= HEARTBEAT:
            parts.append(b"@hb")
            last_hb = now
        if parts:
            out(b"\n".join(parts) + b"\n")


main()
