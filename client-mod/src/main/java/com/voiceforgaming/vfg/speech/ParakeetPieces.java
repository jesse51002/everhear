package com.voiceforgaming.vfg.speech;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * A Parakeet model's pieces, from its {@code tokens.txt}, for hotwords.
 *
 * <p>Parakeet's tokenizer is a SentencePiece BPE model of 1024 pieces, listed in {@code tokens.txt}
 * in merge order. sherpa-onnx cuts plain-text hotwords into pieces with a unigram (Viterbi) encoder
 * over a {@code bpe.vocab}, which does not cut words as the BPE tokenizer the model was trained on
 * does: given scores of minus the rank, it cuts "today" as {@code ▁t od ay} where the model says
 * {@code ▁today}, and about a third of common English words differ. A hotword boosts exactly the
 * pieces it is cut into, so a mis-cut word is boosted along a path the model seldom takes.
 *
 * <p>So the mod cuts each hotword itself, with the model's own BPE merges ({@link #encode}), and
 * hands sherpa-onnx the pieces under code names: a copy of {@code tokens.txt} whose pieces are
 * renamed {@code qbab}, {@code qbac}… ({@link #writeCoded}), read with {@code modeling_unit cjkchar},
 * which looks each space-separated word of a hotword up as it is. The recognizer then answers in
 * code names too, and {@link #text} turns them back into the model's text.
 */
public final class ParakeetPieces {
	/** SentencePiece's word-start mark. */
	public static final char WORD_START = '▁';
	/** The copy of {@code tokens.txt} with code names, written beside it. */
	public static final String CODED_FILE = "tokens-hotwords.txt";

	private final List<String> pieces; // by id
	private final Map<String, Integer> ids = new HashMap<>();
	private final Set<Integer> characters = new HashSet<>(); // code points that are a piece of their own
	private final List<String> codes; // by id
	private final Map<String, String> fromCode = new HashMap<>();

	public ParakeetPieces(List<String> piecesById) {
		this.pieces = List.copyOf(piecesById);
		this.codes = new ArrayList<>(pieces.size());
		for (int id = 0; id < pieces.size(); id++) {
			String p = pieces.get(id);
			ids.putIfAbsent(p, id);
			if (p.codePointCount(0, p.length()) == 1) {
				characters.add(p.codePointAt(0));
			}
			String code = special(p) ? p : code(id);
			codes.add(code);
			fromCode.put(code, p);
		}
	}

	/** The pieces of a {@code tokens.txt}: one {@code <piece> <id>} a line. */
	public static ParakeetPieces read(Path tokensTxt) throws IOException {
		List<String> byId = new ArrayList<>();
		for (String line : Files.readAllLines(tokensTxt, StandardCharsets.UTF_8)) {
			if (line.isBlank()) {
				continue;
			}
			int space = line.lastIndexOf(' ');
			if (space <= 0) {
				throw new IOException("not a tokens.txt line: " + line);
			}
			int id = Integer.parseInt(line.substring(space + 1).trim());
			while (byId.size() <= id) {
				byId.add(null);
			}
			byId.set(id, line.substring(0, space));
		}
		for (int i = 0; i < byId.size(); i++) {
			if (byId.get(i) == null) {
				throw new IOException("tokens.txt has no piece " + i);
			}
		}
		return new ParakeetPieces(byId);
	}

	private static boolean special(String piece) {
		return piece.startsWith("<") && piece.endsWith(">");
	}

	/** {@code q} and at least three lowercase letters, unique for each id: letters alone stay one word to cjkchar. */
	static String code(int id) {
		StringBuilder b = new StringBuilder();
		for (int n = id + 26 * 26; n > 0; n /= 26) {
			b.append((char) ('a' + n % 26));
		}
		return "q" + b.reverse();
	}

	public int size() {
		return pieces.size();
	}

	/** Whether the model can spell this character (it is a piece of its own). */
	public boolean hasCharacter(int codePoint) {
		return characters.contains(codePoint);
	}

	/**
	 * One word (no spaces) cut into the model's pieces as its BPE tokenizer cuts it: {@code ▁} and
	 * the word's characters, then, again and again, the adjacent two whose join is the piece of
	 * lowest id (the earliest merge), the leftmost on a tie, until no two join into a piece. Returns
	 * {@code null} when a character is not a piece of the model's.
	 */
	public List<String> encode(String word) {
		List<String> symbols = new ArrayList<>();
		symbols.add(String.valueOf(WORD_START));
		for (int i = 0; i < word.length(); ) {
			int cp = word.codePointAt(i);
			if (!characters.contains(cp)) {
				return null;
			}
			symbols.add(new String(Character.toChars(cp)));
			i += Character.charCount(cp);
		}
		if (!characters.contains((int) WORD_START)) {
			// A model whose ▁ is never alone: start with ▁ joined to the first character.
			if (symbols.size() < 2 || !ids.containsKey(symbols.get(0) + symbols.get(1))) {
				return null;
			}
			symbols.set(1, symbols.get(0) + symbols.get(1));
			symbols.remove(0);
		}
		while (symbols.size() > 1) {
			int best = -1;
			int bestId = Integer.MAX_VALUE;
			for (int i = 0; i + 1 < symbols.size(); i++) {
				Integer id = ids.get(symbols.get(i) + symbols.get(i + 1));
				if (id != null && id < bestId) {
					bestId = id;
					best = i;
				}
			}
			if (best < 0) {
				break;
			}
			symbols.set(best, symbols.get(best) + symbols.remove(best + 1));
		}
		return symbols;
	}

	/** A phrase's words, each cut by {@link #encode}, as code names joined by spaces; {@code null} if any word cannot be. */
	public String coded(String phrase) {
		StringBuilder b = new StringBuilder();
		for (String word : phrase.trim().split("\\s+")) {
			if (word.isEmpty()) {
				continue;
			}
			List<String> cut = encode(word);
			if (cut == null) {
				return null;
			}
			for (String piece : cut) {
				b.append(b.isEmpty() ? "" : " ").append(codes.get(ids.get(piece)));
			}
		}
		return b.isEmpty() ? null : b.toString();
	}

	/** The model's text from the pieces a recognizer reading the coded file answered: joined, {@code ▁} as spaces. */
	public String text(String[] codedTokens) {
		StringBuilder b = new StringBuilder();
		for (String t : codedTokens) {
			String piece = fromCode.get(t.strip());
			b.append(piece != null ? piece : t);
		}
		return b.toString().replace(WORD_START, ' ').trim();
	}

	/**
	 * Writes {@code tokens.txt} with code names ({@link #CODED_FILE}) into {@code dir}, through a
	 * temporary file, unless it is there already with the same content; returns its path.
	 */
	public Path writeCoded(Path dir) throws IOException {
		StringBuilder b = new StringBuilder();
		for (int id = 0; id < codes.size(); id++) {
			b.append(codes.get(id)).append(' ').append(id).append('\n');
		}
		String content = b.toString();
		Path file = dir.resolve(CODED_FILE);
		if (Files.isRegularFile(file) && Files.readString(file, StandardCharsets.UTF_8).equals(content)) {
			return file;
		}
		Path tmp = dir.resolve(CODED_FILE + ".tmp");
		Files.writeString(tmp, content, StandardCharsets.UTF_8);
		Files.move(tmp, file, StandardCopyOption.REPLACE_EXISTING);
		return file;
	}
}
