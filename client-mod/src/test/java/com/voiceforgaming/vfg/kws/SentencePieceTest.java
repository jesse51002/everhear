package com.voiceforgaming.vfg.kws;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

/**
 * The encoder against Python's sentencepiece: {@code tools/kws_bpe_fixture.py} wrote how
 * sentencepiece splits 45 phrases over the keyword model's own {@code bpe.model}, and that model's
 * pieces and trainer spec (its protobuf with the rest left out), into {@code src/test/resources/kws/}.
 */
class SentencePieceTest {
	/** The fixture's rows: the phrase, and its pieces space-separated. */
	static List<String[]> fixture() throws IOException {
		List<String[]> rows = new ArrayList<>();
		for (String line : new String(resource("bpe-fixture.tsv"), StandardCharsets.UTF_8).split("\n")) {
			if (!line.isEmpty()) {
				rows.add(line.split("\t", 2));
			}
		}
		return rows;
	}

	static byte[] resource(String name) throws IOException {
		try (InputStream in = SentencePieceTest.class.getResourceAsStream("/kws/" + name)) {
			if (in == null) {
				throw new IOException("no test resource kws/" + name);
			}
			return in.readAllBytes();
		}
	}

	static SentencePiece model() throws IOException {
		return SentencePiece.parse(resource("bpe-pieces.model"));
	}

	@Test
	void everyFixturePhraseSplitsAsSentencepieceSplitsIt() throws Exception {
		SentencePiece sp = model();
		List<String[]> rows = fixture();
		assertTrue(rows.size() >= 40, rows.size() + " rows");
		for (String[] row : rows) {
			assertEquals(List.of(row[1].split(" ")), sp.encode(row[0]), "\"" + row[0] + "\"");
		}
	}

	@Test
	void theKeywordModelsBpeModelIsAUnigramModel() throws Exception {
		assertTrue(model().unigram());
	}

	@Test
	void theGameWordsSplitIntoTheModelsUppercasePieces() throws Exception {
		SentencePiece sp = model();
		assertEquals(List.of("▁FI", "RE", "▁BA", "LL"), sp.encode("fire ball"));
		assertEquals(List.of("▁FI", "RE", "B", "AL", "L"), sp.encode("fireball"));
		assertEquals(List.of("▁K", "A", "ME", "HA", "ME", "HA"), sp.encode("Kamehameha!"));
	}

	@Test
	void normalisingKeepsLettersDigitsAndApostrophesInUpperCase() {
		assertEquals("FIRE BALL", SentencePiece.normalize("  Fire-ball!! "));
		assertEquals("DON'T CAST LEVEL 3", SentencePiece.normalize("don't cast: level_3"));
		assertEquals("", SentencePiece.normalize(" ?! "));
	}

	@Test
	void nothingLeftIsNoPieces() throws Exception {
		assertEquals(List.of(), model().encode("...!"));
	}

	/** Unigram, on a vocabulary small enough to follow: the split whose scores sum highest, not the greedy one. */
	@Test
	void aUnigramModelTakesTheSplitWhoseScoresSumHighest() {
		SentencePiece sp = new SentencePiece(Map.of("▁", -1f, "A", -2f, "B", -2f, "C", -2f, "▁AB", -3f, "▁A", -1.5f,
			"BC", -1.5f), true);
		// ▁AB + C = -5; ▁A + BC = -3; ▁ + A + BC = -4.5
		assertEquals(List.of("▁A", "BC"), sp.encode("abc"));
		// a character no piece has comes out as itself, scored 10 below the lowest piece
		assertEquals(List.of("▁A", "Z"), sp.encode("az"));
	}

	/** BPE, on a vocabulary small enough to follow: the highest score merges first, the leftmost on a tie. */
	@Test
	void aBpeModelMergesTheHighestScoringPairFirstAndTheLeftmostOnATie() {
		SentencePiece sp = new SentencePiece(Map.of("▁A", -1f, "AB", -2f, "BC", -2f, "▁AB", -3f, "ABC", -0.5f), false);
		// ▁ A B C: ▁A (-1) beats AB and BC; then ▁A B C: only BC joins; then ▁A BC: nothing
		assertEquals(List.of("▁A", "BC"), sp.encode("abc"));
		SentencePiece tie = new SentencePiece(Map.of("AB", -2f, "BA", -2f), false);
		// ▁ A B A: AB and BA tie; the leftmost, AB, merges
		assertEquals(List.of("▁", "AB", "A"), tie.encode("aba"));
	}

	@Test
	void theTrainerSpecSaysWhichAndPiecesOfOtherTypesAreLeftOut() {
		byte[] bpe = proto(2, piece("▁H", -1f, 1), piece("I", -2f, 1), piece("▁HI", -3f, 1), piece("<unk>", 0f, 2));
		SentencePiece merging = SentencePiece.parse(bpe);
		assertFalse(merging.unigram());
		assertEquals(List.of("▁HI"), merging.encode("hi"));
		byte[] unigram = proto(1, piece("▁H", -1f, 1), piece("I", -1f, 1), piece("▁HI", -3f, 1), piece("<unk>", 0f, 2));
		SentencePiece viterbi = SentencePiece.parse(unigram);
		assertTrue(viterbi.unigram());
		assertEquals(List.of("▁H", "I"), viterbi.encode("hi"));
	}

	@Test
	void bytesThatAreNotAModelAreRefused() {
		assertThrows(IllegalArgumentException.class, () -> SentencePiece.parse(new byte[] {0x0a, 0x7f, 0x01}));
		assertThrows(IllegalArgumentException.class, () -> SentencePiece.parse(new byte[0]));
		assertThrows(IllegalArgumentException.class, () -> SentencePiece.parse(proto(3, piece("A", -1f, 1))));
	}

	// --- a few protobuf bytes, written by hand ---

	private static byte[] piece(String text, float score, int type) {
		ByteArrayOutputStream p = new ByteArrayOutputStream();
		byte[] utf8 = text.getBytes(StandardCharsets.UTF_8);
		p.write(0x0a);
		p.write(utf8.length);
		p.writeBytes(utf8);
		p.write(0x15);
		int bits = Float.floatToIntBits(score);
		for (int i = 0; i < 4; i++) {
			p.write(bits >>> (8 * i));
		}
		p.write(0x18);
		p.write(type);
		return p.toByteArray();
	}

	/** A model of {@code modelType} (1 unigram, 2 BPE): a trainer spec with a string before the type, then pieces. */
	private static byte[] proto(int modelType, byte[]... pieces) {
		ByteArrayOutputStream m = new ByteArrayOutputStream();
		m.write(0x12);
		m.write(6);
		m.writeBytes(new byte[] {0x0a, 0x02, 'i', 'n', 0x18, (byte) modelType});
		for (byte[] p : pieces) {
			m.write(0x0a);
			m.write(p.length);
			m.writeBytes(p);
		}
		return m.toByteArray();
	}
}
