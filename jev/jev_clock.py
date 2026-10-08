"""Time for the filter: one clock, and the contract's timestamp format.

Every timestamp in the system is ISO-8601 UTC with milliseconds
(`2026-10-07T18:22:31.412Z`, docs/CONTRACTS.md §1). The filter writes them
with `iso()` and reads them with `parse_iso()`; because they all share that one
fixed-width format, the SQL compares them as strings.

`SystemClock` is the clock the service runs on; the tests pass their own object
with the same `time()` so no test sleeps through a 10-second rule.
"""
import datetime as dt
import re
import time

UTC = dt.timezone.utc
_FRACTION = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?(.*)$")


class SystemClock:
    """Seconds since the epoch, UTC — `time.time()`."""

    def time(self):
        return time.time()


def iso(t):
    """Epoch seconds -> `2026-10-07T18:22:31.412Z`, rounded to the millisecond."""
    ms = int(round(t * 1000))
    d = dt.datetime.fromtimestamp(ms // 1000, UTC)
    return d.strftime("%Y-%m-%dT%H:%M:%S") + ".%03dZ" % (ms % 1000)


def parse_iso(s):
    """An ISO-8601 timestamp -> epoch seconds. Takes `Z` or an offset, and any
    number of fraction digits (Python 3.9's `fromisoformat` takes only 3 or 6);
    a timestamp with no zone is read as UTC."""
    return _datetime(s).timestamp()


def hms(s):
    """An ISO-8601 timestamp -> `HH:MM:SS` in UTC."""
    return _datetime(s).astimezone(UTC).strftime("%H:%M:%S")


def _datetime(s):
    s = s.strip().replace(" ", "T", 1)
    m = _FRACTION.match(s)
    if not m:
        raise ValueError("not an ISO-8601 timestamp: %r" % s[:40])
    base, frac, zone = m.group(1), m.group(2) or "0", m.group(3)
    if zone in ("Z", "z"):
        zone = "+00:00"
    d = dt.datetime.fromisoformat(base + "." + (frac + "000000")[:6] + zone)
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    return d
