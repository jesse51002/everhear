"""ULIDs: 48 bits of Unix milliseconds, 80 random bits, Crockford base32, 26 characters.

Within one millisecond the random part is incremented by one, behind a lock,
so the ids one process makes sort in the order it made them.
"""

import os
import threading
import time

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_RANDOM_MAX = (1 << 80) - 1

_lock = threading.Lock()
_last_ms = -1
_last_rand = 0


def _encode(value):
    chars = []
    for _ in range(26):
        chars.append(ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def new(now_ms=None):
    """A new ULID string."""
    global _last_ms, _last_rand
    with _lock:
        ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
        if ms <= _last_ms:
            # Same millisecond, or the clock stepped back: keep the order.
            ms = _last_ms
            rand = _last_rand + 1
            if rand > _RANDOM_MAX:
                ms += 1
                rand = int.from_bytes(os.urandom(10), "big")
        else:
            rand = int.from_bytes(os.urandom(10), "big")
        _last_ms, _last_rand = ms, rand
    return _encode(((ms & ((1 << 48) - 1)) << 80) | rand)


def timestamp_ms(ulid):
    """The milliseconds a ULID was made at."""
    value = 0
    for ch in ulid[:10]:
        value = value * 32 + ALPHABET.index(ch)
    return value
