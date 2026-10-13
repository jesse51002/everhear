package com.voiceforgaming.vfg.tools;

import com.voiceforgaming.vfg.speech.Hotwords;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Pattern;

/**
 * {@code ./gradlew sttCompare}'s scores: word errors, how often each hotword comes through, false
 * hotwords, times and memory; and its report.
 */
final class SttScores {
	/** A word or name the transcripts are searched for: its kind, its label, and its letters joined, lowercase. */
	record Term(Hotwords.Kind kind, String label, String joined) {}

	private static final Pattern DOTTED_AI = Pattern.compile("(?i)\\bA\\.\\s?I\\b\\.?");

	private SttScores() {}

	/** The terms of one context: "AI", "brain", the players' names as the mod writes them, the phrases, the command words. */
	static List<Term> terms(SttCompare.Lists lists, List<String> commands) {
		List<Term> out = new ArrayList<>();
		out.add(new Term(Hotwords.Kind.AI, "AI", "ai"));
		out.add(new Term(Hotwords.Kind.BRAIN, "brain", "brain"));
		for (String p : lists.players()) {
			String w = Hotwords.nameWords(p);
			if (w != null) {
				out.add(new Term(Hotwords.Kind.NAME, p, joined(w)));
			}
		}
		for (String p : lists.phrases()) {
			out.add(new Term(Hotwords.Kind.PHRASE, p, joined(p)));
		}
		for (String c : commands) {
			if (out.stream().noneMatch(t -> t.joined().equals(joined(c)))) {
				out.add(new Term(Hotwords.Kind.COMMAND, c, joined(c)));
			}
		}
		return out;
	}

	static String joined(String s) {
		return s.toLowerCase(Locale.ROOT).replaceAll("[^\\p{L}\\p{N}]", "");
	}

	private static final String[] ONES = {"zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
		"ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"};
	private static final String[] TENS = {"", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty",
		"ninety"};

	private static String numberWords(int n) {
		if (n < 20) {
			return ONES[n];
		}
		if (n < 100) {
			return TENS[n / 10] + (n % 10 == 0 ? "" : " " + ONES[n % 10]);
		}
		return Integer.toString(n);
	}

	/**
	 * Lowercase words without punctuation; "A.I." as "ai"; numbers under 100 as words; "any more"
	 * as one word; runs of single letters joined ("t p" is "tp", but never "a" or "i").
	 */
	static List<String> words(String text) {
		String s = DOTTED_AI.matcher(text).replaceAll("AI");
		s = s.toLowerCase(Locale.ROOT).replace("%", " percent ").replace("-", " ").replace("'", "").replace("’", "");
		s = s.replaceAll("[^a-z0-9 ]", " ");
		StringBuilder b = new StringBuilder();
		for (String w : s.trim().split("\\s+")) {
			if (w.matches("\\d{1,2}")) {
				w = numberWords(Integer.parseInt(w));
			}
			b.append(w).append(' ');
		}
		s = " " + b.toString().trim() + " ";
		s = s.replace(" any more ", " anymore ");
		List<String> out = new ArrayList<>();
		StringBuilder letters = new StringBuilder();
		for (String w : s.trim().split("\\s+")) {
			if (w.isEmpty()) {
				continue;
			}
			if (w.length() == 1 && !w.equals("a") && !w.equals("i")) {
				letters.append(w);
				continue;
			}
			if (!letters.isEmpty()) {
				out.add(letters.toString());
				letters.setLength(0);
			}
			out.add(w);
		}
		if (!letters.isEmpty()) {
			out.add(letters.toString());
		}
		return out;
	}

	/** A reference's words: each player's name first written as the mod writes it ({@code Steve_123} is "Steve"). */
	static List<String> referenceWords(String reference, List<String> players) {
		String s = reference;
		for (String p : players) {
			String w = Hotwords.nameWords(p);
			if (w != null) {
				s = s.replace(p, w);
			}
		}
		return words(s);
	}

	/** Words with each name said as several words ("dusk jr", "korbin jms") joined into one, for word errors. */
	static List<String> joinNames(List<String> words, List<Term> terms) {
		List<String> out = new ArrayList<>(words);
		for (Term t : terms) {
			if (t.kind() != Hotwords.Kind.NAME) {
				continue;
			}
			for (int i = 0; i < out.size(); i++) {
				int len = spanAt(out, i, t);
				if (len > 1) {
					String joined = String.join("", out.subList(i, i + len));
					out.subList(i, i + len).clear();
					out.add(i, joined);
				}
			}
		}
		return out;
	}

	/**
	 * How many words from {@code i} make the term: their letters joined are the term's (a name's
	 * also with an "s" after it, "korbinjms"); 0 when none do. "AI" and other terms of two letters
	 * or fewer only as a word of their own.
	 */
	private static int spanAt(List<String> words, int i, Term t) {
		StringBuilder b = new StringBuilder();
		int max = t.joined().length() <= 2 ? 1 : 5;
		for (int len = 1; len <= max && i + len <= words.size(); len++) {
			b.append(words.get(i + len - 1));
			String s = b.toString();
			if (s.equals(t.joined()) || (t.kind() == Hotwords.Kind.NAME && s.equals(t.joined() + "s"))) {
				return len;
			}
			if (s.length() >= t.joined().length() + 1) {
				break;
			}
		}
		return 0;
	}

	/** How many times the term is said in the words, not overlapping. */
	static int count(Term t, List<String> words) {
		int n = 0;
		for (int i = 0; i < words.size(); ) {
			int len = spanAt(words, i, t);
			if (len > 0) {
				n++;
				i += len;
			} else {
				i++;
			}
		}
		return n;
	}

	/** Word-level edit distance: substitutions + deletions + insertions. */
	static int edits(List<String> ref, List<String> hyp) {
		int[] prev = new int[hyp.size() + 1];
		int[] cur = new int[hyp.size() + 1];
		for (int j = 0; j <= hyp.size(); j++) {
			prev[j] = j;
		}
		for (int i = 1; i <= ref.size(); i++) {
			cur[0] = i;
			for (int j = 1; j <= hyp.size(); j++) {
				int sub = prev[j - 1] + (ref.get(i - 1).equals(hyp.get(j - 1)) ? 0 : 1);
				cur[j] = Math.min(sub, Math.min(prev[j] + 1, cur[j - 1] + 1));
			}
			int[] t = prev;
			prev = cur;
			cur = t;
		}
		return prev[hyp.size()];
	}

	// --- one run's scores ---

	/** Errors and reference words, added up. */
	static final class Tally {
		int edits;
		int words;
		int lines;

		void add(int e, int w) {
			edits += e;
			words += w;
			lines++;
		}

		String wer() {
			return words == 0 ? "-" : String.format(Locale.ROOT, "%.1f%%", 100.0 * edits / words);
		}

		double rate() {
			return words == 0 ? Double.NaN : (double) edits / words;
		}
	}

	/** Said and heard, per term. */
	static final class Hits {
		int said;
		int heard;
		int extra;

		String rate() {
			return said == 0 ? "-" : String.format(Locale.ROOT, "%.0f%% (%d/%d)", 100.0 * heard / said, heard, said);
		}

		double fraction() {
			return said == 0 ? Double.NaN : (double) heard / said;
		}
	}

	/** A false hotword: in which utterance, which term, what was heard. */
	record False(int utterance, String term, String heard) {}

	/** Everything one run scores. */
	static final class Score {
		final Map<String, Tally> wer = new LinkedHashMap<>();
		final Map<Hotwords.Kind, Hits> byKind = new LinkedHashMap<>();
		final Map<String, Hits> byTerm = new LinkedHashMap<>(); // addressing set
		final Map<String, Hits> minecraftByTerm = new LinkedHashMap<>();
		int noTermLines;
		int falseInNoTerm;
		int minecraftNoTermLines;
		int minecraftFalseInNoTerm;
		final List<False> falses = new ArrayList<>();
		final Map<Hotwords.Kind, Integer> falseByKind = new LinkedHashMap<>();
		final Map<Hotwords.Kind, Integer> falseNoTermByKind = new LinkedHashMap<>();
		final List<String> errors = new ArrayList<>();
		String[] hyp;

		Tally wer(String k) {
			return wer.computeIfAbsent(k, x -> new Tally());
		}
	}

	static Score score(List<SttCompare.Utterance> utterances, List<SttCompare.Seg> segs, SttCompare.Run run,
			List<List<Term>> termsByContext, List<SttCompare.Lists> lists) {
		Score sc = new Score();
		for (Hotwords.Kind k : Hotwords.Kind.values()) {
			sc.byKind.put(k, new Hits());
		}
		String[] hyp = new String[utterances.size()];
		for (int u = 0; u < hyp.length; u++) {
			hyp[u] = "";
		}
		for (int s = 0; s < segs.size(); s++) {
			int u = segs.get(s).utterance();
			hyp[u] = (hyp[u] + " " + run.texts().get(s)).trim();
		}
		sc.hyp = hyp;
		for (int u = 0; u < utterances.size(); u++) {
			SttCompare.Utterance ut = utterances.get(u);
			List<Term> terms = termsByContext.get(ut.context());
			List<String> refWords = referenceWords(ut.reference(), lists.get(ut.context()).players());
			List<String> hypWords = words(hyp[u]);
			List<String> ref = joinNames(refWords, terms);
			List<String> got = joinNames(hypWords, terms);
			int e = edits(ref, got);
			boolean minecraft = ut.set().equals("minecraft");
			sc.wer(ut.set() + ":" + ut.category()).add(e, ref.size());
			sc.wer(ut.set()).add(e, ref.size());
			sc.wer("all").add(e, ref.size());
			boolean anyTerm = false;
			int falseHere = 0;
			Map<Hotwords.Kind, Integer> falseHereByKind = new LinkedHashMap<>();
			for (Term t : terms) {
				int r = count(t, ref);
				int h = count(t, got);
				anyTerm |= r > 0;
				Map<String, Hits> perTerm = minecraft ? sc.minecraftByTerm : sc.byTerm;
				Hits ht = perTerm.computeIfAbsent(t.kind() + ":" + t.label(), x -> new Hits());
				ht.said += r;
				ht.heard += Math.min(r, h);
				ht.extra += Math.max(0, h - r);
				if (!minecraft) {
					Hits hk = sc.byKind.get(t.kind());
					hk.said += r;
					hk.heard += Math.min(r, h);
					hk.extra += Math.max(0, h - r);
				}
				if (h > r) {
					falseHere += h - r;
					falseHereByKind.merge(t.kind(), h - r, Integer::sum);
					sc.falseByKind.merge(t.kind(), h - r, Integer::sum);
					sc.falses.add(new False(u, t.label(), hyp[u]));
				}
			}
			if (!anyTerm) {
				falseHereByKind.forEach((k, v) -> sc.falseNoTermByKind.merge(k, v, Integer::sum));
				if (minecraft) {
					sc.minecraftNoTermLines++;
					sc.minecraftFalseInNoTerm += falseHere;
				} else {
					sc.noTermLines++;
					sc.falseInNoTerm += falseHere;
					sc.wer("addressing:no-name").add(e, ref.size());
				}
			}
			if (e > 0) {
				sc.errors.add(String.format(Locale.ROOT, "- %s — %d error%s: \"%s\"", ut.source(), e, e == 1 ? "" : "s",
					hyp[u]));
			}
		}
		return sc;
	}

	static double perHundred(int falses, int lines) {
		return lines == 0 ? Double.NaN : 100.0 * falses / lines;
	}

	static long percentile(List<Long> sorted, double p) {
		if (sorted.isEmpty()) {
			return 0;
		}
		return sorted.get((int) Math.min(sorted.size() - 1, Math.max(0, Math.ceil(sorted.size() * p) - 1)));
	}

	// --- the report ---

	static void report(Path out, SttCompare.SetFile set, List<SttCompare.Utterance> utterances, List<SttCompare.Seg> segs,
			List<SttCompare.Run> runs, int threads, List<SttCompare.Lists> lists, boolean tuning) throws IOException {
		// every command word any run boosts is a term, so a run that does not boost it shows how often it comes through anyway
		List<String> commands = new ArrayList<>(Hotwords.DEFAULT.commands());
		for (SttCompare.Run r : runs) {
			if (r.commands() != null) {
				r.commands().stream().filter(c -> !commands.contains(c)).forEach(commands::add);
			}
		}
		List<List<Term>> termsByContext = lists.stream().map(l -> terms(l, commands)).toList();
		Map<String, Score> scores = new LinkedHashMap<>();
		for (SttCompare.Run r : runs) {
			scores.put(r.id(), score(utterances, segs, r, termsByContext, lists));
		}
		long addressingLines = utterances.stream().filter(u -> u.set().equals("addressing")).count();
		long minecraftLines = utterances.size() - addressingLines;
		double audio = segs.stream().mapToDouble(SttCompare.Seg::seconds).sum();

		StringBuilder md = new StringBuilder();
		md.append("# Speech-to-text comparison\n\n");
		md.append(String.format(Locale.ROOT, "%d utterances, %.1f min of speech in %d segments cut by the mod's VAD after its "
				+ "48 kHz path; %d speech-to-text threads, on %s.%n%n", utterances.size(), audio / 60, segs.size(), threads,
			SttCompare.cpu()));
		if (minecraftLines > 0) {
			md.append(String.format(Locale.ROOT, "- **Minecraft set**: %d utterances (5 recordings; 6 Minecraft phrases and 8 "
				+ "hard ones in the 8 Kitten voices, 2 takes each). Its players: Steve, Alex, SteveJM.%n", minecraftLines));
		}
		md.append(String.format(Locale.ROOT, "- **Addressing set**: `%s`, %d lines, each said by Kokoro in 4 voices as it "
			+ "speaks (clean) and in 4 others 1.25× as fast with pink noise 10 dB under the speech (hard): %d utterances. "
			+ "Players: %s. Phrases: %s.%n", set.name(), set.lines().size(), addressingLines, String.join(", ", set.players()),
			String.join(", ", set.phrases())));
		md.append("- **Hotwords** (the mod's list, for a run with hotwords): \"AI\", \"brain\", the players' names, the "
			+ "phrases and the command words, each with its own boost. A term is *through* when the transcript says it as "
			+ "often as the reference (letters compared, spaces and case ignored: \"Dusk JR\" is DuskJR); a *false* "
			+ "hotword is one said more often than in the reference.\n\n");

		md.append("| Model | WER Minecraft | WER addressing: clean / hard / no-name lines | AI | brain | names | incantations "
			+ "| command words | false per 100 no-name lines: addressing / Minecraft | STT per segment, median / p90 "
			+ "| added over greedy, median / p90 | load | RSS after load / peak |\n");
		md.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|\n");
		for (SttCompare.Run r : runs) {
			Score sc = scores.get(r.id());
			List<Long> ms = new ArrayList<>(r.sttMillis());
			ms.sort(null);
			String added = "-";
			SttCompare.Run greedy = runs.stream().filter(g -> g.model().equals(r.model()) && !g.hotwords()).findFirst()
				.orElse(null);
			if (r.hotwords() && greedy != null) {
				List<Long> diff = new ArrayList<>();
				for (int i = 0; i < r.sttMillis().size(); i++) {
					diff.add(r.sttMillis().get(i) - greedy.sttMillis().get(i));
				}
				diff.sort(null);
				added = String.format(Locale.ROOT, "+%d / +%d ms", percentile(diff, 0.5), percentile(diff, 0.9));
			}
			md.append(String.format(Locale.ROOT, "| %s | %s | %s / %s / %s | %s | %s | %s | %s | %s | %s / %s | %d / %d ms | %s "
					+ "| %.1f s | %d / %d MB |%n", r.label(), sc.wer("minecraft").wer(), sc.wer("addressing:clean").wer(),
				sc.wer("addressing:hard").wer(), sc.wer("addressing:no-name").wer(), sc.byKind.get(Hotwords.Kind.AI).rate(),
				sc.byKind.get(Hotwords.Kind.BRAIN).rate(), sc.byKind.get(Hotwords.Kind.NAME).rate(),
				sc.byKind.get(Hotwords.Kind.PHRASE).rate(), sc.byKind.get(Hotwords.Kind.COMMAND).rate(),
				fmt(perHundred(sc.falseInNoTerm, sc.noTermLines)), fmt(perHundred(sc.minecraftFalseInNoTerm,
					sc.minecraftNoTermLines)), percentile(ms, 0.5), percentile(ms, 0.9), added, r.loadMillis() / 1000.0,
				(r.rssLoadedKb() - r.rssBeforeKb()) / 1024, (r.rssPeakKb() - r.rssBeforeKb()) / 1024));
		}
		Score any = scores.values().stream().findFirst().orElse(null);
		if (any != null) {
			md.append(String.format(Locale.ROOT, "%nNo-name lines: %d in the addressing set, %d in the Minecraft set. WER is word "
				+ "errors (substitutions, deletions, insertions) over reference words, after lowercasing, dropping punctuation, "
				+ "numbers as words, joining spelled letters (\"T P\" = \"tp\") and a player's name said in parts. The names, "
				+ "incantations and command words columns are the addressing set's. STT time is per segment on %d threads, "
				+ "one warm-up decode left out; \"added over greedy\" is, segment by segment, the time with hotwords less the "
				+ "same model's greedy time: what the line's last stretch waits longer. Memory is resident memory added by "
				+ "loading (the mod's VAD and the natives included), after loading and at the peak while transcribing.%n",
				any.noTermLines, any.minecraftNoTermLines, threads));
		}

		bar(md, runs, scores);

		md.append("\n## False hotwords by kind (both sets; in no-name lines / in all lines)\n\n| Model | AI | brain | names "
			+ "| incantations | command words |\n|---|---|---|---|---|---|\n");
		for (SttCompare.Run r : runs) {
			Score sc = scores.get(r.id());
			md.append("| ").append(r.label()).append(" |");
			for (Hotwords.Kind k : Hotwords.Kind.values()) {
				md.append(' ').append(sc.falseNoTermByKind.getOrDefault(k, 0)).append(" / ")
					.append(sc.falseByKind.getOrDefault(k, 0)).append(" |");
			}
			md.append('\n');
		}

		{
			md.append("\n## Each term, through (addressing set)\n\n| Term |");
			for (SttCompare.Run r : runs) {
				md.append(' ').append(r.id()).append(" |");
			}
			md.append("\n|---|");
			md.append("---|".repeat(runs.size())).append('\n');
			for (String term : any == null ? List.<String>of() : any.byTerm.keySet()) {
				if (any.byTerm.get(term).said == 0) {
					continue;
				}
				md.append("| ").append(term).append(" |");
				for (SttCompare.Run r : runs) {
					md.append(' ').append(scores.get(r.id()).byTerm.get(term).rate()).append(" |");
				}
				md.append('\n');
			}
			if (any != null && any.minecraftByTerm.values().stream().anyMatch(h -> h.said > 0)) {
				md.append("\nMinecraft set, its players' names: ");
				for (SttCompare.Run r : runs) {
					Score sc = scores.get(r.id());
					int said = 0;
					int heard = 0;
					for (Map.Entry<String, Hits> e : sc.minecraftByTerm.entrySet()) {
						if (e.getKey().startsWith("NAME:")) {
							said += e.getValue().said;
							heard += e.getValue().heard;
						}
					}
					md.append(String.format(Locale.ROOT, "%s %d/%d; ", r.id(), heard, said));
				}
				md.append('\n');
			}

			md.append("\n## False hotwords\n");
			for (SttCompare.Run r : runs) {
				Score sc = scores.get(r.id());
				md.append("\n### ").append(r.label()).append(" — ").append(sc.falses.size()).append("\n\n");
				for (False f : sc.falses) {
					md.append(String.format(Locale.ROOT, "- %s: \"%s\" in \"%s\" (meant: \"%s\")%n", f.term(),
						f.term(), f.heard(), utterances.get(f.utterance()).reference()));
				}
			}

			md.append("\n## The hotword lists\n");
			for (SttCompare.Run r : runs) {
				if (r.hotwords() && r.hotwordLists() != null && !r.hotwordLists().isEmpty()) {
					md.append("\n").append(r.label()).append(":\n\n");
					for (String l : r.hotwordLists()) {
						md.append("- ").append(l).append('\n');
					}
					break;
				}
			}

			md.append("\n# Every utterance with errors\n");
			for (SttCompare.Run r : tuning ? List.<SttCompare.Run>of() : runs) {
				md.append("\n## ").append(r.label()).append("\n\n");
				for (String e : scores.get(r.id()).errors) {
					md.append(e).append('\n');
				}
			}
		}
		Files.writeString(out.resolve("report.md"), md, StandardCharsets.UTF_8);
		int cut = md.indexOf("\n## Each term");
		System.out.println();
		System.out.println(cut > 0 ? md.substring(0, cut) : md);
		System.out.println("Full report: " + out.resolve("report.md"));
	}

	/** The spec's proposed bar, for the mod's default against 0.3.4's. */
	private static void bar(StringBuilder md, List<SttCompare.Run> runs, Map<String, Score> scores) {
		SttCompare.Run base = runs.stream().filter(r -> r.id().equals("110m")).findFirst().orElse(null);
		SttCompare.Run mod = runs.stream().filter(r -> r.id().equals("0.6b+hw")).findFirst().orElse(null);
		if (base == null || mod == null) {
			return;
		}
		Score b = scores.get(base.id());
		Score m = scores.get(mod.id());
		Hits names = m.byKind.get(Hotwords.Kind.NAME);
		Hits ai = m.byKind.get(Hotwords.Kind.AI);
		double falses = perHundred(m.falseInNoTerm + m.minecraftFalseInNoTerm, m.noTermLines + m.minecraftNoTermLines);
		md.append("\n## Against the proposed bar (").append(mod.label()).append(")\n\n");
		md.append(String.format(Locale.ROOT, "- Fewer word errors than 110M: %s against %s over both sets — **%s**%n",
			m.wer("all").wer(), b.wer("all").wer(), m.wer("all").rate() < b.wer("all").rate() ? "pass" : "FAIL"));
		md.append(String.format(Locale.ROOT, "- \"AI\" through in ≥95%% of the lines holding it: %s — **%s**%n", ai.rate(),
			ai.fraction() >= 0.95 ? "pass" : "FAIL"));
		md.append(String.format(Locale.ROOT, "- The players' names through in ≥95%% of the lines holding them: %s — **%s**%n",
			names.rate(), names.fraction() >= 0.95 ? "pass" : "FAIL"));
		md.append(String.format(Locale.ROOT, "- ≤1 false hotword in 100 lines with no name: %s per 100 (%d in %d lines) — **%s**%n",
			fmt(falses), m.falseInNoTerm + m.minecraftFalseInNoTerm, m.noTermLines + m.minecraftNoTermLines,
			falses <= 1.0 ? "pass" : "FAIL"));
	}

	private static String fmt(double v) {
		return Double.isNaN(v) ? "-" : String.format(Locale.ROOT, "%.1f", v);
	}
}
