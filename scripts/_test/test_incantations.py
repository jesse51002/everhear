"""Tests for scripts/_lib/incantations.py: words, the store (in a temp folder), phonemes, Layer 2,
the drop rule, the code aliases and the bind checks. No server, no network; each test takes
milliseconds.

    uv run --no-project --python 3.12 python scripts/_test/test_incantations.py
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(os.path.dirname(HERE), "_lib")
sys.path.insert(0, LIB)

import incantations as inc  # noqa: E402

FIRE_BALL = {"player": "Steve", "phrase": "fire ball", "aliases": []}
KAMEHAMEHA = {"player": "Steve", "phrase": "kamehameha wave", "aliases": ["kamehameha"]}


class WordsTests(unittest.TestCase):
    def test_normalize_drops_apostrophes_and_marks_hyphens_and_underscores(self):
        self.assertEqual(inc.normalize("  Don't cast\u2014FIRE_ball, Fire-Ball!  Caf\u00e9 don\u2019t "),
                         "dont cast fire ball fire ball cafe dont")

    def test_words(self):
        self.assertEqual(inc.words("Uh, fire ball."), ["uh", "fire", "ball"])

    def test_check_phrase_wants_two_words(self):
        self.assertEqual(inc.check_phrase("Fire-Ball!"), "fire ball")
        for bad in ("fireball", "", "  !! "):
            with self.assertRaisesRegex(ValueError, "two words at least"):
                inc.check_phrase(bad)

    def test_split_players(self):
        self.assertEqual(inc.split_players("Steve, Alex,steve"), ["Steve", "Alex"])
        self.assertEqual(inc.split_players("@a"), ["@a"])
        self.assertEqual(inc.split_players("Steve,@a"), ["@a"])
        for bad in ("", " , ", "@p", "Ste ve", "x" * 17):
            with self.assertRaises(ValueError):
                inc.split_players(bad)

    def test_applies_to(self):
        self.assertTrue(inc.applies_to({"player": "Steve"}, "steve"))
        self.assertTrue(inc.applies_to({"player": "@a"}, "Alex"))
        self.assertFalse(inc.applies_to({"player": "Steve"}, "Alex"))

    def test_store_path(self):
        with mock.patch.dict(os.environ, {"VFG_INCANTATIONS": "/x/i.json", "VFG_RUN_DIR": "/b/run"}):
            self.assertEqual(inc.store_path(), "/x/i.json")
        with mock.patch.dict(os.environ, {"VFG_INCANTATIONS": "", "VFG_RUN_DIR": "/b/run/"}):
            self.assertEqual(inc.store_path(), "/b/incantations.json")
        with mock.patch.dict(os.environ, {"VFG_INCANTATIONS": "", "VFG_RUN_DIR": ""}):
            self.assertEqual(inc.store_path(), "/opt/vfg/incantations.json")


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="vfg-incantations-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.path = os.path.join(self.dir, "box", "incantations.json")
        self.store = inc.Store(self.path)

    def put(self, player, phrase, **fields):
        return self.store.put(dict(fields, player=player, phrase=phrase))

    def test_no_file_reads_empty(self):
        self.assertEqual(self.store.read(), {"version": 0, "incantations": []})
        self.assertEqual(self.store.version(), 0)

    def test_put_round_trip_fills_id_and_bumps_version(self):
        entry, replaced = self.put("Steve", "Fire  Ball!", action="command", values={"commands": ["say hi"]})
        self.assertFalse(replaced)
        self.assertEqual((entry["id"], entry["phrase"], entry["cooldown_s"]), ("steve.fire_ball", "fire ball", 3.0))
        with open(self.path) as f:
            on_disk = json.load(f)
        self.assertEqual(on_disk["version"], 1)
        self.assertEqual(on_disk["incantations"], [entry])
        self.assertEqual(self.store.find("STEVE", "fire-ball"), entry)
        self.assertTrue(os.path.exists(os.path.join(self.dir, "box", "incantations.lock")))
        self.assertEqual(sorted(os.listdir(os.path.join(self.dir, "box"))), ["incantations.json", "incantations.lock"])

    def test_put_same_player_and_phrase_replaces(self):
        self.put("Steve", "fire ball", action="command")
        entry, replaced = self.put("steve", "FIRE BALL", action="meteor")
        self.assertTrue(replaced)
        self.assertEqual([e["action"] for e in self.store.read()["incantations"]], ["meteor"])
        self.assertEqual(entry["id"], "steve.fire_ball")
        self.assertEqual(self.store.version(), 2)

    def test_at_a_keeps_its_id(self):
        entry, _ = self.put("@A", "fire ball")
        self.assertEqual((entry["player"], entry["id"]), ("@a", "@a.fire_ball"))

    def test_put_refuses_several_players_and_one_word(self):
        with self.assertRaises(ValueError):
            self.put("Steve,Alex", "fire ball")
        with self.assertRaises(ValueError):
            self.put("Steve", "fireball")
        self.assertEqual(self.store.version(), 0)

    def test_update(self):
        self.put("Steve", "fire ball", cooldown_s=3.0)
        entry = self.store.update("steve", "fire ball", cooldown_s=10.0, aliases=["fireball"])
        self.assertEqual((entry["cooldown_s"], entry["aliases"]), (10.0, ["fireball"]))
        self.assertEqual(self.store.find("Steve", "fire ball"), entry)
        self.assertEqual(self.store.version(), 2)

    def test_update_merging_values_keeps_the_others(self):
        self.put("Steve", "fire ball", values={"power": 2, "speed": 1})
        self.assertEqual(self.store.update("Steve", "fire ball", values={"power": 4})["values"], {"power": 4})
        self.store.update("Steve", "fire ball", values={"power": 2, "speed": 1})
        entry = self.store.update("Steve", "fire ball", merge_values=True, values={"power": 4})
        self.assertEqual(entry["values"], {"power": 4, "speed": 1})

    def test_update_merging_merges_what_was_asked_too_and_drops_what_it_names(self):
        self.put("Steve", "beam me", values={"length": 50, "player": "Alex"}, asked={"length": 50, "player": "Alex"})
        # Two edits, each sending only what it changed: both land.
        self.store.update("Steve", "beam me", merge_values=True, values={"length": 80}, asked={"length": 80})
        entry = self.store.update("Steve", "beam me", merge_values=True, drop_asked=["player"],
                                  values={"cooldown": 1, "player": ""}, asked={"cooldown": 1})
        self.assertEqual(entry["values"], {"length": 80, "player": "", "cooldown": 1})
        self.assertEqual(entry["asked"], {"length": 80, "cooldown": 1})
        self.assertEqual(self.store.find("Steve", "beam me"), entry)
        # Without merging, each is replaced whole: a value the action no longer has goes.
        entry = self.store.update("Steve", "beam me", values={"length": 80}, asked={"length": 80})
        self.assertEqual((entry["values"], entry["asked"]), ({"length": 80}, {"length": 80}))
        self.assertEqual(inc.updated({"values": {"a": 1}}, {"asked": {"a": 1}}, merge=True, drop_asked=["a"]),
                         {"values": {"a": 1}, "asked": {}})

    def test_update_of_none_or_of_the_phrase_refused_without_a_write(self):
        self.put("Steve", "fire ball")
        with self.assertRaises(KeyError):
            self.store.update("Alex", "fire ball", cooldown_s=1.0)
        with self.assertRaises(ValueError):
            self.store.update("Steve", "fire ball", id="steve.ice_ball")
        self.assertEqual(self.store.version(), 1)

    def test_remove(self):
        self.put("Steve", "fire ball")
        self.put("Steve", "ice beam")
        self.assertEqual(self.store.remove("STEVE", "Fire Ball")["id"], "steve.fire_ball")
        self.assertEqual([e["id"] for e in self.store.read()["incantations"]], ["steve.ice_beam"])
        self.assertEqual(self.store.version(), 3)

    def test_remove_of_none_does_not_write(self):
        self.put("Steve", "fire ball")
        self.assertIsNone(self.store.remove("Alex", "fire ball"))
        self.assertIsNone(self.store.remove("@a", "fire ball"))
        self.assertEqual(self.store.version(), 1)

    def test_for_player_own_before_at_a(self):
        self.put("@a", "fire ball")
        self.put("Steve", "ice beam")
        self.put("Alex", "zap them")
        self.assertEqual([e["id"] for e in self.store.for_player("steve")], ["steve.ice_beam", "@a.fire_ball"])
        self.assertEqual([e["id"] for e in self.store.for_player("Notch")], ["@a.fire_ball"])

    def test_lookup_by_alias_own_before_at_a(self):
        self.put("@a", "fire ball", aliases=["fireball"])
        self.put("Steve", "flame orb", aliases=["fireball"])
        self.assertEqual(self.store.lookup("steve", "FireBall!")["id"], "steve.flame_orb")
        self.assertEqual(self.store.lookup("steve", "Fire-Ball")["id"], "@a.fire_ball")
        self.assertEqual(self.store.lookup("Alex", "fireball")["id"], "@a.fire_ball")
        self.assertEqual(self.store.lookup("Alex", "fire ball")["id"], "@a.fire_ball")
        self.assertIsNone(self.store.lookup("Alex", "flame orb"))

    def test_a_change_never_writes_over_a_file_it_cannot_read(self):
        os.makedirs(os.path.dirname(self.path))
        for bad in (b"", b'{"version": 3, "incantations": [{"player": "Ste', b'{"version": 3}', b"[]",
                    b'{"version": "3", "incantations": []}'):
            with open(self.path, "wb") as f:
                f.write(bad)
            self.assertEqual(self.store.read(), {"version": 0, "incantations": []})
            for change in (lambda: self.put("Steve", "fire ball"),
                           lambda: self.store.update("Steve", "fire ball", cooldown_s=1.0),
                           lambda: self.store.remove("Steve", "fire ball")):
                with self.assertRaisesRegex(inc.StoreError, "%s .*nothing was changed" % self.path):
                    change()
            with open(self.path, "rb") as f:
                self.assertEqual(f.read(), bad)
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.path))), ["incantations.json", "incantations.lock"])

    def test_a_change_refuses_a_file_it_cannot_open(self):
        os.makedirs(self.path)    # a folder where the file should be
        with self.assertRaisesRegex(inc.StoreError, "cannot be read"):
            self.put("Steve", "fire ball")

    def test_a_write_is_synced_before_the_rename(self):
        synced = []
        real = os.fsync
        with mock.patch.object(inc.os, "fsync", side_effect=lambda fd: synced.append(fd) or real(fd)):
            self.put("Steve", "fire ball")
        self.assertEqual(len(synced), 1)

    def test_writes_at_once_lose_nothing(self):
        def puts(who):
            for i in range(10):
                inc.Store(self.path).put({"player": who, "phrase": "spell %d" % i})
        threads = [threading.Thread(target=puts, args=(who,)) for who in ("Steve", "Alex", "Notch")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        data = self.store.read()
        self.assertEqual((data["version"], len(data["incantations"])), (30, 30))


class PhonemeTests(unittest.TestCase):
    def test_cmudict_first_pronunciation_without_stress(self):
        self.assertEqual(inc.phonemes("fire"), ["F", "AY", "ER"])        # fire(2) is F AY1 R
        self.assertEqual(inc.phonemes("Don't"), ["D", "OW", "N", "T"])

    def test_dictionary_is_sorted_for_the_bisection(self):
        keys = [line.split(b" ", 1)[0] for line in inc._load().split(b"\n")[:-1]]
        self.assertGreater(len(keys), 100000)
        self.assertEqual(keys, sorted(keys))
        for k in (keys[0], keys[len(keys) // 2], keys[-1]):
            self.assertIsNotNone(inc._cmu(k.decode()))
        self.assertIsNone(inc._cmu("blorptastic"))

    def test_rules_for_words_the_dictionary_lacks(self):
        self.assertIsNone(inc._cmu("meh"))
        self.assertEqual(inc.phonemes("meh"), ["M", "EH"])
        for w in ("blorptastic", "zyx", "qwerty", "kamehamehaa", "xyzzyq"):
            got = inc.phonemes(w)
            self.assertTrue(got, w)
            self.assertTrue(set(got) <= set(inc.PHONES), w)

    def test_digits_are_said(self):
        self.assertEqual(inc.phonemes("2"), inc.phonemes("two"))

    def test_phones_ignore_word_boundaries(self):
        self.assertEqual(inc.phones("fire ball"), inc.phones("fireball"))

    def test_split_nonsense_sounds_close_to_the_word(self):
        self.assertLessEqual(inc._distance("kamehameha", "come a hammer ha"), 0.25)


class Layer2Tests(unittest.TestCase):
    def match(self, line, entries=(FIRE_BALL, KAMEHAMEHA)):
        return [(m.entry["phrase"], m.target, m.start, m.end) for m in inc.match_line(line, list(entries))]

    def test_a_misheard_vowel(self):
        self.assertEqual(self.match("fire bowl"), [("fire ball", "fire ball", 0, 2)])

    def test_the_phrase_run_together(self):
        (m,) = inc.match_line("Fireball!", [FIRE_BALL])
        self.assertEqual((m.target, m.start, m.end, m.distance), ("fire ball", 0, 1, 0.0))

    def test_split_nonsense_across_word_boundaries(self):
        self.assertEqual(self.match("come a hammer ha"), [("kamehameha wave", "kamehameha", 0, 4)])

    def test_inside_a_longer_sentence(self):
        self.assertEqual(self.match("ok so uh cast a fire ball at that creeper"), [("fire ball", "fire ball", 5, 7)])

    def test_best_first(self):
        got = inc.match_line("fire bowl and kamehameha", [FIRE_BALL, KAMEHAMEHA])
        self.assertEqual([m.entry["phrase"] for m in got], ["kamehameha wave", "fire ball"])
        self.assertEqual(got[0].distance, 0.0)

    def test_unrelated_sentences_do_not_match(self):
        for line in ("build me a castle", "where is the village", "give me a diamond sword",
                     "there's a creeper behind you", "can you make it night time", ""):
            self.assertEqual(self.match(line), [], line)

    def test_allowance(self):
        self.assertEqual(inc.match_line("fire wall", [FIRE_BALL], allowance=0.1), [])
        self.assertEqual(len(inc.match_line("fire wall", [FIRE_BALL], allowance=0.25)), 1)

    def test_fast(self):
        entries = [{"player": "Steve", "phrase": p, "aliases": a} for p, a in (
            ("fire ball", ["fireball", "fire bowl", "fire bal", "far ball", "fyre ball"]),
            ("kamehameha wave", ["kamehameha", "kah meh hah meh hah wave", "come a hammer ha", "kameha meha wave",
                                 "kamehame wave"]),
            ("ice beam blast", ["icebeam blast", "ice beam", "eyes beam blast", "ice bean blast", "nice beam blast"]),
            ("summon the horde", ["summon horde", "summon the hoard", "some in the horde", "summoned the horde",
                                  "summon the hordes"]),
            ("lightning strike now", ["lightning strike", "lighting strike now", "light ning strike now",
                                      "lightening strike now", "lightning strikes now"]))]
        line = "hey can you build me a big castle over there by the river with a moat"
        inc.match_line(line, entries)
        t = time.perf_counter()
        for _ in range(20):
            inc.match_line(line, entries)
        self.assertLess((time.perf_counter() - t) / 20, 0.02)     # about 1.5 ms here
        t = time.perf_counter()
        inc.match_line("the creeper blew up my house " * 70, entries)   # 2000 characters, read to 200 words
        self.assertLess(time.perf_counter() - t, 0.25)


class DropTests(unittest.TestCase):
    def test_the_specs_table(self):
        for line, left, dropped in (("fire ball", 0, True), ("fire ball now", 1, True),
                                    ("fire ball and build me a wall", 5, False),
                                    ("don't cast fire ball", 2, False)):
            self.assertEqual(inc.words_left(line, FIRE_BALL), left, line)
            self.assertEqual(inc.drop(line, FIRE_BALL), dropped, line)

    def test_fillers_never_count(self):
        self.assertTrue(inc.drop("uh fire ball", FIRE_BALL))
        self.assertTrue(inc.drop("um, fire, uh, ball, hmm", FIRE_BALL))

    def test_an_alias_counts_as_the_phrase(self):
        entry = dict(FIRE_BALL, aliases=["fireball"])
        self.assertEqual(inc.words_left("Fireball now!", entry), 1)

    def test_every_time_it_is_said_is_taken_out(self):
        self.assertTrue(inc.drop("fire ball fire ball", FIRE_BALL))

    def test_a_request_about_the_spell_is_kept(self):
        for line in ("stop fire ball", "remove fire ball", "cancel fire ball", "don't fire ball", "fire ball no",
                     "stop come a hammer ha"):
            entry = KAMEHAMEHA if "hammer" in line else FIRE_BALL
            self.assertEqual(inc.words_left(line, entry), 1, line)
            self.assertFalse(inc.drop(line, entry), line)
        for line in ("fire ball now", "fire ball please"):
            self.assertTrue(inc.drop(line, FIRE_BALL), line)

    def test_found_by_phonemes(self):
        self.assertEqual(inc.words_left("come a hammer ha wave", KAMEHAMEHA), 0)
        self.assertEqual(inc.words_left("far bowl now", FIRE_BALL), 1)

    def test_a_false_spotter_hit_never_drops_a_real_ask(self):
        # "build me a castle" is kamehameha at 0.4: within 0.5, but four words for a one-word alias
        self.assertEqual(inc.words_left("build me a castle", KAMEHAMEHA), 4)
        self.assertFalse(inc.drop("build me a castle", KAMEHAMEHA))

    def test_split_rendering_still_dropped(self):
        self.assertTrue(inc.drop("come a hammer ha", KAMEHAMEHA))
        self.assertTrue(inc.drop("fire ball", FIRE_BALL))

    def test_a_run_of_unlike_length_is_not_the_phrase(self):
        with mock.patch.object(inc, "DROP_SPAN_RATIO", (0.6, 1.1)):
            self.assertEqual(inc.words_left("come a hammer ha", KAMEHAMEHA), 4)   # 10 phonemes for 8

    def test_not_found_takes_nothing_out(self):
        self.assertEqual(inc.words_left("can you make it night time", FIRE_BALL), 6)
        for line in ("make it night", "give me food", "heal me now", "teleport me home"):
            self.assertEqual(inc.words_left(line, FIRE_BALL), 3, line)
            self.assertFalse(inc.drop(line, FIRE_BALL), line)
        self.assertTrue(inc.drop("fire ball", FIRE_BALL))
        self.assertTrue(inc.drop("come a hammer ha", KAMEHAMEHA))


class AliasTests(unittest.TestCase):
    def test_code_aliases_join_the_phrase(self):
        self.assertEqual(inc.code_aliases("fire ball"), ["fireball"])

    def test_code_aliases_split_a_compound(self):
        self.assertEqual(inc.code_aliases("fireball blast"), ["fireballblast", "fire ball blast"])

    def test_code_aliases_join_two_words_into_a_dictionary_word(self):
        self.assertIn("fireball blast", inc.code_aliases("fire ball blast"))

    def test_no_split_where_the_parts_sound_different(self):
        self.assertEqual(inc.code_aliases("together now"), ["togethernow"])

    def test_merge_aliases(self):
        got = inc.merge_aliases("fire ball", ["Fire-Ball", "fire bowl", "zap", ""], ["fireball", "FIRE BOWL"])
        self.assertEqual(got, ["fire bowl", "fireball"])     # zap: 3 phonemes

    def test_merge_aliases_caps(self):
        self.assertEqual(len(inc.merge_aliases("fire ball", ["fire bowl %d" % i for i in range(20)])), 12)
        self.assertEqual(len(inc.merge_aliases("fire ball", ["fire bowl %d" % i for i in range(20)], cap=3)), 3)


class BindWarningTests(unittest.TestCase):
    def test_short_phrase(self):
        (w,) = inc.bind_warnings("zap it", [], [])
        self.assertIn("short", w)
        self.assertEqual(inc.bind_warnings("fire ball", ["fireball"], []), [])   # 6 phonemes: at the floor

    def test_sounds_like_another_phrase(self):
        (w,) = inc.bind_warnings("fire ball", [], [{"player": "Steve", "phrase": "higher ball"}])
        self.assertEqual(w, '"fire ball" sounds like "higher ball", so one may fire for the other.')

    def test_an_alias_sounds_like_another_phrase(self):
        (w,) = inc.bind_warnings("flame orb", ["fire ball"], [{"player": "Steve", "phrase": "higher ball"}])
        self.assertIn('alias "fire ball" sounds like "higher ball"', w)

    def test_shares_its_first_word(self):
        (w,) = inc.bind_warnings("fire storm", [], [{"player": "Steve", "phrase": "fire ball", "aliases": ["fireball"]}])
        self.assertEqual(w, '"fire storm" starts with "fire", as "fire ball" does, so one may fire for the other.')

    def test_a_distinct_phrase_has_none(self):
        self.assertEqual(inc.bind_warnings("ice beam blast", ["icebeam blast"],
                                           [{"player": "Steve", "phrase": "fire ball", "aliases": ["fireball"]},
                                            {"player": "@a", "phrase": "summon the horde"}]), [])

    def test_its_own_entry_is_skipped(self):
        self.assertEqual(inc.bind_warnings("fire ball", [], [{"player": "Steve", "phrase": "Fire Ball"}]), [])


if __name__ == "__main__":
    unittest.main()
