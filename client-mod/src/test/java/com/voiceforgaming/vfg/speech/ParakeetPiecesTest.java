package com.voiceforgaming.vfg.speech;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * The model's own BPE cut, on a small vocabulary in merge order as Parakeet's {@code tokens.txt}
 * lists it (merges first, single characters last). The real models' cut was checked against
 * Python's sentencepiece, a BPE model built from each {@code tokens.txt}: the same pieces for all
 * 8,142 words tried (the 5,110 filler words, 3,000 of them capitalised, names and phrases).
 */
class ParakeetPiecesTest {
	/** Merge order: ▁t first, then ▁th… ▁today is a late merge; the characters come last. */
	static final List<String> VOCAB = List.of("<unk>", "▁t", "▁th", "▁a", "in", "▁the", "re", "▁to", "ay", "day",
		"▁today", "▁b", "ra", "▁A", "▁", "e", "t", "o", "a", "n", "i", "h", "r", "d", "y", "b", "A", "I", "J", "M",
		"K", "w", "<blk>");

	private static String code(String piece) {
		return ParakeetPieces.code(VOCAB.indexOf(piece));
	}

	static ParakeetPieces pieces() {
		return new ParakeetPieces(VOCAB);
	}

	@TempDir
	Path dir;

	@Test
	void wordsAreCutByTheEarliestMergeFirst() {
		ParakeetPieces p = pieces();
		assertEquals(List.of("▁the"), p.encode("the"));
		assertEquals(List.of("▁today"), p.encode("today"),
			"one piece, as the model says it: a unigram cut over scores of minus the rank says ▁t od ay");
		assertEquals(List.of("▁the", "re"), p.encode("there"));
		assertEquals(List.of("▁A", "I"), p.encode("AI"), "\"AI\" is two pieces, the first the word \"A\"");
		assertEquals(List.of("▁b", "ra", "in"), p.encode("brain"));
		assertEquals(List.of("▁", "K", "o", "r", "b", "in", "J", "M"), p.encode("KorbinJM"),
			"a character with no ▁ piece of its own starts with ▁ alone");
	}

	@Test
	void aCharacterTheModelCannotSpellLeavesTheWordOut() {
		ParakeetPieces p = pieces();
		assertNull(p.encode("zebra"));
		assertNull(p.coded("the zebra"));
		assertFalse(p.hasCharacter('z'));
		assertTrue(p.hasCharacter('K'));
		assertTrue(p.hasCharacter('▁'));
	}

	@Test
	void codeNamesAreLowercaseLettersUniquePerPieceAndSpecialPiecesKeepTheirNames() {
		Set<String> seen = new HashSet<>();
		for (int id = 0; id < 5000; id++) {
			String c = ParakeetPieces.code(id);
			assertTrue(c.matches("q[a-z]{3,}"), c);
			assertTrue(seen.add(c), "unique: " + c);
		}
		ParakeetPieces p = pieces();
		assertEquals(code("▁A") + " " + code("I"), p.coded("AI"));
		assertEquals(code("▁the") + " " + code("▁today"), p.coded(" the  today "),
			"each word cut on its own; spaces between words are word starts");
		assertNull(p.coded("   "));
	}

	@Test
	void theTextComesBackFromTheCodeNames() {
		ParakeetPieces p = pieces();
		String[] tokens = {code("▁A"), code("I"), code("▁today"), code("e")};
		assertEquals("AI todaye", p.text(tokens));
		assertEquals("the", p.text(new String[] {" " + code("▁the")}), "padding around a token is ignored");
		assertEquals("<unk>", p.text(new String[] {"<unk>"}));
		assertEquals("", p.text(new String[0]));
	}

	@Test
	void theCodedTokensFileIsWrittenOnceBesideTheModel() throws IOException {
		Path tokens = dir.resolve("tokens.txt");
		StringBuilder b = new StringBuilder();
		for (int i = 0; i < VOCAB.size(); i++) {
			b.append(VOCAB.get(i)).append(' ').append(i).append('\n');
		}
		Files.writeString(tokens, b, StandardCharsets.UTF_8);
		ParakeetPieces p = ParakeetPieces.read(tokens);
		assertEquals(VOCAB.size(), p.size());
		Path coded = p.writeCoded(dir);
		assertEquals(dir.resolve(ParakeetPieces.CODED_FILE), coded);
		List<String> lines = Files.readAllLines(coded, StandardCharsets.UTF_8);
		assertEquals("<unk> 0", lines.getFirst());
		assertEquals(ParakeetPieces.code(1) + " 1", lines.get(1));
		assertEquals("<blk> " + (VOCAB.size() - 1), lines.getLast(), "the blank keeps its name");
		Files.setLastModifiedTime(coded, java.nio.file.attribute.FileTime.fromMillis(1000));
		p.writeCoded(dir);
		assertEquals(1000, Files.getLastModifiedTime(coded).toMillis(), "the same content is not written again");
	}

	@Test
	void aBrokenTokensFileDoesNotRead() throws IOException {
		Path tokens = dir.resolve("tokens.txt");
		Files.writeString(tokens, "▁a 0\nb 2\n");
		assertThrows(IOException.class, () -> ParakeetPieces.read(tokens), "id 1 missing");
		Files.writeString(tokens, "nospace\n");
		assertThrows(IOException.class, () -> ParakeetPieces.read(tokens));
	}
}
