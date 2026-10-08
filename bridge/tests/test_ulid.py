"""Ids: ULIDs, 26 Crockford base32 characters, distinct and sorted in the order made."""

import re
import time
import unittest

from vfg_bridge import ulid

CROCKFORD = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


class TestUlid(unittest.TestCase):
    def test_ten_thousand_in_a_tight_loop(self):
        ids = [ulid.new() for _ in range(10000)]
        self.assertTrue(all(CROCKFORD.match(i) for i in ids))
        self.assertEqual(len(set(ids)), 10000)
        self.assertEqual(ids, sorted(ids))

    def test_timestamp_is_now(self):
        before = int(time.time() * 1000)
        value = ulid.new()
        after = int(time.time() * 1000)
        self.assertTrue(before <= ulid.timestamp_ms(value) <= after + 1)

    def test_clock_stepping_back_keeps_order(self):
        saved = (ulid._last_ms, ulid._last_rand)
        self.addCleanup(lambda: setattr(ulid, "_last_ms", saved[0]))
        self.addCleanup(lambda: setattr(ulid, "_last_rand", saved[1]))
        first = ulid.new(now_ms=4102444800000)  # 2100-01-01
        second = ulid.new(now_ms=4102444799000)
        self.assertLess(first, second)


if __name__ == "__main__":
    unittest.main()
