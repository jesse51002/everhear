package com.voiceforgaming.vfg.kws;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.google.gson.JsonParser;
import java.io.ByteArrayInputStream;
import java.nio.charset.StandardCharsets;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.function.Predicate;
import org.junit.jupiter.api.Test;

/** Vosk's grammar: the canonical phrases Vosk knows every word of, the fillers that are no phrase's words, [unk]. */
class VoskGrammarTest {
	/** A stand-in vocabulary: every lower-case word but the made-up ones. */
	private static final Predicate<String> KNOWS = w -> !Set.of("galick", "rasengan", "3").contains(w);
	private static final List<String> FILLERS = List.of("the", "ice", "a", "ball", "fire", "gun", "galick", "the", "beam",
		"steak", "you");

	private static List<String> entries(VoskGrammar g) {
		return JsonParser.parseString(g.json()).getAsJsonArray().asList().stream().map(e -> e.getAsString()).toList();
	}

	@Test
	void thePhrasesComeFirstThenTheFillersThenUnk() {
		VoskGrammar g = VoskGrammar.build(List.of(new Incantation("Ice Beam!", List.of("nice beam", "ice cream"), 3),
			new Incantation("fireball", List.of("fire ball"), 3)), KNOWS, FILLERS);
		assertEquals(List.of("ice beam", "fireball"), g.phrases(), "normalised, aliases left to the spotter");
		assertEquals(List.of("ice beam", "fireball", "the", "a", "ball", "fire", "gun", "steak", "you", "[unk]"),
			entries(g));
		assertEquals(7, g.fillerCount());
		assertTrue(g.leftOut().isEmpty());
	}

	@Test
	void noFillerIsAWordOfABoundPhrase() {
		VoskGrammar g = VoskGrammar.build(List.of(new Incantation("ice beam", List.of(), 3),
			new Incantation("juicy steak", List.of(), 3)), KNOWS, FILLERS);
		List<String> e = entries(g);
		for (String w : List.of("ice", "beam", "steak")) {
			assertEquals(1, e.stream().filter(x -> x.equals(w) || x.contains(w + " ") || x.contains(" " + w)).count(),
				w + " only in its phrase: " + e);
		}
		assertTrue(e.contains("fire") && e.contains("ball"), "words of no phrase stay");
	}

	@Test
	void aPhraseWithAWordVoskDoesNotKnowIsLeftOutWithTheWord() {
		VoskGrammar g = VoskGrammar.build(List.of(new Incantation("galick gun", List.of("garlic gun"), 3),
			new Incantation("Rasengan", List.of(), 3), new Incantation("level 3", List.of(), 3),
			new Incantation("ice beam", List.of(), 3)), KNOWS, FILLERS);
		assertEquals(List.of("ice beam"), g.phrases());
		assertEquals(List.of(new VoskGrammar.LeftOut("galick gun", List.of("galick")),
			new VoskGrammar.LeftOut("rasengan", List.of("rasengan")), new VoskGrammar.LeftOut("level 3", List.of("3"))),
			g.leftOut());
		assertTrue(entries(g).contains("gun"), "a left-out phrase's known words stay fillers");
		assertFalse(entries(g).contains("galick"), "an unknown filler is skipped");
	}

	@Test
	void withNothingVoskCanListenForTheGrammarIsEmptyButSaysWhatWasLeftOut() {
		VoskGrammar g = VoskGrammar.build(List.of(new Incantation("galick gun", List.of(), 3)), KNOWS, FILLERS);
		assertTrue(g.isEmpty());
		assertEquals("", g.json());
		assertEquals(1, g.leftOut().size());
		assertTrue(VoskGrammar.build(List.of(), KNOWS, FILLERS).isEmpty());
	}

	@Test
	void anApostropheIsWrittenAsItIsForVosk() {
		VoskGrammar g = VoskGrammar.build(List.of(new Incantation("don't stop", List.of(), 3)), KNOWS,
			List.of("i'm", "<b>"));
		assertEquals("[\"don't stop\",\"i'm\",\"<b>\",\"[unk]\"]", g.json());
	}

	@Test
	void aPhraseTwoIncantationsShareIsListenedForOnce() {
		VoskGrammar g = VoskGrammar.build(List.of(new Incantation("ice beam", List.of(), 3),
			new Incantation("Ice beam", List.of(), 5)), KNOWS, List.of());
		assertEquals(List.of("ice beam", "[unk]"), entries(g));
	}

	@Test
	void theShippedFillersAreAbout5000CommonWordsAndEverydaySpeech() {
		List<String> f = VoskGrammar.fillers();
		assertEquals(5105, f.size(), "5,000 counted, 105 picked by hand");
		assertEquals(f.size(), new HashSet<>(f).size(), "no repeats");
		assertEquals(List.of("the", "of", "a", "and", "to"), f.subList(0, 5), "most common first");
		assertTrue(f.stream().allMatch(w -> w.matches("[a-z]+('[a-z]+)?")), "lower-case words");
		for (String w : List.of("you", "go", "there", "okay", "yeah", "gonna", "i'm", "zombie", "creeper", "diamond",
				"build", "help", "please")) {
			assertTrue(f.contains(w), w);
		}
	}

	@Test
	void theFillerFileSkipsCommentsAndBlankLines() throws Exception {
		String text = "# a comment\nthe\n\n  of \n# another\nand\n";
		assertEquals(List.of("the", "of", "and"),
			VoskGrammar.readFillers(new ByteArrayInputStream(text.getBytes(StandardCharsets.UTF_8))));
	}
}
