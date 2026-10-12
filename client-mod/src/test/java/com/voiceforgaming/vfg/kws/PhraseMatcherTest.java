package com.voiceforgaming.vfg.kws;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.Arrays;
import java.util.List;
import org.junit.jupiter.api.Test;

/** Vosk's partial text into sayings: whole words, revisions, repeats. */
class PhraseMatcherTest {
	private final PhraseMatcher m = new PhraseMatcher(List.of("ice beam", "fireball", "hot fireball", "fire ball",
		"fire"));

	private List<String> update(String partial) {
		return m.update(words(partial)).stream().map(s -> s.phrase() + "#" + s.nth()).toList();
	}

	private List<String> finish(String text) {
		return m.finish(words(text)).stream().map(s -> s.phrase() + "#" + s.nth()).toList();
	}

	private static List<String> words(String text) {
		return text.isEmpty() ? List.of() : Arrays.asList(text.split(" "));
	}

	@Test
	void aPhraseIsASayingOnceItShowsAsWholeWords() {
		assertEquals(List.of(), update("watch"));
		assertEquals(List.of(), update("watch this"));
		assertEquals(List.of(), update("watch this ice"), "half of it");
		assertEquals(List.of("ice beam#1"), update("watch this ice beam"));
		assertEquals(List.of(), update("watch this ice beam now"), "the same saying, shown again");
	}

	@Test
	void wholeWordsOnly() {
		assertEquals(List.of(), update("the fireballs are here"));
		assertEquals(List.of(), update("nice beams"));
		assertEquals(List.of(), update("fires"));
		assertEquals(List.of("fire#1"), update("a fire balloon"), "\"fire\" is a word of it, \"fire ball\" is not");
		assertEquals(List.of(), update("a fire balloon fires"));
	}

	@Test
	void aRevisionThatMovesDropsOrBringsBackASayingCastsNothingNew() {
		assertEquals(List.of("fireball#1"), update("say fireball"));
		assertEquals(List.of(), update("fireball"), "moved to the start");
		assertEquals(List.of(), update("say bull"), "gone a moment");
		assertEquals(List.of(), update("say bull fireball"), "back");
		assertEquals(List.of(), update("the a say bull fireball now"));
	}

	@Test
	void aPhraseSaidTwiceCastsTwice() {
		assertEquals(List.of("fireball#1"), update("fireball"));
		assertEquals(List.of("fireball#2"), update("fireball fireball"));
		assertEquals(List.of(), update("fireball fireball"));
		PhraseMatcher both = new PhraseMatcher(List.of("fireball"));
		assertEquals(2, both.update(words("fireball fireball")).size(), "both at once");
	}

	@Test
	void theLongestPhraseAtTheSameWordsWins() {
		assertEquals(List.of("hot fireball#1"), update("a hot fireball"));
		assertEquals(List.of("fire ball#1"), update("a hot fireball and a fire ball"), "\"fire\" is inside \"fire ball\"");
		PhraseMatcher fresh = new PhraseMatcher(List.of("fireball", "hot fireball"));
		assertEquals(List.of("fireball"), fresh.update(words("fireball")).stream().map(PhraseMatcher.Saying::phrase)
			.toList(), "heard before \"hot\" is");
		assertEquals(List.of("hot fireball"), fresh.update(words("hot fireball")).stream().map(PhraseMatcher.Saying::phrase)
			.toList(), "the longer one is new; the gate makes it the same saying");
	}

	@Test
	void sayingsComeInTheOrderTheyWereSaid() {
		List<PhraseMatcher.Saying> s = m.update(words("ice beam then fireball then ice beam"));
		assertEquals(List.of("ice beam", "fireball", "ice beam"), s.stream().map(PhraseMatcher.Saying::phrase).toList());
		assertEquals(List.of(0, 3, 5), s.stream().map(PhraseMatcher.Saying::first).toList());
		assertEquals(List.of(2, 4, 7), s.stream().map(PhraseMatcher.Saying::end).toList());
	}

	@Test
	void theFinalTakesWhatThePartialsMissedAndTheNextUtteranceCountsAgain() {
		assertEquals(List.of(), update("watch this ice"));
		assertEquals(List.of("ice beam#1"), finish("watch this ice beam"));
		assertEquals(List.of("ice beam#1"), update("ice beam"), "a new utterance");
		assertEquals(List.of(), finish("ice beam"), "its final shows the saying cast already");
		m.update(words("fireball"));
		m.reset();
		assertTrue(update("fireball").contains("fireball#1"), "reset: audio that does not follow on");
	}
}
