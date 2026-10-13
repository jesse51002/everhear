package com.voiceforgaming.vfg.speech;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotSame;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.voiceforgaming.vfg.kws.Incantation;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.Test;

class HotwordsTest {
	private static List<String> texts(Hotwords h) {
		return h.entries().stream().map(Hotwords.Entry::text).toList();
	}

	@Test
	void playerNamesAreCutAsTheModelWritesThem() {
		assertEquals("Korbin JM", Hotwords.nameWords("KorbinJM"), "cut where lowercase turns to a capital");
		assertEquals("Zephyrandrix", Hotwords.nameWords("Zephyrandrix"));
		assertEquals("Steve", Hotwords.nameWords("Steve_123"), "digits and underscores dropped");
		assertEquals("Dusk JR", Hotwords.nameWords("DuskJR"));
		assertEquals("Steve JM", Hotwords.nameWords("SteveJM"));
		assertEquals("dark Wolf", Hotwords.nameWords("dark_Wolf99"));
		assertEquals("Mc Fly", Hotwords.nameWords("McFly"));
		assertEquals("ALEX", Hotwords.nameWords("ALEX"), "capitals in a row stay one word");
		assertEquals("x XSniper Xx", Hotwords.nameWords("xXSniperXx"));
		assertEquals("Zoë", Hotwords.nameWords("Zoë"));
		assertNull(Hotwords.nameWords("A1"), "fewer than two letters");
		assertNull(Hotwords.nameWords("___"));
		assertNull(Hotwords.nameWords(null));
	}

	@Test
	void phrasesAreLowercaseWords() {
		assertEquals("fire ball", Hotwords.phraseWords("Fire  Ball!"));
		assertEquals("don't stop", Hotwords.phraseWords("don't-stop"));
		assertEquals("level 2 heal", Hotwords.phraseWords("level 2 heal"));
		assertNull(Hotwords.phraseWords("!!"));
	}

	@Test
	void theListIsTheAiNamesThePlayersThePhrasesAndTheCommandWordsWithoutRepeats() {
		Hotwords h = new Hotwords();
		assertEquals(List.of("AI", "brain", "unbind", "incantation", "incantations", "aura", "cooldown", "mobs", "spell",
			"spells"), texts(h), "with nobody online and no phrases");
		assertTrue(h.players(List.of("Steve_123", "KorbinJM", "KorbinJM", " ", "A1")));
		assertTrue(h.phrases(List.of("Fire Ball", "freeze", "fire ball", "aura"), null));
		assertEquals(List.of("AI", "brain", "Korbin JM", "Steve", "fire ball", "freeze", "aura", "unbind", "incantation",
			"incantations", "cooldown", "mobs", "spell", "spells"), texts(h),
			"names sorted; a phrase that is a command word is listed once");
		Hotwords higher = new Hotwords(Hotwords.DEFAULT.withBoosts(1, 1, 1, 1, 2));
		higher.phrases(List.of("aura"), null);
		assertEquals(Hotwords.Kind.COMMAND, higher.entries().stream().filter(e -> e.text().equals("aura")).findFirst()
			.orElseThrow().kind(), "the same word keeps the higher boost");
		assertEquals(Hotwords.Kind.PHRASE, h.entries().stream().filter(e -> e.text().equals("aura")).findFirst()
			.orElseThrow().kind(), "and the first kind on a tie");
	}

	@Test
	void anOlderBridgeGivesThePlayersOwnPhrases() {
		Hotwords h = new Hotwords();
		h.phrases(null, List.of(new Incantation("ice wall", List.of("ice hall"), 3), new Incantation("Heal", List.of(), 0)));
		assertEquals(List.of("ice wall", "heal"), h.phrases(), "the phrases, never the aliases");
		h.phrases(List.of("fire ball"), List.of(new Incantation("ice wall", List.of(), 3)));
		assertEquals(List.of("fire ball"), h.phrases(), "server_phrases, when the bridge sends them");
		h.phrases(List.of(), List.of(new Incantation("ice wall", List.of(), 3)));
		assertEquals(List.of(), h.phrases(), "an empty server list is the server's: no phrases at all");
	}

	@Test
	void eachKindHasItsOwnBoostAndTheStringNamesPiecesByCode() {
		ParakeetPieces pieces = ParakeetPiecesTest.pieces();
		Hotwords h = new Hotwords(Hotwords.DEFAULT.withBoosts(0.5, 1.0, 2.0, 3.0, 4.0).withCommands(List.of()));
		h.players(List.of("KorbinJM", "Zed")); // the small vocabulary has no Z
		h.phrases(List.of("the today"), null);
		String s = h.forModel(pieces);
		List<String> parts = List.of(s.split("/"));
		assertEquals(pieces.coded("AI") + " :0.50", parts.get(0));
		assertEquals(pieces.coded("brain") + " :1.00", parts.get(1));
		assertEquals(pieces.coded("Korbin JM") + " :2.00", parts.get(2));
		assertEquals(pieces.coded("ed") + " :2.00", parts.get(3), "a character the model cannot spell is dropped");
		assertEquals(pieces.coded("the today") + " :3.00", parts.get(4));
		assertEquals(5, parts.size());
		assertEquals(5, h.lastWordCount());
		assertFalse(s.contains("\n"));
	}

	@Test
	void aLongNameCanHaveABoostOfItsOwn() {
		ParakeetPieces pieces = ParakeetPiecesTest.pieces();
		Hotwords h = new Hotwords(Hotwords.DEFAULT.withCommands(List.of()).withLongNames(3.0, 5));
		h.players(List.of("KorbinJM")); // in the small vocabulary: ▁ K o r b in, ▁ J M, 9 pieces
		String s = h.forModel(pieces);
		assertTrue(s.contains(pieces.coded("Korbin JM") + " :3.00"), s);
		Hotwords shortOnly = new Hotwords(Hotwords.DEFAULT.withCommands(List.of()).withLongNames(3.0, 20));
		shortOnly.players(List.of("KorbinJM"));
		assertTrue(shortOnly.forModel(pieces).contains(pieces.coded("Korbin JM") + " :1.00"), "under the length: the name boost");
		assertEquals(1.0, Hotwords.DEFAULT.nameBoost(3));
	}

	@Test
	void the06BBoostsLongNamesMoreAndTheStringIsKeptForEachModelsBoosts() {
		ParakeetPieces pieces = ParakeetPiecesTest.pieces();
		Hotwords h = new Hotwords();
		h.players(List.of("KorbinJM", "Ann"));
		String small = h.forModel(pieces);
		assertTrue(small.contains(pieces.coded("Korbin JM") + " :1.00"), small);
		String big = h.forModel(pieces, Hotwords.PARAKEET_06B);
		assertTrue(big.contains(pieces.coded("Korbin JM") + " :2.50"), "9 pieces: a long name");
		assertTrue(big.contains(pieces.coded("Ann") + " :1.00"), "▁A n n, 3 pieces: a short one " + big);
		assertSame(big, h.forModel(pieces, Hotwords.PARAKEET_06B));
		assertNotSame(big, h.forModel(pieces));
	}

	@Test
	void aOneWordPhraseCanHaveABoostOfItsOwn() {
		ParakeetPieces pieces = ParakeetPiecesTest.pieces();
		Hotwords h = new Hotwords(Hotwords.DEFAULT.withCommands(List.of()).withOneWordPhrases(0.25));
		h.phrases(List.of("today", "the today"), null);
		String s = h.forModel(pieces);
		assertTrue(s.contains(pieces.coded("today") + " :0.25"), s);
		assertTrue(s.contains(pieces.coded("the today") + " :1.00"), s);
		assertEquals(1.0, Hotwords.DEFAULT.withBoosts(1, 1, 1, 1, 1).phraseBoost("freeze"),
			"by default a one-word phrase is boosted as any phrase");
	}

	@Test
	void theStringIsBuiltOnceForEachChange() {
		ParakeetPieces pieces = ParakeetPiecesTest.pieces();
		Hotwords h = new Hotwords();
		String first = h.forModel(pieces);
		assertSame(first, h.forModel(pieces), "nothing changed: the same string");
		assertFalse(h.players(List.of()), "the same players: no change");
		assertSame(first, h.forModel(pieces));
		assertTrue(h.players(List.of("Andy")));
		String second = h.forModel(pieces);
		assertNotSame(first, second);
		assertTrue(second.contains(pieces.coded("Andy")));
		assertNotSame(second, h.forModel(ParakeetPiecesTest.pieces()), "another model's pieces: built again");
		h.clear();
		assertEquals(first, h.forModel(pieces), "left the server: no players, no phrases");
	}

	@Test
	void namesAndPhrasesAreCapped() {
		Hotwords h = new Hotwords(new Hotwords.Settings(1, 1, 1, 1, 1, List.of(), false, 2, 1));
		h.players(List.of("Alpha", "Bravo", "Charlie"));
		h.phrases(List.of("one", "two"), null);
		assertEquals(List.of("AI", "brain", "Alpha", "Bravo", "one"), texts(h));
	}

	@Test
	void namesCanBeKeptWholeForMeasuring() {
		Hotwords h = new Hotwords(Hotwords.DEFAULT.withSplitNames(false).withCommands(List.of()));
		h.players(List.of("KorbinJM", "Steve_123", "__"));
		assertEquals(List.of("AI", "brain", "KorbinJM", "Steve 123"), texts(h));
		assertTrue(Hotwords.DEFAULT.splitNames(), "the mod cuts them");
	}

	@Test
	void brainCanBeListedWithACapitalToo() {
		Hotwords h = new Hotwords(Hotwords.DEFAULT.withCapitalBrain(true).withCommands(List.of()));
		h.phrases(List.of("fire ball"), null);
		assertEquals(List.of("AI", "brain", "Brain", "fire ball"), texts(h));
	}

	/**
	 * The client thread and {@code vfg-incantations} replace the lists while {@code vfg-stt} reads:
	 * the reader always gets a whole list, never half of one, and in the end the last of each.
	 */
	@Test
	void writersOnTwoThreadsAndAReaderOnAThirdNeverSeeHalfAList() throws Exception {
		ParakeetPieces pieces = ParakeetPiecesTest.pieces();
		Hotwords h = new Hotwords(Hotwords.DEFAULT.withCommands(List.of()));
		CountDownLatch go = new CountDownLatch(1);
		AtomicReference<Throwable> failed = new AtomicReference<>();
		Thread names = new Thread(() -> {
			await(go);
			for (int i = 0; i < 2000; i++) {
				h.players(i % 2 == 0 ? List.of("Andy", "Ian") : List.of("Andy"));
			}
			h.players(List.of("Andy", "Ian"));
		});
		Thread phrases = new Thread(() -> {
			await(go);
			for (int i = 0; i < 2000; i++) {
				h.phrases(i % 2 == 0 ? List.of("the", "today") : List.of("today"), null);
			}
			h.phrases(List.of("the", "today"), null);
		});
		List<String> seen = new ArrayList<>();
		Thread reader = new Thread(() -> {
			await(go);
			try {
				for (int i = 0; i < 5000; i++) {
					String s = h.forModel(pieces);
					int words = s.split("/").length;
					// AI and brain, then none, one or two names, and none, one or two phrases
					assertTrue(words >= 2 && words <= 6, s);
					assertTrue(s.startsWith(pieces.coded("AI") + " :"), s);
					if (i % 1000 == 0) {
						seen.add(s);
					}
				}
			} catch (Throwable t) {
				failed.set(t);
			}
		});
		names.start();
		phrases.start();
		reader.start();
		go.countDown();
		names.join();
		phrases.join();
		reader.join();
		if (failed.get() != null) {
			throw new AssertionError(failed.get());
		}
		assertEquals(List.of("Andy", "Ian"), h.players());
		assertEquals(List.of("the", "today"), h.phrases());
		String last = h.forModel(pieces);
		assertEquals(6, last.split("/").length, "after the writers are done, the reader gets the last lists: " + last);
		assertTrue(last.contains(pieces.coded("Ian")));
		assertFalse(seen.isEmpty());
	}

	private static void await(CountDownLatch latch) {
		try {
			latch.await();
		} catch (InterruptedException e) {
			Thread.currentThread().interrupt();
		}
	}
}
