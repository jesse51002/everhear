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
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.stream.Stream;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;

/**
 * The keyword spotter on real speech: the keyword model installed through the mod's own
 * installer, Kokoro TTS takes (kept in the test assets), the spotter's own Silero VAD saying when
 * there is speech, and the mod's {@link SpotterWorker}, fed 32 ms windows as the capture thread
 * feeds it, faster than real time, the clock following the sample count.
 *
 * <p>{@code vfg-kws} is played on that clock too: a turn of it (every window waiting, then a
 * re-check) starts when the thread is free and a window is waiting, and lasts as long as it took
 * here, so a window that comes while a re-check runs waits for it, as in the mod, and a cast is
 * heard when its turn got that far, its compute included. Latency is from the end of the phrase's
 * sound to then.
 *
 * <p>Each case runs two ways: the mod's ({@link #MOD}: re-checks over 3 s beside the live stream)
 * and the live stream alone, pushed at the end of speech ({@link #STREAM}:
 * {@code incantation_recheck_seconds: 0}). It checks the bars that are certain (Prototype_Fast_Casts:
 * a median at most 0.1 s and a 90th percentile at most 0.2 s after the phrase ends; "fire ball"
 * heard at least as often as before the re-check, alone and in sentences; no cast in the ordinary
 * sentences and near misses; no saying cast twice) and reports the rest: phrases that start longer
 * words, a spell after nonstop talk, one-word phrases, the CPU. Run with {@code ./gradlew kwsTest}
 * (or with every engine test, {@code ./gradlew engineTest}); the report is left in
 * {@code build/kws-test/report.txt}.
 */
@Tag("engine")
@Tag("kws")
class SpotterEngineTest {
	private static final long T0 = 1_760_000_000_000L;
	private static final double THRESHOLD = VfgConfig.DEFAULT_INCANTATION_THRESHOLD;
	private static final double BOOST = VfgConfig.DEFAULT_INCANTATION_BOOST;

	/** How the spotter runs: how far a re-check reaches back (0: none), and the silence it keeps (-1: all). */
	record Way(String name, double recheckSeconds, int leadWindows) {
		@Override
		public String toString() {
			return name;
		}
	}

	/** The mod's: re-checks over 3 s beside the live stream. */
	static final Way MOD = new Way("re-checks over 3 s and the stream", VfgConfig.DEFAULT_INCANTATION_RECHECK_SECONDS,
		SpotterWorker.LEAD_WINDOWS);
	/** {@code incantation_recheck_seconds: 0}: the live stream alone, pushed at the end of speech. */
	static final Way STREAM = new Way("the stream alone", 0, SpotterWorker.LEAD_WINDOWS);
	static final List<Way> WAYS = List.of(MOD, STREAM);

	/**
	 * Before the re-check (the stream alone, with no push), on 2026-10-11: "fire ball" said alone was
	 * heard 9 of 11, inside a sentence 12 of 12.
	 */
	static final int BEFORE_ALONE = 9;
	static final int BEFORE_INSIDE = 12;

	static final Incantation FIRE_BALL = new Incantation("fire ball", List.of("fireball", "fire bowl"), 3);
	/** Aliases as the create action's one LLM call might list them: respellings of how it is said. */
	static final Incantation KAMEHAMEHA = new Incantation("kamehameha",
		List.of("kah meh hah meh hah", "kameha meha", "kame hame ha", "come a hammer ha", "kami hami ha"), 3);
	static final Incantation ICE_WALL = new Incantation("ice wall", List.of("icewall", "ice well"), 3);
	static final Incantation GALICK_GUN = new Incantation("galick gun", List.of("garlic gun", "gallic gun"), 3);
	static final List<Incantation> ALL = List.of(FIRE_BALL, KAMEHAMEHA, ICE_WALL, GALICK_GUN);
	/** The spec's other two-word phrases, with the aliases its measurements bound. */
	static final List<Incantation> MORE_TWO_WORDS = List.of(
		new Incantation("lightning strike", List.of("lightning strikes"), 3),
		new Incantation("summon wolves", List.of("summon wolf", "summon the wolves"), 3),
		new Incantation("rocket jump", List.of("rocketjump"), 3),
		new Incantation("healing light", List.of("heal in light"), 3),
		new Incantation("meteor storm", List.of("meteorstorm"), 3));
	/** The two-word phrases whose end is timed, each said alone and ending two sentences ({@link #said}). */
	static final List<Incantation> TIMED = Stream.concat(Stream.of(FIRE_BALL, ICE_WALL), MORE_TWO_WORDS.stream())
		.toList();
	/** A one-word phrase that starts a two-word one. */
	static final Incantation FIRE = new Incantation("fire", List.of(), 3);

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

	/** A bound phrase as the start of a longer word: what casting before the phrase is over risks. */
	static final String[] LONGER_WORDS = {
		"Look at the fire balloon over there.", "The ice walls are melting fast.", "That was a fire ballad.",
	};
	/** The spell, said mid-line after nonstop talk. */
	static final String MID_LINE = "Fire ball on that zombie now.";
	static final int[] TALK_SECONDS = {0, 4, 8, 12};
	static final int[] TALK_VOICES = {0, 3, 6, 9};

	/** One-word phrases, as the spec measured them. */
	static final Incantation FIREBALL = new Incantation("fireball", List.of("fire ball"), 3);
	static final Incantation HEAL = new Incantation("heal", List.of(), 3);
	static final Incantation SHIELD = new Incantation("shield", List.of(), 3);
	static final List<Incantation> ONE_WORD = List.of(FIREBALL, HEAL, SHIELD);
	/** Sentences with a bound one-word phrase in them, or starting a word: they cast, by design. */
	static final String[] ONE_WORD_INSIDE = {
		"Craft me an iron sword and a shield for the fight.", "I need some healing potions.",
	};

	private static SpeechEngine engine;
	private static Spotter model;
	private static SpotterVad vad;
	private static KokoroTakes takes;
	private static Path out;
	private static final Map<Way, Stats> STATS = new LinkedHashMap<>();
	private static long naiveNanos;
	private static double naiveSeconds;

	/** What one way did over the whole test. */
	static final class Stats {
		final List<Double> latencies = new ArrayList<>();
		final List<Double> clockLatencies = new ArrayList<>();
		final List<Double> startErrors = new ArrayList<>();
		final List<Double> endErrors = new ArrayList<>();
		final List<Double> recheckMillis = new ArrayList<>();
		long nanos;
		double seconds;
		double speech;
		int twice;
	}

	@BeforeAll
	static void setUp() throws Exception {
		engine = TestAssets.engine(VfgConfig.DEFAULT_MODEL, 2, 0.5f);
		long t0 = System.nanoTime();
		model = TestAssets.spotter();
		vad = TestAssets.spotterVad(VfgConfig.DEFAULT_VAD_THRESHOLD);
		takes = new KokoroTakes();
		out = Path.of("build/kws-test").toAbsolutePath();
		String dir = System.getProperty("vfg.kwsOut");
		if (dir != null) {
			out = Path.of(dir);
		}
		Files.createDirectories(out);
		Files.deleteIfExists(out.resolve("report.txt"));
		for (Way way : WAYS) {
			STATS.put(way, new Stats());
		}
		say("keyword model loaded in %d ms (threshold %s, boost %s, %d trailing blank frames); the two ways: %s",
			(System.nanoTime() - t0) / 1_000_000, THRESHOLD, BOOST, model.trailingBlanks(), WAYS);
	}

	@AfterAll
	static void tearDown() {
		say("%n== summary ==");
		for (Map.Entry<Way, Stats> e : STATS.entrySet()) {
			Stats s = e.getValue();
			if (s.latencies.isEmpty()) {
				continue;
			}
			say("%s: after the phrase ends, with its compute: median %+.2f s, 90th percentile %+.2f s, %+.2f to %+.2f s"
				+ " over %d hits where the phrase ends the take (on the audio clock alone: median %+.2f s);"
				+ " started_at less the true start: median %+.2f s, ended_at less the true end: median %+.2f s;"
				+ " %d sayings cast twice", e.getKey(), median(s.latencies), p90(s.latencies), Collections.min(s.latencies),
				Collections.max(s.latencies), s.latencies.size(), median(s.clockLatencies), median(s.startErrors),
				median(s.endErrors), s.twice);
			say("  CPU: %.3f of one core over %.0f s of audio, %.3f while someone speaks (%.0f s of speech)%s",
				s.nanos / 1e9 / s.seconds, s.seconds, s.nanos / 1e9 / s.speech, s.speech, s.recheckMillis.isEmpty() ? ""
					: String.format(Locale.ROOT, "; %d re-checks, median %.1f ms, 90th percentile %.1f ms, %.1f a second"
						+ " of speech", s.recheckMillis.size(), median(s.recheckMillis), p90(s.recheckMillis),
						s.recheckMillis.size() / s.speech));
		}
		if (naiveSeconds > 0) {
			say("CPU, every window decoded (one stream throughout): %.4f of real time (%.1f s of audio in %d ms)",
				naiveNanos / 1e9 / naiveSeconds, naiveSeconds, naiveNanos / 1_000_000);
		}
		if (takes != null) {
			takes.close();
		}
		if (vad != null) {
			vad.close();
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
			SpeechTimeline t = alone("Fire ball!", voice);
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

	/**
	 * The two-word phrases said alone and ending a sentence, in 11 voices: hits, and how long after
	 * the phrase's sound ends each is heard. The spec's bars are checked on the mod's way, over all of
	 * them.
	 */
	@Test
	void aPhraseIsHeardAsItEnds() throws Exception {
		Map<Way, Integer> fireAlone = new LinkedHashMap<>();
		for (Way way : WAYS) {
			List<Double> latencies = new ArrayList<>();
			StringBuilder cells = new StringBuilder();
			StringBuilder eachFireBall = new StringBuilder();
			int twice = 0;
			int falseCasts = 0;
			for (Incantation inc : TIMED) {
				int alone = 0;
				int ending = 0;
				for (int voice = 0; voice < KokoroTakes.VOICES; voice++) {
					List<String> texts = said(inc);
					for (int i = 0; i < texts.size(); i++) {
						SpeechTimeline t = alone(texts.get(i), voice);
						Heard h = listen(t, List.of(inc), model, THRESHOLD, BOOST, way);
						List<Integer> mine = castsOf(h, inc.phrase());
						falseCasts += h.casts().size() - mine.size();
						if (mine.size() > 1) {
							twice++;
						}
						if (!mine.isEmpty()) {
							latencies.add(measure(way, t.clips().getFirst(), h, mine.getFirst(), i == 0));
							if (i == 0) {
								alone++;
							} else {
								ending++;
							}
						}
						if (i == 0 && inc == FIRE_BALL) {
							eachFireBall.append(String.format(Locale.ROOT, " v%d:%s", voice, heardWords(h)));
						}
					}
				}
				cells.append(String.format(Locale.ROOT, "; \"%s\" alone %d of 11, ending a sentence %d of 22", inc.phrase(),
					alone, ending));
				if (inc == FIRE_BALL) {
					fireAlone.put(way, alone);
				}
			}
			STATS.get(way).twice += twice;
			say("%n== %s: %d two-word phrases alone and ending a sentence (Kokoro, 11 voices) ==%n%s;"
				+ " %d false casts, %d sayings cast twice%n  after the phrase ends, with its compute: median %+.2f s,"
				+ " 90th percentile %+.2f s over %d hits%n  \"fire ball\" alone:%s", way, TIMED.size(), cells.substring(2),
				falseCasts, twice, median(latencies), p90(latencies), latencies.size(), eachFireBall);
			if (way == MOD) {
				assertTrue(median(latencies) <= 0.10, "median " + median(latencies) + " s after the phrase");
				assertTrue(p90(latencies) <= 0.20, "90th percentile " + p90(latencies) + " s after the phrase");
				assertEquals(0, twice, "sayings cast twice");
			}
		}
		assertTrue(fireAlone.get(MOD) >= BEFORE_ALONE, "fire ball alone: " + fireAlone);
		assertTrue(fireAlone.get(MOD) >= fireAlone.get(STREAM), "fire ball alone: " + fireAlone);
	}

	@Test
	void fireBallInsideASentenceIsHeard() throws Exception {
		Map<Way, Integer> heard = new LinkedHashMap<>();
		for (Way way : WAYS) {
			int hits = 0;
			int tries = 0;
			int twice = 0;
			StringBuilder each = new StringBuilder();
			for (int i = 0; i < IN_SENTENCES.length; i++) {
				for (int voice : new int[] {i, i + 4, i + 7}) {
					SpeechTimeline t = alone(IN_SENTENCES[i], voice);
					Heard h = listen(t, List.of(FIRE_BALL), model, THRESHOLD, BOOST, way);
					tries++;
					List<Integer> mine = castsOf(h, "fire ball");
					hits += mine.isEmpty() ? 0 : 1;
					twice += mine.size() > 1 ? 1 : 0;
					each.append(String.format(Locale.ROOT, " s%d v%d:%s", i + 1, voice, heardWords(h)));
				}
			}
			heard.put(way, hits);
			STATS.get(way).twice += twice;
			say("%n== %s: \"fire ball\" inside a sentence (Kokoro, 4 sentences x 3 voices) ==%n%d of %d heard, %d cast"
				+ " twice;%s", way, hits, tries, twice, each);
			if (way == MOD) {
				assertEquals(0, twice, "sayings cast twice");
			}
		}
		assertTrue(heard.get(MOD) >= BEFORE_INSIDE && heard.get(MOD) >= heard.get(STREAM), "inside a sentence: " + heard);
	}

	@Test
	void kamehamehaIsHeardThroughItsAliases() throws Exception {
		for (Way way : WAYS) {
			for (String said : KAMEHAMEHA_SAID) {
				int hits = 0;
				Map<String, Integer> by = new TreeMap<>();
				for (int voice = 0; voice < KokoroTakes.VOICES; voice++) {
					SpeechTimeline t = alone(said, voice);
					Heard h = listen(t, List.of(KAMEHAMEHA), model, THRESHOLD, BOOST, way);
					List<Integer> mine = castsOf(h, KAMEHAMEHA.phrase());
					if (!mine.isEmpty()) {
						hits++;
						measure(way, t.clips().getFirst(), h, mine.getFirst());
					}
					STATS.get(way).twice += mine.size() > 1 ? 1 : 0;
					h.casts().forEach(c -> by.merge(c.heard(), 1, Integer::sum));
				}
				say("%n== %s: \"kamehameha\" said \"%s\" (Kokoro, 11 voices), bound with %s ==%n%d of 11 heard; by"
					+ " keyword %s", way, said, KAMEHAMEHA.aliases(), hits, by);
			}
		}
	}

	/**
	 * Every incantation bound at once, over ordinary requests, and over near misses with a phrase's
	 * words in them: every cast is a false one, and the mod's way makes none.
	 */
	@Test
	void noFalseCastsOnSentencesWithoutAPhrase() throws Exception {
		for (Way way : WAYS) {
			int total = 0;
			for (Object[][] set : new Object[][][] {ORDINARY, NEAR_MISSES}) {
				SpeechTimeline t = sentences(set);
				Heard h = listen(t, ALL, model, THRESHOLD, BOOST, way);
				List<String> which = new ArrayList<>();
				for (SpotterWorker.Cast c : h.casts()) {
					which.add("\"" + c.heard() + "\" in \"" + sentenceAt(t, c) + "\"");
				}
				total += h.casts().size();
				say("%n== %s: false casts: %d %s, %s bound ==%n%d false casts in %.0f s of audio%s", way, set.length,
					set == ORDINARY ? "ordinary requests" : "near misses (" + Arrays.stream(set).map(o -> (String) o[0])
						.toList() + ")", ALL.stream().map(Incantation::phrase).toList(), h.casts().size(), h.seconds(),
					which.isEmpty() ? "" : ": " + String.join(", ", which));
			}
			if (way == MOD) {
				assertEquals(0, total, "false casts with the re-check");
			}
		}
	}

	/**
	 * sherpa-onnx issue #3990: a stream in silence resets itself every ~1.6 s and drops the start of
	 * a keyword said just then. "Fire ball" after 1.0 to 2.0 s of silence, in 0.05 s steps, two voices:
	 * the plain way (one stream, every window decoded) against the mod's two ways. Reported, and
	 * neither may miss more.
	 */
	@Test
	void aPhraseAfterAPauseIsHeard() throws Exception {
		Keywords k = Keywords.build(List.of(FIRE_BALL), model.pieces(), model.tokens(), THRESHOLD, BOOST);
		int tries = 0;
		int naiveHits = 0;
		List<String> naiveMissed = new ArrayList<>();
		Map<Way, Integer> hits = new LinkedHashMap<>();
		Map<Way, List<String>> missed = new LinkedHashMap<>();
		for (Way way : WAYS) {
			hits.put(way, 0);
			missed.put(way, new ArrayList<>());
		}
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
				for (Way way : WAYS) {
					Heard h = listen(t, List.of(FIRE_BALL), model, THRESHOLD, BOOST, way);
					List<Integer> mine = castsOf(h, "fire ball");
					if (mine.isEmpty()) {
						missed.get(way).add(label);
					} else {
						hits.merge(way, 1, Integer::sum);
						measure(way, t.clips().getFirst(), h, mine.getFirst());
					}
					STATS.get(way).twice += mine.size() > 1 ? 1 : 0;
				}
			}
		}
		say("%n== \"fire ball\" after 1.0 to 2.0 s of silence (sherpa-onnx #3990) ==");
		say("one stream, every window decoded: %d of %d heard%s", naiveHits, tries,
			naiveMissed.isEmpty() ? "" : "; missed after " + naiveMissed);
		for (Way way : WAYS) {
			say("%s: %d of %d heard%s", way, hits.get(way), tries,
				missed.get(way).isEmpty() ? "" : "; missed after " + missed.get(way));
			assertTrue(hits.get(way) >= naiveHits, way + " heard " + hits.get(way) + ", the plain way " + naiveHits);
		}
	}

	@Test
	void latencyAndTimesAreOnTheAudioClock() throws Exception {
		for (int voice : new int[] {1, 6}) {
			SpeechTimeline t = alone("Fire ball!", voice);
			Heard h = listen(t, List.of(FIRE_BALL), model, THRESHOLD, BOOST, MOD);
			assertEquals(1, h.casts().size(), "voice " + voice);
			SpotterWorker.Cast c = h.casts().getFirst();
			long start = T0 + SpeechTimeline.millisOf(t.clips().getFirst().start());
			long end = T0 + SpeechTimeline.millisOf(t.clips().getFirst().end());
			double lateness = (c.heardAtMillis() - end) / 1000.0;
			assertTrue(lateness > -0.5 && lateness < 1.2, "heard " + lateness + " s after the phrase");
			assertTrue(Math.abs(c.startedAtMillis() - start) <= 300, "started_at off by " + (c.startedAtMillis() - start));
			assertTrue(Math.abs(c.endedAtMillis() - end) <= 300, "ended_at off by " + (c.endedAtMillis() - end));
			assertTrue(c.endedAtMillis() <= c.heardAtMillis(), "ended_at after the newest sample");
		}
	}

	/**
	 * The spotter's own VAD over the ordinary requests and near misses: what a window costs, and how
	 * long after each sentence's sound it says speech ended, against the capture's VAD. Reported.
	 */
	@Test
	void theSpottersVadEndsSpeechSoonerAndCostsLittle() throws Exception {
		List<Double> own = new ArrayList<>();
		List<Double> capture = new ArrayList<>();
		long nanos = 0;
		int windows = 0;
		for (Object[][] set : new Object[][][] {ORDINARY, NEAR_MISSES}) {
			SpeechTimeline t = sentences(set);
			float[] audio = t.at16k();
			CaptureProcessor lines = new CaptureProcessor(() -> engine, item -> {});
			lines.onStarted(true);
			vad.reset();
			boolean[] was = new boolean[2];
			List<Double> ownEnds = new ArrayList<>();
			List<Double> captureEnds = new ArrayList<>();
			for (int off = 0; off + SpeechEngine.WINDOW <= audio.length; off += SpeechEngine.WINDOW) {
				float[] w = Arrays.copyOfRange(audio, off, off + SpeechEngine.WINDOW);
				double at = (off + SpeechEngine.WINDOW) / 16000.0;
				long a = System.nanoTime();
				boolean ownNow = vad.speaking(w);
				nanos += System.nanoTime() - a;
				windows++;
				lines.onWindow(w, T0 + Math.round(at * 1000));
				boolean captureNow = lines.speaking();
				if (was[0] && !ownNow) {
					ownEnds.add(at);
				}
				if (was[1] && !captureNow) {
					captureEnds.add(at);
				}
				was[0] = ownNow;
				was[1] = captureNow;
			}
			lines.onStopped(StopReason.DISCARD);
			for (SpeechTimeline.Clip c : t.clips()) {
				double end = SpeechTimeline.millisOf(c.end()) / 1000.0;
				ownEnds.stream().filter(e -> e > end - 0.1).findFirst().ifPresent(e -> own.add(e - end));
				captureEnds.stream().filter(e -> e > end - 0.1).findFirst().ifPresent(e -> capture.add(e - end));
			}
		}
		say("%n== the spotter's own VAD (Silero, %.2f s of silence ends speech), over %d sentences ==%nspeech ends for it"
			+ " a median %.2f s after the sentence's sound (90th percentile %.2f s), for the capture's VAD %.2f s (%.2f s);"
			+ " %.0f µs a window, %.4f of one core", Spotter.VAD_MIN_SILENCE_SECONDS, ORDINARY.length + NEAR_MISSES.length,
			median(own), p90(own), median(capture), p90(capture), nanos / 1e3 / windows, nanos / 1e9 / (windows * 0.032));
	}

	/**
	 * "fire" and "fire ball" both bound: "fire ball" alone and inside a sentence should cast "fire
	 * ball", never "fire", and "fire" said alone still casts "fire" (by the stream, as it starts the
	 * longer one). Reported.
	 */
	@Test
	void aOneWordPhraseThatStartsALongerOneIsCounted() throws Exception {
		List<Incantation> both = List.of(FIRE, FIRE_BALL);
		for (Way way : WAYS) {
			int ball = 0;
			int ballTries = 0;
			int fireInBall = 0;
			List<SpeechTimeline> takes = new ArrayList<>();
			for (int voice = 0; voice < KokoroTakes.VOICES; voice++) {
				takes.add(alone("Fire ball!", voice));
			}
			for (int i = 0; i < IN_SENTENCES.length; i++) {
				for (int voice : new int[] {i, i + 4, i + 7}) {
					takes.add(alone(IN_SENTENCES[i], voice));
				}
			}
			for (SpeechTimeline t : takes) {
				Heard h = listen(t, both, model, THRESHOLD, BOOST, way);
				ballTries++;
				ball += castsOf(h, "fire ball").isEmpty() ? 0 : 1;
				fireInBall += castsOf(h, "fire").size();
			}
			int fire = 0;
			List<Double> latencies = new ArrayList<>();
			for (int voice = 0; voice < KokoroTakes.VOICES; voice++) {
				SpeechTimeline t = alone("Fire!", voice);
				Heard h = listen(t, both, model, THRESHOLD, BOOST, way);
				List<Integer> mine = castsOf(h, "fire");
				if (!mine.isEmpty()) {
					fire++;
					latencies.add(heardSeconds(t.clips().getFirst(), h, mine.getFirst()));
				}
			}
			say("%n== %s: \"fire\" and \"fire ball\" both bound (Kokoro) ==%n\"fire ball\" alone and inside a"
				+ " sentence cast \"fire ball\" in %d of %d takes, and \"fire\" %d times; \"Fire!\" alone cast \"fire\""
				+ " in %d of 11, a median %+.2f s after it ends", way, ball, ballTries, fireInBall, fire,
				median(latencies));
		}
	}

	/** A bound phrase as the start of a longer word ("fire balloon"): how many takes cast it. Reported. */
	@Test
	void phrasesThatStartLongerWordsAreCounted() throws Exception {
		for (Way way : WAYS) {
			StringBuilder each = new StringBuilder();
			int total = 0;
			for (String text : LONGER_WORDS) {
				int cast = 0;
				for (int voice = 0; voice < KokoroTakes.VOICES; voice++) {
					Heard h = listen(alone(text, voice), List.of(FIRE_BALL, ICE_WALL), model, THRESHOLD, BOOST, way);
					cast += h.casts().isEmpty() ? 0 : 1;
				}
				total += cast;
				each.append(String.format(Locale.ROOT, "; \"%s\" %d", text, cast));
			}
			say("%n== %s: a phrase starting a longer word, \"fire ball\" and \"ice wall\" bound (Kokoro, 11 voices) =="
				+ "%n%d of %d takes cast%s", way, total, LONGER_WORDS.length * KokoroTakes.VOICES, each);
		}
	}

	/** "Fire ball on that zombie now." after about 0, 4, 8 and 12 s of nonstop talk. Reported. */
	@Test
	void aSpellAfterNonstopTalkIsCounted() throws Exception {
		for (Way way : WAYS) {
			StringBuilder each = new StringBuilder();
			for (int seconds : TALK_SECONDS) {
				int hits = 0;
				int falseCasts = 0;
				for (int voice : TALK_VOICES) {
					SpeechTimeline t = afterTalk(seconds, voice);
					Heard h = listen(t, ALL, model, THRESHOLD, BOOST, way);
					long spell = T0 + SpeechTimeline.millisOf(t.clips().getLast().start());
					List<Integer> mine = castsOf(h, "fire ball");
					boolean heard = mine.stream().anyMatch(i -> h.casts().get(i).endedAtMillis() >= spell);
					hits += heard ? 1 : 0;
					falseCasts += h.casts().size() - (heard ? 1 : 0);
				}
				each.append(String.format(Locale.ROOT, "%n  after ~%2d s of talk: heard %d of %d, %d false casts", seconds,
					hits, TALK_VOICES.length, falseCasts));
			}
			say("%n== %s: \"%s\" after nonstop talk, every phrase bound (Kokoro, voices %s) ==%s", way, MID_LINE,
				Arrays.toString(TALK_VOICES), each);
		}
	}

	/** "fireball", "heal" and "shield" bound as one-word phrases: hits, latency, and what else casts. Reported. */
	@Test
	void oneWordPhrasesAreCounted() throws Exception {
		for (Way way : WAYS) {
			List<Double> latencies = new ArrayList<>();
			StringBuilder cells = new StringBuilder();
			StringBuilder doubled = new StringBuilder();
			int twice = 0;
			for (Incantation inc : ONE_WORD) {
				int alone = 0;
				int ending = 0;
				for (int voice = 0; voice < KokoroTakes.VOICES; voice++) {
					for (String text : List.of(capitalized(inc.phrase()) + "!", "Okay, " + inc.phrase() + "!")) {
						SpeechTimeline t = alone(text, voice);
						Heard h = listen(t, ONE_WORD, model, THRESHOLD, BOOST, way);
						List<Integer> mine = castsOf(h, inc.phrase());
						if (mine.size() > 1) {
							twice++;
							doubled.append(String.format(Locale.ROOT, "%n    v%d \"%s\": %s", voice, text,
								described(t.clips().getFirst(), h)));
						}
						if (!mine.isEmpty()) {
							double late = heardSeconds(t.clips().getFirst(), h, mine.getFirst());
							latencies.add(late);
							if (text.startsWith("Okay")) {
								ending++;
							} else {
								alone++;
							}
						}
					}
				}
				cells.append(String.format(Locale.ROOT, "; \"%s\" alone %d of 11, ending a sentence %d of 11",
					inc.phrase(), alone, ending));
			}
			StringBuilder inside = new StringBuilder();
			for (String text : ONE_WORD_INSIDE) {
				int cast = 0;
				for (int voice = 0; voice < KokoroTakes.VOICES; voice++) {
					cast += listen(alone(text, voice), ONE_WORD, model, THRESHOLD, BOOST, way).casts().isEmpty() ? 0 : 1;
				}
				inside.append(String.format(Locale.ROOT, "; \"%s\" %d of 11", text, cast));
			}
			List<String> falseCasts = new ArrayList<>();
			for (Object[][] set : new Object[][][] {ORDINARY, NEAR_MISSES}) {
				SpeechTimeline t = sentences(set);
				for (SpotterWorker.Cast c : listen(t, ONE_WORD, model, THRESHOLD, BOOST, way).casts()) {
					falseCasts.add("\"" + c.heard() + "\" in \"" + sentenceAt(t, c) + "\"");
				}
			}
			say("%n== %s: one-word phrases %s bound (Kokoro, 11 voices) ==%n%s; %d cast twice%s%n  after the phrase"
				+ " ends, with its compute: median %+.2f s, 90th percentile %+.2f s over %d hits%n  sentences with one in"
				+ " them that cast%s%n  %d casts in the %d ordinary requests and near misses%s", way,
				ONE_WORD.stream().map(Incantation::phrase).toList(), cells.substring(2), twice, doubled, median(latencies),
				p90(latencies), latencies.size(), inside, falseCasts.size(), ORDINARY.length + NEAR_MISSES.length,
				falseCasts.isEmpty() ? "" : ": " + String.join(", ", falseCasts));
		}
	}

	/**
	 * Opt in with {@code -Dvfg.kwsSweep=true} ({@code ./gradlew kwsTest -Pkws-sweep}): hits and false
	 * casts over thresholds, boosts and trailing blanks, on the stream alone, to pick the defaults.
	 */
	@Test
	void sweep() throws Exception {
		assumeTrue(Boolean.getBoolean("vfg.kwsSweep"), "opt in with -Pkws-sweep");
		SpeechTimeline ordinary = sentences(ORDINARY);
		SpeechTimeline near = sentences(NEAR_MISSES);
		say("%n== sweep, the stream alone: hits of fire ball alone /11, in a sentence /12, kamehameha /22; false casts"
			+ " in 16 ordinary requests and 6 near misses ==");
		for (int trailing : new int[] {1, 2}) {
			try (Spotter m = Spotter.load(TestAssets.nativesDir(), TestAssets.kwsModelDir(), trailing)) {
				for (double threshold : new double[] {0.1, 0.15, 0.2, 0.25, 0.35}) {
					for (double boost : new double[] {1.0, 1.5, 2.0, 3.0}) {
						int alone = 0;
						int inside = 0;
						int kame = 0;
						for (int voice = 0; voice < KokoroTakes.VOICES; voice++) {
							alone += listen(alone("Fire ball!", voice), List.of(FIRE_BALL), m, threshold, boost, STREAM)
								.casts().isEmpty() ? 0 : 1;
							for (String said : KAMEHAMEHA_SAID) {
								kame += listen(alone(said, voice), List.of(KAMEHAMEHA), m, threshold, boost, STREAM)
									.casts().isEmpty() ? 0 : 1;
							}
						}
						for (int i = 0; i < IN_SENTENCES.length; i++) {
							for (int voice : new int[] {i, i + 4, i + 7}) {
								inside += listen(alone(IN_SENTENCES[i], voice), List.of(FIRE_BALL), m, threshold, boost,
									STREAM).casts().isEmpty() ? 0 : 1;
							}
						}
						int falseCasts = listen(ordinary, ALL, m, threshold, boost, STREAM).casts().size();
						int nearCasts = listen(near, ALL, m, threshold, boost, STREAM).casts().size();
						say("trailing %d, threshold %.2f, boost %.1f: alone %2d, sentence %2d, kamehameha %2d; false %d,"
							+ " near %d", trailing, threshold, boost, alone, inside, kame, falseCasts, nearCasts);
					}
				}
			}
		}
	}

	/**
	 * Opt in with {@code -Dvfg.kwsLead=true} ({@code ./gradlew kwsTest -Pkws-lead}): the re-check
	 * with its leading silence kept whole, cut to {@link SpotterWorker#LEAD_WINDOWS} (the mod's) and
	 * to 0.64 s, and reaching back 1.2 s, over the phrases alone and ending a sentence, after a pause,
	 * and after nonstop talk: hits and latency, to pick how much silence a re-check keeps.
	 */
	@Test
	void leadingSilence() throws Exception {
		assumeTrue(Boolean.getBoolean("vfg.kwsLead"), "opt in with -Pkws-lead");
		List<Way> ways = List.of(new Way("3 s, its silence kept whole", 3, -1),
			new Way("3 s, its silence cut to 0.32 s", 3, 10), new Way("3 s, its silence cut to 0.64 s", 3, 20),
			new Way("1.2 s, its silence kept whole", 1.2, -1), new Way("1.2 s, its silence cut to 0.32 s", 1.2, 10));
		say("%n== the re-check's leading silence: hits alone /22, ending a sentence /44, after a pause /42, after"
			+ " talk /16; after the phrase ends, with its compute ==");
		for (Way way : ways) {
			List<Double> latencies = new ArrayList<>();
			int alone = 0;
			int ending = 0;
			int pause = 0;
			int talk = 0;
			List<Double> recheck = new ArrayList<>();
			for (Incantation inc : List.of(FIRE_BALL, ICE_WALL)) {
				for (int voice = 0; voice < KokoroTakes.VOICES; voice++) {
					List<String> texts = said(inc);
					for (int i = 0; i < texts.size(); i++) {
						SpeechTimeline t = alone(texts.get(i), voice);
						Heard h = listen(t, List.of(inc), model, THRESHOLD, BOOST, way, recheck);
						List<Integer> mine = castsOf(h, inc.phrase());
						if (!mine.isEmpty()) {
							latencies.add(heardSeconds(t.clips().getFirst(), h, mine.getFirst()));
							if (i == 0) {
								alone++;
							} else {
								ending++;
							}
						}
					}
				}
			}
			for (int voice : new int[] {0, 5}) {
				Wav.Audio take = take("Fire ball!", voice);
				for (int step = 0; step <= 20; step++) {
					SpeechTimeline t = new SpeechTimeline().silence(1.0 + step * 0.05).speech("fire ball", List.of(), take)
						.silence(1.0);
					Heard h = listen(t, List.of(FIRE_BALL), model, THRESHOLD, BOOST, way, recheck);
					List<Integer> mine = castsOf(h, "fire ball");
					if (!mine.isEmpty()) {
						pause++;
						latencies.add(heardSeconds(t.clips().getFirst(), h, mine.getFirst()));
					}
				}
			}
			for (int seconds : TALK_SECONDS) {
				for (int voice : TALK_VOICES) {
					SpeechTimeline t = afterTalk(seconds, voice);
					Heard h = listen(t, ALL, model, THRESHOLD, BOOST, way, recheck);
					long spell = T0 + SpeechTimeline.millisOf(t.clips().getLast().start());
					talk += castsOf(h, "fire ball").stream().anyMatch(i -> h.casts().get(i).endedAtMillis() >= spell)
						? 1 : 0;
				}
			}
			say("%s: alone %d, ending %d, after a pause %d, after talk %d; median %+.2f s, 90th percentile %+.2f s"
				+ " over %d hits; a re-check median %.1f ms", way, alone, ending, pause, talk, median(latencies),
				p90(latencies), latencies.size(), median(recheck));
		}
	}

	// --- the harness ---

	/** A phrase said alone, then ending two sentences, as the spec's measurements said it. */
	private static List<String> said(Incantation inc) {
		String p = inc.phrase();
		return List.of(capitalized(p) + "!", "Okay, " + p + "!", "Watch this. " + capitalized(p) + "!");
	}

	/** One take in its voice, with 1 s of silence before it and 1.5 s after. */
	private static SpeechTimeline alone(String text, int voice) throws Exception {
		return new SpeechTimeline().silence(1.0).speech(text, List.of(), take(text, voice)).silence(1.5);
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
	 * Ordinary requests in {@code voice}, 80 ms apart, so no VAD hears a pause between them, until
	 * about {@code seconds} of talk; then {@link #MID_LINE}, the last clip.
	 */
	private static SpeechTimeline afterTalk(int seconds, int voice) throws Exception {
		SpeechTimeline t = new SpeechTimeline().silence(1.0);
		double talk = 0;
		for (int i = voice; talk < seconds; i++) {
			t.speech((String) ORDINARY[i % ORDINARY.length][0], List.of(), take((String) ORDINARY[i % ORDINARY.length][0],
				voice)).silence(0.08);
			talk = SpeechTimeline.millisOf(t.clips().getLast().end() - t.clips().getFirst().start()) / 1000.0;
		}
		return t.speech(MID_LINE, List.of(), take(MID_LINE, voice)).silence(1.5);
	}

	/**
	 * The casts the spotter made over a timeline, when each was heard (seconds into it, its compute
	 * included), the time it spent and the audio's length.
	 */
	record Heard(List<SpotterWorker.Cast> casts, List<Double> heardAt, long nanos, double seconds) {}

	static Heard listen(SpeechTimeline t, List<Incantation> list, KeywordModel m, double threshold, double boost,
			Way way) {
		return listen(t, list, m, threshold, boost, way, STATS.containsKey(way) && m == model ? null : new ArrayList<>());
	}

	/**
	 * Plays a timeline at 16 kHz through the capture's VAD (a {@link CaptureProcessor} on the real
	 * engine) and a {@link SpotterWorker} with the spotter's own VAD, 32 ms at a time, as the capture
	 * thread does, with {@code vfg-kws} played on the audio clock (see the class comment). The way's
	 * statistics take its time unless {@code recheckMillis} is given, which takes each re-check's.
	 */
	static Heard listen(SpeechTimeline t, List<Incantation> list, KeywordModel m, double threshold, double boost,
			Way way, List<Double> recheckMillis) {
		float[] audio = t.at16k();
		List<SpotterWorker.Cast> casts = new ArrayList<>();
		List<Long> castNanos = new ArrayList<>();
		SpotterWorker worker = new SpotterWorker(true, threshold, boost, way.recheckSeconds(), c -> {
			casts.add(c);
			castNanos.add(System.nanoTime());
		});
		worker.setModel(kept(m), kept(vad));
		worker.leadWindows(way.leadWindows());
		Stats stats = recheckMillis == null ? STATS.get(way) : null;
		List<Double> rechecks = recheckMillis != null ? recheckMillis : stats.recheckMillis;
		worker.recheckTimes(n -> rechecks.add(n / 1e6));
		worker.setIncantations(list);
		worker.onStarted(true);
		CaptureProcessor capture = new CaptureProcessor(() -> engine, item -> {});
		capture.onStarted(true);
		KwsThread kws = new KwsThread(worker, casts, castNanos);
		long samples = 0;
		for (int off = 0; off + SpeechEngine.WINDOW <= audio.length; off += SpeechEngine.WINDOW) {
			float[] w = Arrays.copyOfRange(audio, off, off + SpeechEngine.WINDOW);
			samples += w.length;
			double arrives = samples / 16000.0;
			if (kws.waiting > 0 && kws.free < arrives) {
				kws.turn(kws.free); // free before this window came: it takes what waits
			}
			long wall = T0 + samples * 1000 / 16000;
			capture.onWindow(w, wall);
			worker.onWindow(w, wall, capture.speaking());
			kws.waiting++;
			if (kws.free <= arrives) {
				kws.turn(arrives);
			}
		}
		while (kws.waiting > 0) {
			kws.turn(kws.free);
		}
		worker.onStopped(StopReason.DISCARD);
		capture.onStopped(StopReason.DISCARD);
		worker.shutdown(0);
		double seconds = audio.length / 16000.0;
		if (stats != null) {
			stats.nanos += kws.nanos;
			stats.seconds += seconds;
			for (SpeechTimeline.Clip c : t.clips()) {
				stats.speech += SpeechTimeline.millisOf(c.end() - c.start()) / 1000.0;
			}
		}
		return new Heard(casts, kws.heardAt, kws.nanos, seconds);
	}

	/** {@code vfg-kws} on the audio clock: busy until {@code free}, with {@code waiting} windows queued. */
	private static final class KwsThread {
		final SpotterWorker worker;
		final List<SpotterWorker.Cast> casts;
		final List<Long> castNanos;
		final List<Double> heardAt = new ArrayList<>();
		double free;
		int waiting;
		long nanos;

		KwsThread(SpotterWorker worker, List<SpotterWorker.Cast> casts, List<Long> castNanos) {
			this.worker = worker;
			this.casts = casts;
			this.castNanos = castNanos;
		}

		/**
		 * A turn starting at {@code at} seconds: every window waiting, then a re-check; each cast is
		 * heard as far into the turn as it came.
		 */
		void turn(double at) {
			int before = casts.size();
			long a = System.nanoTime();
			worker.drainNow();
			long spent = System.nanoTime() - a;
			for (int i = before; i < casts.size(); i++) {
				heardAt.add(at + (castNanos.get(i) - a) / 1e9);
			}
			nanos += spent;
			free = at + spent / 1e9;
			waiting = 0;
		}
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

	/** The spotter's VAD, shared the same way; the worker resets it when its audio starts. */
	static SpotterVad kept(SpotterVad v) {
		return new SpotterVad() {
			@Override
			public boolean speaking(float[] window) {
				return v.speaking(window);
			}

			@Override
			public void reset() {
				v.reset();
			}

			@Override
			public void close() {}
		};
	}

	/** The indexes of the casts of {@code phrase}. */
	private static List<Integer> castsOf(Heard h, String phrase) {
		List<Integer> out = new ArrayList<>();
		for (int i = 0; i < h.casts().size(); i++) {
			if (h.casts().get(i).phrase().equals(phrase)) {
				out.add(i);
			}
		}
		return out;
	}

	/** How long after the end of the phrase's sound cast {@code i} was heard, its compute included. */
	private static double heardSeconds(SpeechTimeline.Clip clip, Heard h, int i) {
		return h.heardAt().get(i) - SpeechTimeline.millisOf(clip.end()) / 1000.0;
	}

	/** Records a hit on a take that is the phrase alone: its latency, and how far its times are off. */
	private static double measure(Way way, SpeechTimeline.Clip clip, Heard h, int i) {
		return measure(way, clip, h, i, true);
	}

	/**
	 * Records a hit where the phrase ends the take: its latency, how far its end is off, and how far
	 * its start is when the take is the phrase {@code alone} (a sentence starts before its phrase).
	 */
	private static double measure(Way way, SpeechTimeline.Clip clip, Heard h, int i, boolean alone) {
		Stats s = STATS.get(way);
		SpotterWorker.Cast c = h.casts().get(i);
		long start = T0 + SpeechTimeline.millisOf(clip.start());
		long end = T0 + SpeechTimeline.millisOf(clip.end());
		double late = heardSeconds(clip, h, i);
		s.latencies.add(late);
		s.clockLatencies.add((c.heardAtMillis() - end) / 1000.0);
		if (alone) {
			s.startErrors.add((c.startedAtMillis() - start) / 1000.0);
		}
		s.endErrors.add((c.endedAtMillis() - end) / 1000.0);
		return late;
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

	/** Each cast: the keyword, which way, and its times against the clip's, in seconds. */
	private static String described(SpeechTimeline.Clip clip, Heard h) {
		long start = T0 + SpeechTimeline.millisOf(clip.start());
		long end = T0 + SpeechTimeline.millisOf(clip.end());
		List<String> out = new ArrayList<>();
		for (int i = 0; i < h.casts().size(); i++) {
			SpotterWorker.Cast c = h.casts().get(i);
			out.add(String.format(Locale.ROOT, "\"%s\" by %s, said %+.2f to %+.2f s, heard %+.2f s", c.heard(),
				c.via().words(), (c.startedAtMillis() - start) / 1000.0, (c.endedAtMillis() - end) / 1000.0,
				heardSeconds(clip, h, i)));
		}
		return String.join("; ", out);
	}

	private static String heardWords(Heard h) {
		return h.casts().isEmpty() ? "-" : String.join("+", h.casts().stream().map(SpotterWorker.Cast::heard).toList());
	}

	private static String capitalized(String phrase) {
		return Character.toUpperCase(phrase.charAt(0)) + phrase.substring(1);
	}

	private static Wav.Audio take(String text, int voice) throws Exception {
		return takes.say(text, voice % KokoroTakes.VOICES);
	}

	private static double median(List<Double> values) {
		if (values.isEmpty()) {
			return Double.NaN;
		}
		List<Double> sorted = new ArrayList<>(values);
		Collections.sort(sorted);
		int n = sorted.size();
		return n % 2 == 1 ? sorted.get(n / 2) : (sorted.get(n / 2 - 1) + sorted.get(n / 2)) / 2;
	}

	/** The 90th percentile, as the spec's measurements take it: the value 90% of the way up the sorted list. */
	private static double p90(List<Double> values) {
		if (values.isEmpty()) {
			return Double.NaN;
		}
		List<Double> sorted = new ArrayList<>(values);
		Collections.sort(sorted);
		return sorted.get(Math.min(sorted.size() - 1, (int) (0.9 * sorted.size())));
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
