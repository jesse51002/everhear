package com.voiceforgaming.vfg.kws;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

import com.voiceforgaming.vfg.audio.WindowListener.StopReason;
import com.voiceforgaming.vfg.config.VfgConfig;
import com.voiceforgaming.vfg.speech.CaptureProcessor;
import com.voiceforgaming.vfg.speech.SpeechEngine;
import com.voiceforgaming.vfg.testing.KokoroTakes;
import com.voiceforgaming.vfg.testing.SpeechTimeline;
import com.voiceforgaming.vfg.testing.TestAssets;
import com.voiceforgaming.vfg.testing.Wav;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;

/**
 * The keyword spotter on real speech: the keyword model installed through the mod's own
 * installer, Kokoro TTS takes (kept in the test assets), the mod's Silero VAD saying when there
 * is speech, and the mod's {@link SpotterWorker}, fed 32 ms windows as the capture thread feeds it,
 * faster than real time, the clock following the sample count. It reports hits, false casts,
 * the after-a-pause case, the latency on the audio clock and the CPU; it checks the bars that
 * are certain (a phrase said alone is heard) and reports the rest, whose bars are the spec's TODO.
 * Run with {@code ./gradlew kwsTest} (or with every engine test, {@code ./gradlew engineTest});
 * the report is left in {@code build/kws-test/report.txt}.
 */
@Tag("engine")
@Tag("kws")
class SpotterEngineTest {
	private static final long T0 = 1_760_000_000_000L;
	private static final double THRESHOLD = VfgConfig.DEFAULT_INCANTATION_THRESHOLD;
	private static final double BOOST = VfgConfig.DEFAULT_INCANTATION_BOOST;

	static final Incantation FIRE_BALL = new Incantation("fire ball", List.of("fireball", "fire bowl"), 3);
	/** Aliases as the create action's one LLM call might list them: respellings of how it is said. */
	static final Incantation KAMEHAMEHA = new Incantation("kamehameha",
		List.of("kah meh hah meh hah", "kameha meha", "kame hame ha", "come a hammer ha", "kami hami ha"), 3);
	static final Incantation ICE_WALL = new Incantation("ice wall", List.of("icewall", "ice well"), 3);
	static final Incantation GALICK_GUN = new Incantation("galick gun", List.of("garlic gun", "gallic gun"), 3);
	static final List<Incantation> ALL = List.of(FIRE_BALL, KAMEHAMEHA, ICE_WALL, GALICK_GUN);

	static final String[] IN_SENTENCES = {
		"I'm going to cast fire ball at that creeper.",
		"Okay, fire ball on the zombie now.",
		"Quick, fire ball the skeleton before it shoots.",
		"Watch this one, fire ball, right into the lava.",
	};
	/** How a player says the made-up word: as spelled, and syllable by syllable. */
	static final String[] KAMEHAMEHA_SAID = {"Kamehameha!", "Ka-me-ha-me-ha!"};

	/** Sentences with no phrase in them, each in the voice the speech test keeps it in. */
	static final Object[][] ORDINARY = {
		{"Build me a castle by the cave.", 0}, {"Make it day.", 1}, {"Give everyone diamonds.", 2},
		{"Spawn a zombie behind the house.", 3}, {"Turn off the rain, please.", 4}, {"Who is winning right now?", 5},
		{"Put a torch on the pillar.", 6}, {"Craft me a diamond pickaxe.", 7},
		{"Set my spawn point at the village.", 8}, {"Summon an ender dragon.", 9},
		{"Can you build a bridge over the river?", 10}, {"Plant some wheat next to the barn.", 0},
		{"Build me a big stone castle right next to the river.", 1},
		{"Dig a tunnel straight through the mountain to the east.", 2},
		{"Can you go and find some diamonds for me down in the cave?", 3},
		{"Make the sun come up and stop the rain over the farm.", 4},
	};

	/** Sentences with a phrase's words or sounds in them, but not the phrase. */
	static final Object[][] NEAR_MISSES = {
		{"Light a fire next to the wall.", 0}, {"Throw the ball into the fire.", 3}, {"The fire is all gone now.", 5},
		{"Put the ice in the hall.", 7}, {"Grab the garlic and the gun.", 9}, {"Can we have a ball at the castle?", 1},
	};

	private static SpeechEngine engine;
	private static Spotter model;
	private static KokoroTakes takes;
	private static Path out;
	private static final List<Double> latencies = Collections.synchronizedList(new ArrayList<>());
	private static final List<Double> startErrors = Collections.synchronizedList(new ArrayList<>());
	private static final List<Double> endErrors = Collections.synchronizedList(new ArrayList<>());
	private static long spotterNanos;
	private static double spotterSeconds;
	private static long naiveNanos;
	private static double naiveSeconds;

	@BeforeAll
	static void setUp() throws Exception {
		engine = TestAssets.engine(VfgConfig.DEFAULT_MODEL, 2, 0.5f);
		long t0 = System.nanoTime();
		model = TestAssets.spotter();
		takes = new KokoroTakes();
		out = Path.of("build/kws-test").toAbsolutePath();
		String dir = System.getProperty("vfg.kwsOut");
		if (dir != null) {
			out = Path.of(dir);
		}
		Files.createDirectories(out);
		Files.deleteIfExists(out.resolve("report.txt"));
		say("keyword model loaded in %d ms (threshold %s, boost %s, %d trailing blank frames)",
			(System.nanoTime() - t0) / 1_000_000, THRESHOLD, BOOST, model.trailingBlanks());
	}

	@AfterAll
	static void tearDown() {
		if (!latencies.isEmpty()) {
			say("%n== summary ==");
			say("detection latency, end of the phrase's audio to the hit, on the audio clock: median %.2f s,"
				+ " %.2f to %.2f s over %d hits", median(latencies), Collections.min(latencies),
				Collections.max(latencies), latencies.size());
			say("started_at less the phrase's true start: median %+.2f s; ended_at less its true end: median %+.2f s",
				median(startErrors), median(endErrors));
		}
		if (spotterSeconds > 0) {
			say("CPU, the spotter as the mod runs it (streams only while the VAD hears speech): %.4f of real time"
				+ " (%.1f s of audio in %d ms)", spotterNanos / 1e9 / spotterSeconds, spotterSeconds,
				spotterNanos / 1_000_000);
		}
		if (naiveSeconds > 0) {
			say("CPU, every window decoded (one stream throughout): %.4f of real time (%.1f s of audio in %d ms)",
				naiveNanos / 1e9 / naiveSeconds, naiveSeconds, naiveNanos / 1_000_000);
		}
		if (takes != null) {
			takes.close();
		}
		if (model != null) {
			model.close();
		}
		if (engine != null) {
			engine.close();
		}
	}

	// --- the checks ---

	/** The model's own bpe.model, parsed by the mod, splits the fixture's phrases as Python's sentencepiece does. */
	@Test
	void theModelsOwnPiecesMatchTheFixtureAndAreTokens() throws Exception {
		SentencePiece sp = SentencePiece.load(TestAssets.kwsModelDir().resolve(Spotter.PIECES));
		assertTrue(sp.unigram(), "the keyword model's bpe.model is a unigram model");
		Set<String> tokens = model.tokens();
		int checked = 0;
		for (String[] row : SentencePieceTest.fixture()) {
			List<String> pieces = sp.encode(row[0]);
			assertEquals(List.of(row[1].split(" ")), pieces, row[0]);
			if (!row[0].contains("3")) {
				assertTrue(tokens.containsAll(pieces), row[0] + ": " + pieces);
			}
			checked++;
		}
		assertEquals(500, tokens.size());
		say("the model's bpe.model splits all %d fixture phrases as sentencepiece does", checked);
	}

	/**
	 * The plain sherpa-onnx way, one stream from the first sample and every window decoded, so a
	 * hit's frames count from 0: it fires in the chunk just decoded, {@code trailing} + 1 frames after
	 * its last token, 8 output frames to a chunk; and its first token is near where the phrase starts.
	 */
	@Test
	void aHitFiresInTheChunkJustDecodedAfterItsTrailingBlanks() throws Exception {
		Keywords k = Keywords.build(List.of(FIRE_BALL), model.pieces(), model.tokens(), THRESHOLD, BOOST);
		for (int voice : new int[] {0, 4, 8}) {
			SpeechTimeline t = new SpeechTimeline().silence(1.0).speech("fire ball", List.of(), take("Fire ball!", voice))
				.silence(1.0);
			List<Naive> hits = naive(t.at16k(), k.spec());
			assertEquals(1, hits.size(), "voice " + voice + ": " + hits);
			Naive h = hits.getFirst();
			long fire = SpotterWorker.frame(h.timestamps()[h.timestamps().length - 1]) + model.trailingBlanks() + 1;
			assertTrue(fire >= (h.decodes() - 1) * Spotter.CHUNK_FRAMES && fire < h.decodes() * Spotter.CHUNK_FRAMES,
				"fired at frame " + fire + " after " + h.decodes() + " chunks");
			double first = h.timestamps()[0];
			double start = SpeechTimeline.millisOf(t.clips().getFirst().start()) / 1000.0;
			assertTrue(Math.abs(first - start) < 0.6, "first token at " + first + " s, the phrase at " + start + " s");
		}
	}

	@Test
	void fireBallSaidAloneIsHeard() throws Exception {
		int hits = 0;
		StringBuilder each = new StringBuilder();
		for (int voice = 0; voice < KokoroTakes.VOICES; voice++) {
			SpeechTimeline t = new SpeechTimeline().silence(1.0).speech("fire ball", List.of(), take("Fire ball!", voice))
				.silence(1.5);
			Heard h = listen(t, List.of(FIRE_BALL), model, THRESHOLD, BOOST);
			if (h.casts().size() == 1 && h.casts().getFirst().phrase().equals("fire ball")) {
				hits++;
				measure(t.clips().getFirst(), h.casts().getFirst());
			}
			each.append(String.format(Locale.ROOT, " v%d:%s", voice, heardWords(h)));
		}
		say("%n== \"fire ball\" said alone (Kokoro, 11 voices) ==%n%d of 11 heard;%s", hits, each);
		assertTrue(hits >= 9, hits + " of 11");
	}

	@Test
	void fireBallInsideASentenceIsHeard() throws Exception {
		int hits = 0;
		int tries = 0;
		StringBuilder each = new StringBuilder();
		for (int i = 0; i < IN_SENTENCES.length; i++) {
			for (int voice : new int[] {i, i + 4, i + 7}) {
				SpeechTimeline t = new SpeechTimeline().silence(1.0)
					.speech(IN_SENTENCES[i], List.of(), take(IN_SENTENCES[i], voice)).silence(1.5);
				Heard h = listen(t, List.of(FIRE_BALL), model, THRESHOLD, BOOST);
				tries++;
				if (h.casts().stream().anyMatch(c -> c.phrase().equals("fire ball"))) {
					hits++;
				}
				each.append(String.format(Locale.ROOT, " s%d v%d:%s", i + 1, voice, heardWords(h)));
			}
		}
		say("%n== \"fire ball\" inside a sentence (Kokoro, 4 sentences x 3 voices) ==%n%d of %d heard;%s", hits,
			tries, each);
		assertTrue(hits * 2 >= tries, hits + " of " + tries);
	}

	@Test
	void kamehamehaIsHeardThroughItsAliases() throws Exception {
		int total = 0;
		int tries = 0;
		for (String said : KAMEHAMEHA_SAID) {
			int hits = 0;
			Map<String, Integer> by = new TreeMap<>();
			for (int voice = 0; voice < KokoroTakes.VOICES; voice++) {
				SpeechTimeline t = new SpeechTimeline().silence(1.0).speech(said, List.of(), take(said, voice)).silence(1.5);
				Heard h = listen(t, List.of(KAMEHAMEHA), model, THRESHOLD, BOOST);
				tries++;
				if (!h.casts().isEmpty()) {
					hits++;
					measure(t.clips().getFirst(), h.casts().getFirst());
				}
				h.casts().forEach(c -> by.merge(c.heard(), 1, Integer::sum));
			}
			total += hits;
			say("%n== \"kamehameha\" said \"%s\" (Kokoro, 11 voices), bound with %s ==%n%d of 11 heard; by keyword %s",
				said, KAMEHAMEHA.aliases(), hits, by);
		}
		assertTrue(total > 0, "kamehameha was never heard in " + tries + " takes");
	}

	/**
	 * Every incantation bound at once, over ordinary requests, and over near misses with a phrase's
	 * words in them: every cast is a false one. Reported, not checked.
	 */
	@Test
	void falseCastsOnSentencesWithoutAPhraseAreCounted() throws Exception {
		for (Object[][] set : new Object[][][] {ORDINARY, NEAR_MISSES}) {
			SpeechTimeline t = sentences(set);
			Heard h = listen(t, ALL, model, THRESHOLD, BOOST);
			List<String> which = new ArrayList<>();
			for (SpotterWorker.Cast c : h.casts()) {
				which.add("\"" + c.heard() + "\" in \"" + sentenceAt(t, c) + "\"");
			}
			say("%n== false casts: %d %s, %s bound ==%n%d false casts in %.0f s of audio%s", set.length,
				set == ORDINARY ? "ordinary requests" : "near misses (" + Arrays.stream(set).map(o -> (String) o[0])
					.toList() + ")", ALL.stream().map(Incantation::phrase).toList(), h.casts().size(), h.seconds(),
				which.isEmpty() ? "" : ": " + String.join(", ", which));
		}
	}

	/** The sentences, each in its voice, 1 s apart. */
	private static SpeechTimeline sentences(Object[][] set) throws Exception {
		SpeechTimeline t = new SpeechTimeline().silence(1.0);
		for (Object[] o : set) {
			t.speech((String) o[0], List.of(), take((String) o[0], (Integer) o[1])).silence(1.0);
		}
		return t;
	}

	/**
	 * sherpa-onnx issue #3990: a stream in silence resets itself every ~1.6 s and drops the start of
	 * a keyword said just then. "Fire ball" after 1.0 to 2.0 s of silence, in 0.05 s steps, two voices:
	 * the plain way (one stream, every window decoded) against the mod's (streams only while the VAD
	 * hears speech, with 0.64 s before it). Reported, and the mod's must miss no more.
	 */
	@Test
	void aPhraseAfterAPauseIsHeard() throws Exception {
		Keywords k = Keywords.build(List.of(FIRE_BALL), model.pieces(), model.tokens(), THRESHOLD, BOOST);
		int tries = 0;
		int naiveHits = 0;
		int modHits = 0;
		List<String> naiveMissed = new ArrayList<>();
		List<String> modMissed = new ArrayList<>();
		for (int voice : new int[] {0, 5}) {
			Wav.Audio take = take("Fire ball!", voice);
			for (int step = 0; step <= 20; step++) {
				double lead = 1.0 + step * 0.05;
				SpeechTimeline t = new SpeechTimeline().silence(lead).speech("fire ball", List.of(), take).silence(1.0);
				String label = String.format(Locale.ROOT, "v%d %.2f s", voice, lead);
				tries++;
				float[] audio = t.at16k();
				long a = System.nanoTime();
				List<Naive> plain = naive(audio, k.spec());
				naiveNanos += System.nanoTime() - a;
				naiveSeconds += audio.length / 16000.0;
				if (plain.isEmpty()) {
					naiveMissed.add(label);
				} else {
					naiveHits++;
				}
				Heard h = listen(t, List.of(FIRE_BALL), model, THRESHOLD, BOOST);
				if (h.casts().isEmpty()) {
					modMissed.add(label);
				} else {
					modHits++;
					measure(t.clips().getFirst(), h.casts().getFirst());
				}
			}
		}
		say("%n== \"fire ball\" after 1.0 to 2.0 s of silence (sherpa-onnx #3990) ==");
		say("one stream, every window decoded: %d of %d heard%s", naiveHits, tries,
			naiveMissed.isEmpty() ? "" : "; missed after " + naiveMissed);
		say("the mod's spotter, streams following the VAD: %d of %d heard%s", modHits, tries,
			modMissed.isEmpty() ? "" : "; missed after " + modMissed);
		assertTrue(modHits >= naiveHits, "the mod's spotter heard " + modHits + ", the plain way " + naiveHits);
	}

	@Test
	void latencyAndTimesAreOnTheAudioClock() throws Exception {
		// the phrase-alone cases above fill these; this one adds its own few so it stands alone
		for (int voice : new int[] {1, 6}) {
			SpeechTimeline t = new SpeechTimeline().silence(1.0).speech("fire ball", List.of(), take("Fire ball!", voice))
				.silence(1.5);
			Heard h = listen(t, List.of(FIRE_BALL), model, THRESHOLD, BOOST);
			assertEquals(1, h.casts().size(), "voice " + voice);
			SpotterWorker.Cast c = h.casts().getFirst();
			double lateness = measure(t.clips().getFirst(), c);
			assertTrue(lateness > 0 && lateness < 1.2, "heard " + lateness + " s after the phrase");
			long start = T0 + SpeechTimeline.millisOf(t.clips().getFirst().start());
			long end = T0 + SpeechTimeline.millisOf(t.clips().getFirst().end());
			assertTrue(Math.abs(c.startedAtMillis() - start) <= 300, "started_at off by " + (c.startedAtMillis() - start));
			assertTrue(Math.abs(c.endedAtMillis() - end) <= 300, "ended_at off by " + (c.endedAtMillis() - end));
		}
	}

	/**
	 * Opt in with {@code -Dvfg.kwsSweep=true} ({@code ./gradlew kwsTest -Pkws-sweep}): hits and false
	 * casts over thresholds, boosts and trailing blanks, to pick the defaults.
	 */
	@Test
	void sweep() throws Exception {
		assumeTrue(Boolean.getBoolean("vfg.kwsSweep"), "opt in with -Pkws-sweep");
		SpeechTimeline ordinary = sentences(ORDINARY);
		SpeechTimeline near = sentences(NEAR_MISSES);
		say("%n== sweep: hits of fire ball alone /11, in a sentence /12, kamehameha /22; false casts in 16 ordinary"
			+ " requests and 6 near misses ==");
		for (int trailing : new int[] {1, 2}) {
			try (Spotter m = Spotter.load(TestAssets.nativesDir(), TestAssets.kwsModelDir(), trailing)) {
				for (double threshold : new double[] {0.1, 0.15, 0.2, 0.25, 0.35}) {
					for (double boost : new double[] {1.0, 1.5, 2.0, 3.0}) {
						int alone = 0;
						int inside = 0;
						int kame = 0;
						for (int voice = 0; voice < KokoroTakes.VOICES; voice++) {
							SpeechTimeline t = new SpeechTimeline().silence(1.0)
								.speech("", List.of(), take("Fire ball!", voice)).silence(1.5);
							alone += listen(t, List.of(FIRE_BALL), m, threshold, boost).casts().isEmpty() ? 0 : 1;
							for (String said : KAMEHAMEHA_SAID) {
								SpeechTimeline kt = new SpeechTimeline().silence(1.0).speech("", List.of(), take(said, voice))
									.silence(1.5);
								kame += listen(kt, List.of(KAMEHAMEHA), m, threshold, boost).casts().isEmpty() ? 0 : 1;
							}
						}
						for (int i = 0; i < IN_SENTENCES.length; i++) {
							for (int voice : new int[] {i, i + 4, i + 7}) {
								SpeechTimeline t = new SpeechTimeline().silence(1.0)
									.speech("", List.of(), take(IN_SENTENCES[i], voice)).silence(1.5);
								inside += listen(t, List.of(FIRE_BALL), m, threshold, boost).casts().isEmpty() ? 0 : 1;
							}
						}
						int falseCasts = listen(ordinary, ALL, m, threshold, boost).casts().size();
						int nearCasts = listen(near, ALL, m, threshold, boost).casts().size();
						say("trailing %d, threshold %.2f, boost %.1f: alone %2d, sentence %2d, kamehameha %2d; false %d,"
							+ " near %d", trailing, threshold, boost, alone, inside, kame, falseCasts, nearCasts);
					}
				}
			}
		}
	}

	// --- the harness ---

	/** The casts the spotter made over a timeline, and the time it spent and the audio it heard. */
	record Heard(List<SpotterWorker.Cast> casts, long nanos, double seconds) {}

	/**
	 * Plays a timeline at 16 kHz through the mod's VAD (a {@link CaptureProcessor} on the real engine)
	 * and a {@link SpotterWorker}, 32 ms at a time, as the capture thread does.
	 */
	static Heard listen(SpeechTimeline t, List<Incantation> list, KeywordModel m, double threshold, double boost) {
		float[] audio = t.at16k();
		List<SpotterWorker.Cast> casts = new ArrayList<>();
		SpotterWorker worker = new SpotterWorker(true, threshold, boost, casts::add);
		worker.setModel(kept(m));
		worker.setIncantations(list);
		worker.onStarted(true);
		CaptureProcessor vad = new CaptureProcessor(() -> engine, item -> {});
		vad.onStarted(true);
		long nanos = 0;
		long samples = 0;
		for (int off = 0; off + SpeechEngine.WINDOW <= audio.length; off += SpeechEngine.WINDOW) {
			float[] w = Arrays.copyOfRange(audio, off, off + SpeechEngine.WINDOW);
			samples += w.length;
			long wall = T0 + samples * 1000 / 16000;
			vad.onWindow(w, wall);
			worker.onWindow(w, wall, vad.speaking());
			long a = System.nanoTime();
			worker.drainNow();
			nanos += System.nanoTime() - a;
		}
		worker.onStopped(StopReason.DISCARD);
		vad.onStopped(StopReason.DISCARD);
		worker.shutdown(0);
		if (m == model) {
			spotterNanos += nanos;
			spotterSeconds += audio.length / 16000.0;
		}
		return new Heard(casts, nanos, audio.length / 16000.0);
	}

	/** One hit the plain way: its tag, its token times, and the chunks decoded when it fired. */
	record Naive(String keyword, float[] timestamps, long decodes) {
		@Override
		public String toString() {
			return keyword + " " + Arrays.toString(timestamps) + " after " + decodes + " chunks";
		}
	}

	/** One stream from the first sample, every window decoded and the stream reset after each hit. */
	static List<Naive> naive(float[] audio, String spec) {
		List<Naive> hits = new ArrayList<>();
		try (KeywordModel.Stream s = model.open(spec)) {
			long decodes = 0;
			for (int off = 0; off + SpeechEngine.WINDOW <= audio.length; off += SpeechEngine.WINDOW) {
				s.accept(Arrays.copyOfRange(audio, off, off + SpeechEngine.WINDOW));
				while (s.ready()) {
					s.decode();
					decodes++;
					KeywordModel.Hit h = s.hit();
					if (h != null) {
						hits.add(new Naive(h.keyword(), h.timestamps(), decodes));
						s.reset();
					}
				}
			}
		}
		return hits;
	}

	/** The model, with a close that does nothing: the worker closes the model it owns, and this one is shared. */
	static KeywordModel kept(KeywordModel m) {
		return new KeywordModel() {
			@Override
			public Stream open(String keywords) {
				return m.open(keywords);
			}

			@Override
			public SentencePiece pieces() {
				return m.pieces();
			}

			@Override
			public Set<String> tokens() {
				return m.tokens();
			}

			@Override
			public int chunkFrames() {
				return m.chunkFrames();
			}

			@Override
			public int trailingBlanks() {
				return m.trailingBlanks();
			}

			@Override
			public void close() {}
		};
	}

	/** Records a hit on a phrase said alone: its latency on the audio clock, and how far its times are off. */
	private static double measure(SpeechTimeline.Clip clip, SpotterWorker.Cast c) {
		long start = T0 + SpeechTimeline.millisOf(clip.start());
		long end = T0 + SpeechTimeline.millisOf(clip.end());
		double lateness = (c.heardAtMillis() - end) / 1000.0;
		latencies.add(lateness);
		startErrors.add((c.startedAtMillis() - start) / 1000.0);
		endErrors.add((c.endedAtMillis() - end) / 1000.0);
		return lateness;
	}

	private static String sentenceAt(SpeechTimeline t, SpotterWorker.Cast c) {
		for (SpeechTimeline.Clip clip : t.clips()) {
			long end = T0 + SpeechTimeline.millisOf(clip.end());
			if (c.endedAtMillis() <= end + 500) {
				return clip.label();
			}
		}
		return "?";
	}

	private static String heardWords(Heard h) {
		return h.casts().isEmpty() ? "-" : String.join("+", h.casts().stream().map(SpotterWorker.Cast::heard).toList());
	}

	private static Wav.Audio take(String text, int voice) throws Exception {
		return takes.say(text, voice % KokoroTakes.VOICES);
	}

	private static double median(List<Double> values) {
		List<Double> sorted = new ArrayList<>(values);
		Collections.sort(sorted);
		int n = sorted.size();
		return n % 2 == 1 ? sorted.get(n / 2) : (sorted.get(n / 2 - 1) + sorted.get(n / 2)) / 2;
	}

	private static synchronized void say(String format, Object... args) {
		String line = String.format(Locale.ROOT, format, args);
		System.out.println(line);
		try {
			Files.writeString(out.resolve("report.txt"), line + System.lineSeparator(), StandardCharsets.UTF_8,
				StandardOpenOption.CREATE, StandardOpenOption.APPEND);
		} catch (Exception e) {
			// the report is a convenience; stdout has it too
		}
	}
}
