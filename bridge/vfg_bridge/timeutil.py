"""Times: read incoming ones, write ours, date a console line.

Ours are ``YYYY-MM-DDTHH:MM:SS.mmmZ``.  Reading and writing are vfg_feed's,
so the feed and the history can never disagree on a format.
"""

import datetime as _dt

from vfg_feed import UTC, format_time, parse_time, utcnow  # noqa: F401 -- re-exported

AHEAD_LIMIT = _dt.timedelta(seconds=60)
FRESH_MS = _dt.timedelta(seconds=5)


def console_at(hour, minute, second, now):
    """The moment of a console stamp ``HH:MM:SS`` read at ``now`` (aware, UTC).

    The stamp goes on the latest UTC date that does not put it more than 60 s
    ahead of ``now`` -- so ``23:59:58`` read at ``00:00:01`` keeps yesterday.
    Its milliseconds are the clock's when it is read within 5 s of the stamp,
    and 000 otherwise.
    """
    now = now.astimezone(UTC)
    moment = now.replace(hour=hour, minute=minute, second=second, microsecond=0)
    moment += _dt.timedelta(days=1)
    while moment - now > AHEAD_LIMIT:
        moment -= _dt.timedelta(days=1)
    if abs(now - moment) <= FRESH_MS:
        moment = moment.replace(microsecond=(now.microsecond // 1000) * 1000)
    return moment
