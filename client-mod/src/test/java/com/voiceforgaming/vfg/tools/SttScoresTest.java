package com.voiceforgaming.vfg.tools;

import static org.junit.jupiter.api.Assertions.assertEquals;

import com.voiceforgaming.vfg.speech.Hotwords;
import java.util.List;
import org.junit.jupiter.api.Test;

/** How sttCompare finds "AI", names and phrases in a transcript, and parses a set file. */
class SttScoresTest {
	private static final SttCompare.Lists LISTS = new SttCompare.Lists(List.of("DuskJR", "Steve_123", "KorbinJM"),
		List.of("fire ball", "ice aura"));
	private static final List<SttScores.Term> TERMS = SttScores.terms(LISTS, Hotwords.DEFAULT.commands());

	private static SttScores.Term term(String label) {
		return TERMS.stream().filter(t -> t.label().equals(label)).findFirst().orElseThrow();
	}

	private static int count(String label, String text) {
		return SttScores.count(term(label), SttScores.joinNames(SttScores.words(text), TERMS));
	}

	@Test
	void aiIsAWordOfItsOwnSpelledAnyWay() {
		assertEquals(1, count("AI", "Hey AI, give me food."));
		assertEquals(1, count("AI", "hey a.i. give me food"));
		assertEquals(1, count("AI", "Hey A.I., give me food."));
		assertEquals(0, count("AI", "Hey, I give me food."));
		assertEquals(0, count("AI", "give me a I mean an apple"), "\"a\" and \"I\" apart are not AI");
		assertEquals(0, count("AI", "I'm talking today."));
		assertEquals(2, count("AI", "AI? AI!"));
	}

	@Test
	void namesArePlayersWrittenAsTheModWritesThemAndFoundInParts() {
		assertEquals(1, count("DuskJR", "Teleport me to Dusk JR."));
		assertEquals(1, count("DuskJR", "teleport me to dusk j r"));
		assertEquals(1, count("DuskJR", "Teleport me to DuskJR."));
		assertEquals(0, count("DuskJR", "Teleport me to Dusk Junior."));
		assertEquals(1, count("Steve_123", "Give Steve some bread."), "Steve_123 is said Steve");
		assertEquals(1, count("KorbinJM", "Is it Korbin JM's flame aura?"), "a name's possessive counts");
		assertEquals(List.of("give", "steve", "some", "bread", "ai"),
			SttScores.referenceWords("Give Steve_123 some bread, AI.", LISTS.players()));
	}

	@Test
	void phrasesAndCommandWordsAreFoundJoinedOrApart() {
		assertEquals(1, count("fire ball", "I cast fireball at it"));
		assertEquals(1, count("fire ball", "I cast fire ball at it"));
		assertEquals(0, count("fire ball", "fire the ball"));
		assertEquals(2, count("aura", "stop my ice aura, the aura"));
		assertEquals(1, count("ice aura", "stop my ice aura"));
		assertEquals(1, count("unbind", "Yes, unbind freeze."));
		assertEquals(0, count("unbind", "Yes, unpound freeze."));
	}

	@Test
	void wordErrorsJoinANameSaidInParts() {
		List<String> ref = SttScores.joinNames(SttScores.referenceWords("Teleport me to DuskJR.", LISTS.players()), TERMS);
		List<String> hyp = SttScores.joinNames(SttScores.words("Teleport me to Dusk J.R."), TERMS);
		assertEquals(0, SttScores.edits(ref, hyp));
		assertEquals(List.of("give", "me", "sixty", "four", "steak"), SttScores.words("Give me 64 steak."));
	}

	@Test
	void aSetFileHasPlayersPhrasesAndLinesSaidAndMeant() {
		SttCompare.SetFile set = SttCompare.parseSet("test", List.of("# a comment", "players: Ann, BobJR ,",
			"phrases: fire ball", "", "Hey AI.", "Teleport me to Bob J R. => Teleport me to BobJR."));
		assertEquals(List.of("Ann", "BobJR"), set.players());
		assertEquals(List.of("fire ball"), set.phrases());
		assertEquals(2, set.lines().size());
		assertEquals("Hey AI.", set.lines().get(0)[1]);
		assertEquals("Teleport me to Bob J R.", set.lines().get(1)[0]);
		assertEquals("Teleport me to BobJR.", set.lines().get(1)[1]);
	}
}
