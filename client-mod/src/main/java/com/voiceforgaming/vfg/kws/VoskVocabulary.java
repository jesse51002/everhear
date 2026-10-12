package com.voiceforgaming.vfg.kws;

import java.io.BufferedInputStream;
import java.io.DataInputStream;
import java.io.EOFException;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashSet;
import java.util.Set;

/**
 * The words a Vosk model knows: a grammar may only use these (Vosk warns of any other and leaves
 * it out). A model with {@code graph/words.txt} lists them there, a word and its number a line; a
 * small model, as {@code vosk-model-small-en-us-0.15} is, keeps them only as the first symbol table
 * of {@code graph/Gr.fst}, its language model, which Vosk reads them from. That table is read
 * here, from OpenFst's binary header: the same 152,217 words Vosk's {@code vosk_model_find_word}
 * knows, number for number.
 */
public final class VoskVocabulary {
	static final int FST_MAGIC = 2125659606;
	static final int SYMBOLS_MAGIC = 2125658996;
	private static final int HAS_INPUT_SYMBOLS = 1;
	private static final int HAS_OUTPUT_SYMBOLS = 2;
	private static final int MAX_STRING = 1 << 20;

	private VoskVocabulary() {}

	/** The model's words, from {@code graph/words.txt} or else {@code graph/Gr.fst}. */
	public static Set<String> read(Path modelDir) throws IOException {
		Path words = modelDir.resolve("graph").resolve("words.txt");
		if (Files.isRegularFile(words)) {
			Set<String> out = new HashSet<>();
			for (String line : Files.readAllLines(words, StandardCharsets.UTF_8)) {
				int space = line.lastIndexOf(' ');
				if (space > 0) {
					out.add(line.substring(0, space));
				}
			}
			return out;
		}
		try (InputStream in = new BufferedInputStream(Files.newInputStream(modelDir.resolve("graph").resolve("Gr.fst")),
				1 << 16)) {
			return fromFst(in);
		}
	}

	/**
	 * The first symbol table of an OpenFst binary file: its header (magic, type, arc type, version,
	 * flags, properties, start, states, arcs), then the input symbols, then the output symbols, each
	 * as present in the flags; each table its magic, name, next free key and size, then each symbol
	 * with its key. Numbers are little-endian, a string its 32-bit length and then its bytes.
	 */
	static Set<String> fromFst(InputStream stream) throws IOException {
		DataInputStream in = new DataInputStream(stream);
		if (int32(in) != FST_MAGIC) {
			throw new IOException("not an OpenFst file");
		}
		string(in); // fst type
		string(in); // arc type
		int32(in); // version
		int flags = int32(in);
		in.readFully(new byte[8 + 8 + 8 + 8]); // properties, start, states, arcs
		if ((flags & (HAS_INPUT_SYMBOLS | HAS_OUTPUT_SYMBOLS)) == 0) {
			throw new IOException("the language model has no word list");
		}
		if (int32(in) != SYMBOLS_MAGIC) {
			throw new IOException("not an OpenFst symbol table");
		}
		string(in); // name
		int64(in); // available key
		long size = int64(in);
		if (size < 0 || size > 10_000_000) {
			throw new IOException("a symbol table of " + size + " words");
		}
		Set<String> out = new HashSet<>((int) (size * 4 / 3) + 1);
		for (long i = 0; i < size; i++) {
			out.add(string(in));
			int64(in); // key
		}
		return out;
	}

	private static int int32(DataInputStream in) throws IOException {
		return Integer.reverseBytes(in.readInt());
	}

	private static long int64(DataInputStream in) throws IOException {
		return Long.reverseBytes(in.readLong());
	}

	private static String string(DataInputStream in) throws IOException {
		int n = int32(in);
		if (n < 0 || n > MAX_STRING) {
			throw new EOFException("a string of " + n + " bytes");
		}
		byte[] b = new byte[n];
		in.readFully(b);
		return new String(b, StandardCharsets.UTF_8);
	}
}
