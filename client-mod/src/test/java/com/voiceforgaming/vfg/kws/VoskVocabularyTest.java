package com.voiceforgaming.vfg.kws;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Set;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** The words a Vosk model knows, from its words.txt or the symbol table at the head of its Gr.fst. */
class VoskVocabularyTest {
	@TempDir
	Path dir;

	/** An OpenFst binary header as a small model's Gr.fst starts: an n-gram FST with its word list. */
	static byte[] grFst(int flags, List<String> words) throws IOException {
		ByteArrayOutputStream b = new ByteArrayOutputStream();
		int32(b, VoskVocabulary.FST_MAGIC);
		string(b, "ngram");
		string(b, "standard");
		int32(b, 4);
		int32(b, flags);
		int64(b, 0x25a555550001L);
		int64(b, 1);
		int64(b, 196557);
		int64(b, 0);
		if (flags != 0) {
			int32(b, VoskVocabulary.SYMBOLS_MAGIC);
			string(b, "exp/chain_a/tdnn/lgraph/words.txt");
			int64(b, words.size());
			int64(b, words.size());
			for (int i = 0; i < words.size(); i++) {
				string(b, words.get(i));
				int64(b, i);
			}
		}
		b.write(new byte[] {1, 2, 3, 4}); // the FST itself follows
		return b.toByteArray();
	}

	private static void int32(ByteArrayOutputStream b, int v) {
		b.writeBytes(ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN).putInt(v).array());
	}

	private static void int64(ByteArrayOutputStream b, long v) {
		b.writeBytes(ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN).putLong(v).array());
	}

	private static void string(ByteArrayOutputStream b, String s) {
		byte[] bytes = s.getBytes(StandardCharsets.UTF_8);
		int32(b, bytes.length);
		b.writeBytes(bytes);
	}

	@Test
	void theWordsAreTheFirstSymbolTableOfGrFst() throws Exception {
		Files.createDirectories(dir.resolve("graph"));
		Files.write(dir.resolve("graph/Gr.fst"), grFst(3, List.of("<eps>", "!SIL", "'a", "fire", "fireball", "don't")));
		assertEquals(Set.of("<eps>", "!SIL", "'a", "fire", "fireball", "don't"), VoskVocabulary.read(dir));
	}

	@Test
	void aWordsTxtWinsWhenTheModelHasOne() throws Exception {
		Files.createDirectories(dir.resolve("graph"));
		Files.write(dir.resolve("graph/Gr.fst"), grFst(3, List.of("fire")));
		Files.writeString(dir.resolve("graph/words.txt"), "<eps> 0\nice 1\nbeam 2\n");
		assertEquals(Set.of("<eps>", "ice", "beam"), VoskVocabulary.read(dir));
	}

	@Test
	void anFstWithoutAWordListOrNotAnFstIsAnError() throws Exception {
		assertThrows(IOException.class, () -> VoskVocabulary.fromFst(new ByteArrayInputStream(grFst(0, List.of()))));
		assertThrows(IOException.class, () -> VoskVocabulary.fromFst(new ByteArrayInputStream(new byte[64])));
		byte[] cut = grFst(3, List.of("fire", "fireball"));
		assertThrows(IOException.class, () -> VoskVocabulary.fromFst(new ByteArrayInputStream(cut, 0, cut.length - 20)));
	}
}
