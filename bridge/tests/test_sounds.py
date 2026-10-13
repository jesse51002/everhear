"""Tests for vfg_bridge/sounds.py, the bridge's phoneme matching (from the kit's incantations
module, whose tests these were): words, phonemes, Layer 2, the drop rule, the code aliases and the
bind checks. No server, no network; each test takes milliseconds."""
import time
import unittest
from unittest import mock

from vfg_bridge import sounds as inc

FIRE_BALL = {"player": "Steve", "phrase": "fire ball", "aliases": []}
KAMEHAMEHA = {"player": "Steve", "phrase": "kamehameha wave", "aliases": ["kamehameha"]}
# One-word phrases: the spec's six (Specs/2026-10-11-Prototype_Fast_Casts), with the code's aliases.
ONE_WORD = dict((p, {"player": "Steve", "phrase": p, "aliases": inc.code_aliases(p)})
                for p in ("fireball", "heal", "freeze", "teleport", "dash", "shield", "kamehameha"))
EVERYDAY = ("build me a castle", "where is the village", "give me a diamond sword", "help me", "will you help me",
            "it's really building up", "all of it", "i still feel ill", "that rocket was huge", "she'll do it",
            "the field is big", "ok cool", "can you make it night time", "deal with it", "go up the hill")


class WordsTests(unittest.TestCase):
    def test_normalize_drops_apostrophes_and_marks_hyphens_and_underscores(self):
        self.assertEqual(inc.normalize("  Don't cast\u2014FIRE_ball, Fire-Ball!  Caf\u00e9 don\u2019t "),
                         "dont cast fire ball fire ball cafe dont")

    def test_words(self):
        self.assertEqual(inc.words("Uh, fire ball."), ["uh", "fire", "ball"])

    def test_check_phrase_wants_a_word(self):
        self.assertEqual(inc.check_phrase("Fire-Ball!"), "fire ball")
        self.assertEqual(inc.check_phrase(" Fireball! "), "fireball")
        for bad in ("", "  !! ", None):
            with self.assertRaisesRegex(ValueError, "a word at least"):
                inc.check_phrase(bad)

    def test_one_word(self):
        self.assertTrue(inc.one_word({"phrase": "Fireball!"}))
        self.assertFalse(inc.one_word({"phrase": "fire-ball"}))


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

    def one(self, line, *phrases):
        return [(m.entry["phrase"], m.target, m.start, m.end)
                for m in inc.match_line(line, [ONE_WORD[p] for p in phrases or ONE_WORD])]

    def test_a_one_word_phrase_in_any_sentence_and_at_the_start_of_a_longer_word(self):
        self.assertEqual(self.one("Heal!"), [("heal", "heal", 0, 1)])
        self.assertEqual(self.one("uh shields up"), [("shield", "shield", 1, 2)])
        self.assertEqual(self.one("Craft me an iron sword and a shield for the fight."), [("shield", "shield", 7, 8)])
        self.assertEqual(self.one("I need some healing potions."), [("heal", "heal", 3, 4)])
        self.assertEqual(self.one("open the dashboard"), [("dash", "dash", 2, 3)])

    def test_a_one_word_phrase_runs_only_from_the_start_of_a_word(self):
        self.assertEqual(self.one("haberdashery"), [])
        self.assertEqual(self.one("my flash drive", "dash"), [])
        # Across word boundaries still, from the start of one.
        self.assertEqual(self.one("uh come a hammer ha", "kamehameha"), [("kamehameha", "kamehameha", 1, 5)])
        self.assertEqual(self.one("tell a port", "teleport"), [("teleport", "teleport", 0, 3)])
        self.assertEqual(self.one("fire bowl", "fireball"), [("fireball", "fireball", 0, 2)])
        # A phrase of two words is matched as it was: from anywhere.
        self.assertEqual(self.match("ok then afire ball", [FIRE_BALL]), [("fire ball", "fire ball", 2, 4)])

    def test_a_short_one_word_phrase_is_found_only_as_it_is_said(self):
        for line in EVERYDAY:
            self.assertEqual(self.one(line), [], line)
        self.assertEqual(self.one("heel", "heal"), [("heal", "heal", 0, 1)])    # the same sounds
        self.assertEqual(self.one("eel", "heal"), [("heal", "heal", 0, 1)])     # an HH left out
        self.assertEqual(self.one("hill", "heal"), [])
        # Its mishearings come from its aliases.
        (m,) = inc.match_line("go up the hill", [dict(ONE_WORD["heal"], aliases=["hill"])])
        self.assertEqual((m.target, m.start, m.end, m.distance), ("hill", 3, 4, 0.0))
        # Within the allowance too, when that is tighter.
        self.assertEqual(inc.match_line("eel", [ONE_WORD["heal"]], allowance=0.05), [])
        # Held as a phrase of two words is, "heal" is in most of them.
        with mock.patch.object(inc, "one_word", lambda e: False):
            self.assertGreater(len([line for line in EVERYDAY if self.one(line, "heal")]), 8)

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

    def test_a_one_word_phrase(self):
        shield, heal = ONE_WORD["shield"], ONE_WORD["heal"]
        for line, left, dropped in (("shield", 0, True), ("Shield now!", 1, True), ("uh shield please", 1, True),
                                    ("shield and build me a wall", 5, False), ("don't shield", 1, False),
                                    ("Craft me an iron sword and a shield for the fight.", 10, False)):
            self.assertEqual(inc.words_left(line, shield), left, line)
            self.assertEqual(inc.drop(line, shield), dropped, line)
        self.assertTrue(inc.drop("Fireball!", ONE_WORD["fireball"]))
        self.assertTrue(inc.drop("fire bowl now", ONE_WORD["fireball"]))      # by phonemes
        self.assertTrue(inc.drop("come a hammer ha", ONE_WORD["kamehameha"]))
        # A longer word is not the phrase, and a spotter's misfire never drops a real ask.
        self.assertEqual(inc.words_left("I need some healing potions", heal), 4)
        for line in ("help me", "build it", "will you", "hill now"):
            self.assertEqual(inc.words_left(line, heal), 2, line)
            self.assertFalse(inc.drop(line, heal), line)

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

    def test_code_aliases_of_one_word(self):
        self.assertEqual(inc.code_aliases("fireball"), ["fire ball"])
        for word in ("heal", "dash", "shield", "kamehameha", "teleport"):
            self.assertEqual(inc.code_aliases(word), [], word)
        self.assertEqual(inc.merge_aliases("heal", inc.code_aliases("heal"), ["heel", "heals", "he'll", "Heal!"]),
                         ["heals"])      # heel and hell: 3 phonemes

    def test_merge_aliases(self):
        got = inc.merge_aliases("fire ball", ["Fire-Ball", "fire bowl", "zap", ""], ["fireball", "FIRE BOWL"])
        self.assertEqual(got, ["fire bowl", "fireball"])     # zap: 3 phonemes

    def test_pronunciation_from_the_dictionary_or_the_rules(self):
        self.assertEqual(inc.pronunciation("Ice-Beam!"), [("ice", "AY S", True), ("beam", "B IY M", True)])
        self.assertEqual(inc.pronunciation("galick gun"), [("galick", "G AE L IH K", False), ("gun", "G AH N", True)])
        self.assertEqual(inc.pronunciation(" "), [])

    def test_sound_distance_is_whole_against_whole_over_the_phrases_sounds(self):
        d = inc.sound_distance
        self.assertEqual(d("juicy steak", "juicy stake"), 0.0)
        self.assertEqual(d("fire ball", "Fire-Ball!"), 0.0)
        self.assertAlmostEqual(d("ice beam", "ice been"), 0.9 / 5)       # IH for IY 0.3, N for M 0.6
        self.assertAlmostEqual(d("ice beam", "ice beam now"), 2 / 5)     # N AW put in, 1 each
        self.assertAlmostEqual(d("ice beam", "ice"), 3 / 5)              # B IY M left out, 1 each
        self.assertAlmostEqual(d("speed dash", "speed ash"), 1 / 7)      # D left out
        self.assertAlmostEqual(d("ice beam", "nice beam"), 1 / 5)
        self.assertGreater(d("fire ball", "ice cream"), 0.8)

    def test_checked_variants(self):
        kept, dropped = inc.checked_variants("ice beam", [
            "Ice Been", "ice been", "Ice-Beam", "ice beam now please", "nice beam", "fire ball", "", "nice dean",
            "icebeam", "eyes beam"])
        self.assertEqual(kept, ["ice been", "nice beam", "icebeam", "eyes beam"])
        self.assertEqual(dropped, [("ice been", "a repeat"), ("ice beam", "the phrase itself"),
                                   ("ice beam now please", "6 speech sounds longer"),
                                   ("fire ball", "sounds unlike it (0.66)"), ("nice dean", "sounds unlike it (0.44)")])
        # Longer is weighed as a sound put in is (an HH 0.25, an AH 0.5): "come a hammer a ha" is 3
        # sounds more than CMUdict's K AH M EY AH M EY AH, two of them H, one AH: 1.5. At 3 it goes.
        self.assertEqual(inc.checked_variants("kamehameha", ["come a hammer ha", "come a hammer a ha",
                                                             "come uh huh hammer uh huh", "call me hammer ha"]),
                         (["come a hammer ha", "come a hammer a ha", "come uh huh hammer uh huh"],
                          [("call me hammer ha", "3 speech sounds longer")]))
        self.assertEqual(inc.checked_variants("ice beam", ["ice beam yeah", "ice beam yeah ok"])[1],
                         [("ice beam yeah", "sounds unlike it (0.40)"), ("ice beam yeah ok", "5 speech sounds longer")])
        self.assertEqual(inc.checked_variants("fire ball", []), ([], []))

    def test_everyday_is_the_marks_among_the_aliases(self):
        aliases = ["ice been", "ice cream", "i scream", "nice beam", "eyes beam"]
        self.assertEqual(inc.everyday(aliases, ["Eyes-Beam", "ice cream", "ice cream", "hot fire bowl"]),
                         ["ice cream", "eyes beam"])
        self.assertEqual(inc.everyday(aliases, None), [])
        self.assertEqual(inc.everyday(None, ["ice cream"]), [])

    def test_merge_aliases_caps(self):
        self.assertEqual(inc.ALIAS_CAP, 24)     # the alias call's 20 beside the code's joined and split forms
        self.assertEqual(len(inc.merge_aliases("fire ball", ["fire bowl %d" % i for i in range(30)])), 24)
        self.assertEqual(len(inc.merge_aliases("fire ball", ["fire bowl %d" % i for i in range(20)], cap=3)), 3)

class BindWarningTests(unittest.TestCase):
    def test_short_phrase(self):
        (w,) = inc.bind_warnings("zap it", [], [])
        self.assertIn("short", w)
        self.assertEqual(inc.bind_warnings("fire ball", ["fireball"], []), [])   # 6 phonemes: at the floor

    def test_short_one_word_phrase(self):
        self.assertEqual(inc.bind_warnings("heal", [], []),
                         ['"heal" is short (3 speech sounds), so it may fire by accident: a second word helps.'])
        self.assertEqual(inc.bind_warnings("teleport", [], []), [])
        self.assertEqual(inc.bind_warnings("kamehameha", [], []), [])

    def test_a_one_word_phrase_sounds_like_another_as_layer_2_holds_it(self):
        mine = [{"player": "Steve", "phrase": p, "aliases": inc.code_aliases(p)}
                for p in ("fire ball", "ice wall", "summon the horde", "teleport")]
        self.assertEqual(inc.bind_warnings("heal", [], mine)[1:], [])
        self.assertEqual(inc.bind_warnings("kamehameha", [], mine), [])
        self.assertEqual(inc.bind_warnings("fire ball", [], [ONE_WORD["heal"], ONE_WORD["dash"]]), [])
        (_, w) = inc.bind_warnings("heal", [], [{"player": "Steve", "phrase": "healing light"}])
        self.assertEqual(w, '"heal" sounds like "healing light", so one may fire for the other.')
        (_, w) = inc.bind_warnings("dash", [], [{"player": "Steve", "phrase": "speed dash"}])
        self.assertEqual(w, '"dash" sounds like "speed dash", so one may fire for the other.')
        (w,) = inc.bind_warnings("fire ball", [], [ONE_WORD["fireball"]])
        self.assertEqual(w, '"fire ball" sounds like "fireball", so one may fire for the other.')
        (w,) = inc.bind_warnings("teleport", [], [{"player": "Steve", "phrase": "tell a port"}])
        self.assertEqual(w, '"teleport" sounds like "tell a port", so one may fire for the other.')

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
