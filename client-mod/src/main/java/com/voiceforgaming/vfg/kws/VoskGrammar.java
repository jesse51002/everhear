package com.voiceforgaming.vfg.kws;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.function.Predicate;

/**
 * Vosk's grammar for a player's incantations: the words it may hear. It is a JSON list of
 * phrases, any of which Vosk strings together:
 *
 * <ul>
 * <li>each incantation's own phrase, normalised as the spotter's keywords are ({@link Keywords#words}).
 *     Not the aliases: they are the spotter's mishearings, and Vosk hears the phrase itself as well
 *     without them. A phrase with a word Vosk does not know ("galick gun", "rasengan") is left out,
 *     with the words, for the spotter alone to listen for: Vosk would drop the word and listen for
 *     the rest;
 * <li>then about 5,000 common English words, the {@link #fillers()}, each a phrase of its own, but
 *     none of a bound phrase's words. They give ordinary speech somewhere else to go: with the
 *     phrases alone Vosk heard one in nearly anything (272 false casts an hour of real speech), with
 *     3,000 common words 13.8, with 5,000 7.6;
 * <li>and {@code [unk]}, for the rest.
 * </ul>
 */
public final class VoskGrammar {
	/** The shipped filler list, a resource beside this class: a word a line, {@code #} lines are comments. */
	public static final String FILLERS_RESOURCE = "vosk-fillers.txt";
	public static final String UNKNOWN = "[unk]";
	/**
	 * Gson escapes an apostrophe as a Unicode escape by default, which Vosk's JSON reader leaves as it
	 * is, so "don't" would be a word Vosk does not know: written as it is.
	 */
	private static final Gson GSON = new GsonBuilder().disableHtmlEscaping().create();
	private static List<String> shippedFillers;

	/** A phrase left out, and the words in it Vosk does not know. */
	public record LeftOut(String phrase, List<String> unknown) {}

	public static final VoskGrammar NONE = new VoskGrammar("", List.of(), List.of(), 0);

	private final String json;
	private final List<String> phrases;
	private final List<LeftOut> leftOut;
	private final int fillerCount;

	private VoskGrammar(String json, List<String> phrases, List<LeftOut> leftOut, int fillerCount) {
		this.json = json;
		this.phrases = phrases;
		this.leftOut = leftOut;
		this.fillerCount = fillerCount;
	}

	/**
	 * The grammar for {@code incantations}, with the words {@code knows} says Vosk knows, and the
	 * {@code fillers} that are not words of a phrase; {@link #NONE}, but with what was left out, when
	 * no phrase is left to listen for.
	 */
	public static VoskGrammar build(List<Incantation> incantations, Predicate<String> knows, List<String> fillers) {
		Set<String> phrases = new LinkedHashSet<>();
		List<LeftOut> leftOut = new ArrayList<>();
		Set<String> seen = new HashSet<>();
		for (Incantation inc : incantations) {
			String phrase = Keywords.words(inc.phrase());
			if (phrase.isEmpty() || !seen.add(phrase)) {
				continue;
			}
			List<String> unknown = Arrays.stream(phrase.split(" ")).filter(w -> !knows.test(w)).distinct().toList();
			if (unknown.isEmpty()) {
				phrases.add(phrase);
			} else {
				leftOut.add(new LeftOut(phrase, unknown));
			}
		}
		if (phrases.isEmpty()) {
			return new VoskGrammar("", List.of(), List.copyOf(leftOut), 0);
		}
		Set<String> bound = new HashSet<>();
		for (String p : phrases) {
			bound.addAll(Arrays.asList(p.split(" ")));
		}
		List<String> entries = new ArrayList<>(phrases);
		Set<String> taken = new HashSet<>(phrases);
		int count = 0;
		for (String f : fillers) {
			if (!bound.contains(f) && knows.test(f) && taken.add(f)) {
				entries.add(f);
				count++;
			}
		}
		entries.add(UNKNOWN);
		return new VoskGrammar(GSON.toJson(entries), List.copyOf(phrases), List.copyOf(leftOut), count);
	}

	/** The shipped list of common English words, most common first (see the README for its source). */
	public static synchronized List<String> fillers() {
		if (shippedFillers == null) {
			try (InputStream in = VoskGrammar.class.getResourceAsStream(FILLERS_RESOURCE)) {
				if (in == null) {
					throw new IOException(FILLERS_RESOURCE + " is missing from the jar");
				}
				shippedFillers = readFillers(in);
			} catch (IOException e) {
				throw new UncheckedIOException(e);
			}
		}
		return shippedFillers;
	}

	/** A word a line, lower case; blank lines and {@code #} comments skipped. */
	static List<String> readFillers(InputStream in) throws IOException {
		List<String> out = new ArrayList<>();
		BufferedReader r = new BufferedReader(new InputStreamReader(in, StandardCharsets.UTF_8));
		String line;
		while ((line = r.readLine()) != null) {
			line = line.strip();
			if (!line.isEmpty() && !line.startsWith("#")) {
				out.add(line);
			}
		}
		return List.copyOf(out);
	}

	/** The JSON for Vosk's recognizer; empty when there is nothing to listen for. */
	public String json() {
		return json;
	}

	/** The phrases listened for, normalised. */
	public List<String> phrases() {
		return phrases;
	}

	public boolean isEmpty() {
		return phrases.isEmpty();
	}

	/** The phrases left out, as Vosk does not know a word of theirs. */
	public List<LeftOut> leftOut() {
		return leftOut;
	}

	/** How many common words are in it beside the phrases. */
	public int fillerCount() {
		return fillerCount;
	}

	@Override
	public String toString() {
		return "VoskGrammar[" + phrases + ", " + fillerCount + " fillers" + (leftOut.isEmpty() ? "" : ", left out "
			+ leftOut) + "]";
	}
}
