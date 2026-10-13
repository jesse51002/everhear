package com.voiceforgaming.vfg.kws;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Finds the phrases in Vosk's words for one utterance, and says which sayings are new: Vosk
 * revises its partial text as it hears more, so the same saying shows again and again, sometimes
 * moved or briefly gone, and a phrase said twice ("fireball fireball") shows twice.
 *
 * <ul>
 * <li><b>Whole words only:</b> a phrase is a run of whole words; "fire ball" is not in "fire
 *     balloon", nor "fireball" in "fireballs".
 * <li><b>The longest phrase:</b> a phrase found inside a longer one found at the same words
 *     ("fireball" in "hot fireball") is not a saying of its own.
 * <li><b>Each saying once:</b> sayings are counted by phrase, in order. The first time the words show
 *     a phrase n times, its nth saying is new; a revision that moves it, drops it or brings it back
 *     changes nothing, and a second saying is new when the words first show it twice.
 * </ul>
 *
 * {@link #finish} takes the utterance's final words and starts the count again for the next.
 */
final class PhraseMatcher {
	/** A saying of {@code phrase} over words {@code first} to {@code end} (exclusive), its {@code nth} (from 1) in the utterance. */
	record Saying(String phrase, int nth, int first, int end) {}

	private record Phrase(String text, List<String> words) {}

	private final List<Phrase> phrases = new ArrayList<>();
	private final Map<String, Integer> counted = new HashMap<>();

	/** Listens for {@code phrases}, each normalised words separated by single spaces. */
	PhraseMatcher(List<String> phrases) {
		for (String p : phrases) {
			this.phrases.add(new Phrase(p, Arrays.asList(p.split(" "))));
		}
	}

	/** The new sayings in the utterance's words so far. */
	List<Saying> update(List<String> words) {
		List<Saying> found = new ArrayList<>();
		Map<String, List<int[]>> byPhrase = new HashMap<>();
		for (int[] m : longest(words)) {
			byPhrase.computeIfAbsent(phrases.get(m[0]).text(), k -> new ArrayList<>()).add(m);
		}
		for (Map.Entry<String, List<int[]>> e : byPhrase.entrySet()) {
			List<int[]> at = e.getValue();
			int before = counted.getOrDefault(e.getKey(), 0);
			for (int i = before; i < at.size(); i++) {
				found.add(new Saying(e.getKey(), i + 1, at.get(i)[1], at.get(i)[2]));
			}
			counted.put(e.getKey(), Math.max(before, at.size()));
		}
		found.sort(Comparator.comparingInt(Saying::first));
		return found;
	}

	/** The new sayings in the utterance's final words; the next words are a new utterance. */
	List<Saying> finish(List<String> words) {
		List<Saying> found = update(words);
		counted.clear();
		return found;
	}

	/** Forgets the utterance. */
	void reset() {
		counted.clear();
	}

	/** Every saying in {@code words}, each phrase's numbered from 1, in order; nothing counted. */
	List<Saying> sayings(List<String> words) {
		List<Saying> out = new ArrayList<>();
		Map<String, Integer> nth = new HashMap<>();
		for (int[] m : longest(words)) {
			String p = phrases.get(m[0]).text();
			out.add(new Saying(p, nth.merge(p, 1, Integer::sum), m[1], m[2]));
		}
		return out;
	}

	/** The {@code nth} saying of {@code phrase} in {@code words}, or {@code null}. */
	Saying find(List<String> words, String phrase, int nth) {
		for (Saying s : sayings(words)) {
			if (s.phrase().equals(phrase) && s.nth() == nth) {
				return s;
			}
		}
		return null;
	}

	/** Takes back a saying counted that did not hold: shown again, it is new. */
	void uncount(String phrase, int nth) {
		counted.computeIfPresent(phrase, (k, n) -> Math.min(n, nth - 1));
	}

	/** Every phrase found as whole words, {@code {phrase, first, end}}, less those inside a longer one, in order. */
	private List<int[]> longest(List<String> words) {
		List<int[]> all = new ArrayList<>();
		for (int p = 0; p < phrases.size(); p++) {
			List<String> pw = phrases.get(p).words();
			for (int i = 0; i + pw.size() <= words.size(); i++) {
				if (words.subList(i, i + pw.size()).equals(pw)) {
					all.add(new int[] {p, i, i + pw.size()});
				}
			}
		}
		List<int[]> kept = new ArrayList<>();
		for (int[] m : all) {
			boolean inside = false;
			for (int[] o : all) {
				if (o != m && o[1] <= m[1] && m[2] <= o[2] && o[2] - o[1] > m[2] - m[1]) {
					inside = true;
					break;
				}
			}
			if (!inside) {
				kept.add(m);
			}
		}
		kept.sort(Comparator.comparingInt((int[] m) -> m[1]));
		return kept;
	}
}
