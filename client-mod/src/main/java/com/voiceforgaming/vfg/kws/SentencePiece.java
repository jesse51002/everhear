package com.voiceforgaming.vfg.kws;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * A phrase to the keyword model's tokens: SentencePiece's own encoding over the model's
 * {@code bpe.model}, as Python's {@code sentencepiece} encodes it, so the box sends plain phrases
 * and the tokens are made here.
 *
 * <p>The model is a protobuf, of which only this is read: each piece (field 1: its text, score and
 * type) and the trainer's model type (field 2, then 3). Despite its name, the GigaSpeech keyword
 * model's {@code bpe.model} is a <i>unigram</i> model (icefall trains {@code unigram_500}), and a
 * unigram model is not encoded by merging: the text is split where the pieces' scores (their log
 * probabilities) sum highest, as sentencepiece's Viterbi does it, a character no piece starts with
 * becoming an unknown piece scored 10 below the lowest. A BPE model is encoded by merging, the
 * adjacent pair whose joined piece scores highest first, the leftmost on a tie, until none joins.
 * Either way the text is first split into characters behind a leading {@code ▁}, with {@code ▁}
 * for each space. A character the model has no piece for comes out as itself, and the keyword
 * it is in is then left out, as the model has no token for it.
 *
 * <p>The normalising is the mod's own, not the model's: upper case (the model's tokens are),
 * letters, digits and apostrophes kept, anything else a space, spaces collapsed. Pieces of the types
 * sentencepiece never produces from text (unknown, control, byte) are left out, and so are unused
 * ones, which this model has none of.
 */
public final class SentencePiece {
	/** SentencePiece's word boundary, U+2581. */
	public static final String SPACE = "▁";
	/** How far below the lowest piece an unknown character scores, in a unigram model. */
	private static final float UNKNOWN_PENALTY = 10f;

	private static final int NORMAL = 1;
	private static final int USER_DEFINED = 4;
	private static final int UNIGRAM = 1;
	private static final int BPE = 2;

	private final Map<String, Float> scores;
	private final Map<String, Boolean> userDefined;
	private final boolean unigram;
	private final int longest;
	private final float lowest;
	private final float highest;

	/**
	 * The pieces the encoding may produce, with their scores; {@code unigram} for a unigram model,
	 * else BPE. No piece here is user-defined.
	 */
	public SentencePiece(Map<String, Float> scores, boolean unigram) {
		this(scores, Map.of(), unigram);
	}

	private SentencePiece(Map<String, Float> scores, Map<String, Boolean> userDefined, boolean unigram) {
		this.scores = Map.copyOf(scores);
		this.userDefined = Map.copyOf(userDefined);
		this.unigram = unigram;
		int max = 1;
		float lo = Float.MAX_VALUE;
		float hi = -Float.MAX_VALUE;
		for (Map.Entry<String, Float> e : scores.entrySet()) {
			max = Math.max(max, e.getKey().codePointCount(0, e.getKey().length()));
			if (!userDefined.containsKey(e.getKey())) {
				lo = Math.min(lo, e.getValue());
				hi = Math.max(hi, e.getValue());
			}
		}
		this.longest = max;
		this.lowest = lo == Float.MAX_VALUE ? 0f : lo;
		this.highest = hi == -Float.MAX_VALUE ? 0f : hi;
	}

	/** Reads a {@code bpe.model} file. */
	public static SentencePiece load(Path model) throws IOException {
		return parse(Files.readAllBytes(model));
	}

	/** Reads the pieces and the model type from a SentencePiece model's protobuf bytes. */
	public static SentencePiece parse(byte[] model) {
		Map<String, Float> scores = new HashMap<>();
		Map<String, Boolean> userDefined = new HashMap<>();
		int type = UNIGRAM; // the protobuf's default
		Reader r = new Reader(model, 0, model.length);
		while (r.more()) {
			long key = r.varint();
			if ((key & 7) == 2 && ((key >>> 3) == 1 || (key >>> 3) == 2)) {
				int size = (int) r.varint();
				Reader inner = new Reader(model, r.at, r.at + size);
				if ((key >>> 3) == 1) {
					piece(inner, scores, userDefined);
				} else {
					type = modelType(inner, type);
				}
				r.at += size;
			} else {
				r.skip((int) (key & 7));
			}
		}
		if (scores.isEmpty()) {
			throw new IllegalArgumentException("no pieces in the model");
		}
		if (type != UNIGRAM && type != BPE) {
			throw new IllegalArgumentException("a SentencePiece model of type " + type + ", not unigram or BPE");
		}
		return new SentencePiece(scores, userDefined, type == UNIGRAM);
	}

	private static void piece(Reader r, Map<String, Float> scores, Map<String, Boolean> userDefined) {
		String piece = null;
		float score = 0f;
		int type = NORMAL;
		while (r.more()) {
			long key = r.varint();
			int field = (int) (key >>> 3);
			int wire = (int) (key & 7);
			if (field == 1 && wire == 2) {
				int size = (int) r.varint();
				piece = new String(r.bytes, r.at, size, StandardCharsets.UTF_8);
				r.at += size;
			} else if (field == 2 && wire == 5) {
				score = Float.intBitsToFloat(r.fixed32());
			} else if (field == 3 && wire == 0) {
				type = (int) r.varint();
			} else {
				r.skip(wire);
			}
		}
		if (piece != null && (type == NORMAL || type == USER_DEFINED) && !scores.containsKey(piece)) {
			scores.put(piece, score);
			if (type == USER_DEFINED) {
				userDefined.put(piece, true);
			}
		}
	}

	/** The trainer spec's {@code model_type} (field 3). */
	private static int modelType(Reader r, int fallback) {
		int type = fallback;
		while (r.more()) {
			long key = r.varint();
			if ((key >>> 3) == 3 && (key & 7) == 0) {
				type = (int) r.varint();
			} else {
				r.skip((int) (key & 7));
			}
		}
		return type;
	}

	/** Whether the model is a unigram one (else BPE). */
	public boolean unigram() {
		return unigram;
	}

	/** The mod's normalising: upper case; A–Z, 0–9 and {@code '} kept; anything else a space; collapsed. */
	public static String normalize(String text) {
		StringBuilder out = new StringBuilder(text.length());
		boolean space = true;
		for (char c : text.toUpperCase(Locale.ROOT).toCharArray()) {
			boolean keep = (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '\'';
			if (keep) {
				out.append(c);
				space = false;
			} else if (!space) {
				out.append(' ');
				space = true;
			}
		}
		int end = out.length();
		while (end > 0 && out.charAt(end - 1) == ' ') {
			end--;
		}
		return out.substring(0, end);
	}

	/** The pieces of {@code text}, normalised first; empty when nothing is left of it. */
	public List<String> encode(String text) {
		String normalized = normalize(text);
		if (normalized.isEmpty()) {
			return List.of();
		}
		List<String> chars = new ArrayList<>();
		(SPACE + normalized.replace(" ", SPACE)).codePoints().forEach(cp -> chars.add(new String(Character.toChars(cp))));
		return unigram ? viterbi(chars) : merge(chars);
	}

	/**
	 * The split whose pieces' scores sum highest, as sentencepiece's optimised Viterbi finds it: for
	 * each start, in order, every piece starting there, shortest first, improves the best path ending
	 * where it ends only when it scores strictly higher; a character no piece starts with is unknown.
	 */
	private List<String> viterbi(List<String> chars) {
		int n = chars.size();
		float[] best = new float[n + 1];
		int[] from = new int[n + 1];
		java.util.Arrays.fill(from, -1);
		float unknown = lowest - UNKNOWN_PENALTY;
		for (int start = 0; start < n; start++) {
			if (start > 0 && from[start] < 0) {
				continue; // no path reaches here
			}
			boolean single = false;
			StringBuilder piece = new StringBuilder();
			for (int end = start + 1; end <= n && end - start <= longest; end++) {
				piece.append(chars.get(end - 1));
				Float score = scores.get(piece.toString());
				if (score == null) {
					continue;
				}
				float s = userDefined.containsKey(piece.toString())
					? piece.toString().getBytes(StandardCharsets.UTF_8).length * highest - 0.1f : score;
				float candidate = best[start] + s;
				if (from[end] < 0 || candidate > best[end]) {
					best[end] = candidate;
					from[end] = start;
				}
				if (end == start + 1) {
					single = true;
				}
			}
			if (!single) {
				float candidate = best[start] + unknown;
				if (from[start + 1] < 0 || candidate > best[start + 1]) {
					best[start + 1] = candidate;
					from[start + 1] = start;
				}
			}
		}
		List<String> out = new ArrayList<>();
		for (int end = n; end > 0; end = from[end]) {
			out.add(String.join("", chars.subList(from[end], end)));
		}
		java.util.Collections.reverse(out);
		return out;
	}

	/** BPE: the adjacent pair whose joined piece scores highest merges first, the leftmost on a tie. */
	private List<String> merge(List<String> symbols) {
		while (symbols.size() > 1) {
			int best = -1;
			float bestScore = Float.NEGATIVE_INFINITY;
			for (int i = 0; i + 1 < symbols.size(); i++) {
				Float score = scores.get(symbols.get(i) + symbols.get(i + 1));
				if (score != null && (best < 0 || score > bestScore)) {
					best = i;
					bestScore = score;
				}
			}
			if (best < 0) {
				break;
			}
			symbols.set(best, symbols.get(best) + symbols.remove(best + 1));
		}
		return symbols;
	}

	/** Protobuf's wire format, just enough of it. */
	private static final class Reader {
		final byte[] bytes;
		int at;
		final int end;

		Reader(byte[] bytes, int at, int end) {
			if (end > bytes.length || at > end) {
				throw new IllegalArgumentException("not a SentencePiece model");
			}
			this.bytes = bytes;
			this.at = at;
			this.end = end;
		}

		boolean more() {
			return at < end;
		}

		long varint() {
			long value = 0;
			for (int shift = 0; shift < 64; shift += 7) {
				if (at >= end) {
					throw new IllegalArgumentException("not a SentencePiece model");
				}
				byte b = bytes[at++];
				value |= (long) (b & 0x7f) << shift;
				if ((b & 0x80) == 0) {
					return value;
				}
			}
			throw new IllegalArgumentException("not a SentencePiece model");
		}

		int fixed32() {
			if (at + 4 > end) {
				throw new IllegalArgumentException("not a SentencePiece model");
			}
			int v = (bytes[at] & 0xff) | (bytes[at + 1] & 0xff) << 8 | (bytes[at + 2] & 0xff) << 16
				| (bytes[at + 3] & 0xff) << 24;
			at += 4;
			return v;
		}

		void skip(int wire) {
			switch (wire) {
				case 0 -> varint();
				case 1 -> at += 8;
				case 2 -> {
					int size = (int) varint(); // read before adding: the varint moves at
					at += size;
				}
				case 5 -> at += 4;
				default -> throw new IllegalArgumentException("not a SentencePiece model");
			}
			if (at > end) {
				throw new IllegalArgumentException("not a SentencePiece model");
			}
		}
	}
}
