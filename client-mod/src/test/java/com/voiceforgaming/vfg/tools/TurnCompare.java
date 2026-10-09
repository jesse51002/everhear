package com.voiceforgaming.vfg.tools;

import com.k2fsa.sherpa.onnx.OfflineRecognizer;
import com.k2fsa.sherpa.onnx.OfflineRecognizerResult;
import com.k2fsa.sherpa.onnx.OfflineStream;
import com.voiceforgaming.vfg.speech.SpeechEngine;
import com.voiceforgaming.vfg.speech.VoiceDetector;
import com.voiceforgaming.vfg.testing.SpeechTimeline;
import com.voiceforgaming.vfg.testing.SttModels;
import com.voiceforgaming.vfg.testing.TestAssets;
import com.voiceforgaming.vfg.testing.Tts;
import com.voiceforgaming.vfg.testing.Wav;
import com.voiceforgaming.vfg.turn.TurnDetector;
import com.voiceforgaming.vfg.turn.TurnDetectors;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HexFormat;
import java.util.List;
import java.util.Locale;
import java.util.Random;
import java.util.Set;
import java.util.TreeSet;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.function.BiFunction;
import java.util.function.Predicate;

/**
 * {@code ./gradlew turnCompare}: Smart Turn v3.2 (the mod's default) and turn-1-mini on two
 * labelled sets, each clip given to the models as the mod gives a line at a pause: from 0.5 s
 * before the first speech its own VAD finds to 0.2 s after the last. The public set is the
 * English clips of one shard of Pipecat's Smart Turn v3.2 test set, which {@code turnData}
 * prepares first ({@code tools/turn_data.py}); the game set is the playtest's direct asks spoken
 * by Kokoro TTS in every voice, whole, cut at a gap between words near the middle, and cut just
 * after an "uh" or "um" said in that gap. The same asks spoken by Kitten, the speech test's TTS,
 * are scored too, to show why the game set is not Kitten's. For each model and set: accuracy,
 * the share of unfinished clips scored finished, the share of finished ones kept waiting, and
 * the time per score; turn-1-mini's threshold, picked to cut off the public set's unfinished
 * clips as often as Smart Turn does at 0.5; and both models asked later in the pause, 0.6 s
 * after the speech. Measurement only: the mod is unchanged.
 */
public final class TurnCompare {
	private static final int RATE = 16000;
	private static final int THREADS = 2;
	/** What the mod gives a model around a line's speech: Pipecat's padding before, its 0.2 s of silence after. */
	private static final double BEFORE_SPEECH = 0.5;
	private static final double AFTER_SPEECH = 0.2;
	/** When the VAD itself ends a segment, the latest the mod could ask instead. */
	private static final double LATER = 0.6;
	/** The quiet floor of {@link SpeechTimeline}: -60 dBFS, a quiet room through a mic. */
	private static final float FLOOR_RMS = 0.001f;
	private static final double DEFAULT_THRESHOLD = 0.5;
	private static final double[] SWEEP = {0.3, 0.4, 0.5, 0.6, 0.7};
	/**
	 * The fillers, as written for the TTS: said alone, Kitten makes up words for them, but inside a
	 * sentence both TTS models say "uh" as a short schwa (Parakeet mostly leaves it out) and
	 * "ummm" as a held "um" (Parakeet hears "um" in all their voices; "um" as written, in fewer).
	 */
	private static final String[][] FILLERS = {{"uh", "uh"}, {"um", "ummm"}};
	private static final int MAX_EXAMPLES = 4;

	/** One clip a model scored: its set, an id, whether the speaker had finished, and what kind it is. */
	record Clip(String set, String id, boolean finished, Set<String> tags) {}

	/** One word of a transcript, with where it starts and ends in the clip (seconds, from Parakeet's tokens). */
	record Word(String text, double start, double end) {}

	/** One take for a TTS to say. */
	record Take(String text, int voice) {}

	/** Where a take is cut: the first half's words in the text, and the sample (-1 for no clean cut); every try. */
	record Found(int gap, int sample, List<String> tries) {}

	/** One spoken game set: which TTS, what became of its cuts, and a few clean ones to show. */
	record GameSet(String set, String tts, int asks, int voices, int cutMiddle, int cutOther, int cutNone,
			int fillerMade, int fillerFailed, List<String> examples) {
		int pairs() {
			return asks * voices;
		}
	}

	/** A turn model under test, and what it scored, clip by clip. */
	private static final class Run {
		final String name;
		final String label;
		final TurnDetector detector;
		final List<Float> scores = new ArrayList<>();
		final List<Long> nanos = new ArrayList<>();

		Run(String name, String label, TurnDetector detector) {
			this.name = name;
			this.label = label;
			this.detector = detector;
		}
	}

	private final List<Clip> clips = new ArrayList<>();
	private final List<Run> runs = new ArrayList<>();
	private final VoiceDetector vad;
	private final Path parakeetDir;
	private final StringBuilder notes = new StringBuilder();
	private final StringBuilder cuts = new StringBuilder("set\tid\tform\tgap\tcut (s)\tclean\twhole heard\t"
		+ "first half heard\tsecond half heard\n");

	private TurnCompare(VoiceDetector vad, Path parakeetDir) {
		this.vad = vad;
		this.parakeetDir = parakeetDir;
	}

	public static void main(String[] args) throws Exception {
		Path out = Path.of(System.getProperty("vfg.compareOut", "build/turn-compare")).toAbsolutePath();
		Files.createDirectories(out);
		Path labels = TestAssets.root().resolve("turn-data/public/labels.tsv");
		if (!Files.exists(labels)) {
			throw new IllegalStateException("no public set at " + labels + ": ./gradlew turnCompare prepares it "
				+ "first, or run uv run --with pyarrow --with soundfile tools/turn_data.py");
		}

		// The mod's VAD and Parakeet first: loading them loads ONNX Runtime, which the turn models then share.
		SpeechEngine engine = SttModels.engine(SttModels.PARAKEET_110M, THREADS, null, 0);
		TurnCompare compare = new TurnCompare(engine, SttModels.install(SttModels.PARAKEET_110M));
		compare.runs.add(new Run(TurnDetectors.SMART_TURN, "Smart Turn v3.2",
			TestAssets.turnDetector(TurnDetectors.SMART_TURN)));
		compare.runs.add(new Run(TurnDetectors.TURN_1_MINI, "turn-1-mini",
			TestAssets.turnDetector(TurnDetectors.TURN_1_MINI)));
		for (Run r : compare.runs) {
			r.detector.score(new float[RATE]); // warm-up, not timed
		}

		compare.publicSet(labels);
		Path asksFile = TestAssets.root().resolve("turn/game-asks.txt");
		List<GameSet> games = new ArrayList<>();
		if (Files.exists(asksFile)) {
			List<String> asks = Files.readAllLines(asksFile, StandardCharsets.UTF_8).stream().map(String::strip)
				.filter(s -> !s.isEmpty()).toList();
			games.add(compare.gameSet("kokoro", Tts.Model.KOKORO, "Kokoro", asks));
			games.add(compare.gameSet("kitten", Tts.Model.KITTEN, "Kitten", asks));
		} else {
			System.out.println("no " + asksFile + " (players' speech, kept out of the repo): skipping the game set");
		}
		for (Run r : compare.runs) {
			r.detector.close();
		}
		engine.close();
		compare.writeScores(out.resolve("scores.tsv"));
		Files.writeString(out.resolve("cuts.tsv"), compare.cuts, StandardCharsets.UTF_8);
		compare.report(out, games);
	}

	/** Scores one clip with every model, timing each score. */
	private void score(Clip clip, float[] audio) {
		clips.add(clip);
		for (Run r : runs) {
			long t = System.nanoTime();
			float p = r.detector.score(audio);
			r.nanos.add(System.nanoTime() - t);
			r.scores.add(p);
		}
	}

	// --- the public set ---

	/**
	 * Every clip of the public set three times: framed as the mod frames a line (set "public");
	 * the same with 0.6 s after the speech instead of 0.2 s, as if asked when the VAD ends the
	 * segment (set "later"); and as published (set "published"), which is how Pipecat measures
	 * its own accuracy.
	 */
	private void publicSet(Path labels) throws IOException {
		List<String> rows = Files.readAllLines(labels, StandardCharsets.UTF_8);
		List<String> head = Arrays.asList(rows.getFirst().split("\t"));
		int file = head.indexOf("file");
		int finished = head.indexOf("finished");
		int synthetic = head.indexOf("synthetic");
		int endfiller = head.indexOf("endfiller");
		List<Double> tailFinished = new ArrayList<>();
		List<Double> tailUnfinished = new ArrayList<>();
		int ownTail = 0;
		int noSpeech = 0;
		for (int i = 1; i < rows.size(); i++) {
			String[] c = rows.get(i).split("\t", -1);
			float[] audio = Wav.read(labels.resolveSibling(c[file])).samples();
			boolean done = c[finished].equals("1");
			String id = c[file].replace(".wav", "");
			Set<String> tags = new TreeSet<>();
			tags.add(c[synthetic].equals("1") ? "tts" : "human");
			if (c[endfiller].equals("1")) {
				tags.add("endfiller");
			}
			double[] speech = speech(audio);
			if (speech == null) {
				noSpeech++;
			} else {
				double tail = audio.length / (double) RATE - speech[1];
				(done ? tailFinished : tailUnfinished).add(tail);
				if (tail >= AFTER_SPEECH) {
					ownTail++;
				}
			}
			score(new Clip("public", id, done, tags), speech == null ? audio : frame(audio, speech, AFTER_SPEECH, i));
			score(new Clip("later", id, done, tags), speech == null ? audio : frame(audio, speech, LATER, i));
			score(new Clip("published", id, done, tags), audio);
			if (i % 100 == 0) {
				System.out.printf(Locale.ROOT, "public set: %d of %d clips%n", i, rows.size() - 1);
			}
		}
		notes.append(String.format(Locale.ROOT, "- **Silence after the last speech in the public clips as "
				+ "published**, by the mod's VAD: median %.2f s in finished clips and %.2f s in unfinished ones. %d of %d "
				+ "clips have at least %.1f s of their own; the rest are given the quiet floor after their audio runs "
				+ "out. %s%n",
			median(tailFinished), median(tailUnfinished), ownTail, tailFinished.size() + tailUnfinished.size(),
			AFTER_SPEECH, noSpeech == 0 ? "The VAD found speech in every clip."
				: noSpeech + " clips in which the VAD found no speech are given to the models as published."));
	}

	/**
	 * Where the mod's VAD hears speech in {@code audio}: the first speech sample and the last, in
	 * seconds, or {@code null} for none. The audio goes in 512-sample windows, as the capture
	 * thread feeds it, with a second of quiet floor after it so that the VAD ends the last segment
	 * on a pause, as it does in the mod.
	 */
	private double[] speech(float[] audio) {
		float[] padded = Arrays.copyOf(audio, audio.length + RATE);
		addFloor(padded, audio.length, padded.length, new Random(audio.length));
		vad.reset();
		List<VoiceDetector.RawSegment> raw = new ArrayList<>();
		for (int off = 0; off + SpeechEngine.WINDOW <= padded.length; off += SpeechEngine.WINDOW) {
			vad.accept(Arrays.copyOfRange(padded, off, off + SpeechEngine.WINDOW));
			raw.addAll(vad.drain());
		}
		vad.flush();
		raw.addAll(vad.drain());
		if (raw.isEmpty()) {
			return null;
		}
		VoiceDetector.RawSegment last = raw.getLast();
		return new double[] {raw.getFirst().start() / (double) RATE,
			Math.min(audio.length, last.start() + last.samples().length) / (double) RATE};
	}

	/**
	 * The clip as the mod gives it to a model at a pause after its speech: from 0.5 s before the
	 * first speech to {@code after} seconds after the last, with the quiet floor wherever the clip
	 * has no audio.
	 */
	private static float[] frame(float[] audio, double[] speech, double after, long seed) {
		int from = (int) Math.round((speech[0] - BEFORE_SPEECH) * RATE);
		int to = (int) Math.round((speech[1] + after) * RATE);
		float[] out = new float[to - from];
		int copyFrom = Math.max(0, from);
		int copyTo = Math.min(audio.length, to);
		System.arraycopy(audio, copyFrom, out, copyFrom - from, Math.max(0, copyTo - copyFrom));
		Random random = new Random(seed);
		addFloor(out, 0, copyFrom - from, random);
		addFloor(out, Math.max(0, copyTo - from), out.length, random);
		return out;
	}

	/** Framed as the mod frames it, or as given when the VAD hears no speech in it. */
	private float[] frame(float[] audio) {
		double[] speech = speech(audio);
		return speech == null ? audio : frame(audio, speech, AFTER_SPEECH, audio.length);
	}

	private static void addFloor(float[] a, int from, int to, Random random) {
		for (int i = from; i < to; i++) {
			a[i] += (float) (random.nextGaussian() * FLOOR_RMS);
		}
	}

	// --- the game sets ---

	/**
	 * Every ask in every voice of {@code model}, three ways. Whole: as said, finished. Cut: the
	 * same audio cut at a gap between words, the one nearest the middle that both halves confirm
	 * ({@link #cutBefore}); not finished. Filler: the ask said again with ", uh," or ", um," in
	 * that gap, cut just after the filler; not finished. A gap after a full stop or a question
	 * mark is never used: the first half would be a finished sentence. Every cut tried is written
	 * to {@code cuts.tsv}.
	 */
	private GameSet gameSet(String set, Tts.Model model, String tts, List<String> asks) throws Exception {
		Path cache = TestAssets.root().resolve("turn-compare-tts").resolve(model.name().toLowerCase(Locale.ROOT));
		int voices;
		try (Tts t = new Tts(model, 1)) {
			voices = t.voices();
		}
		List<Take> wholes = new ArrayList<>();
		for (String ask : asks) {
			for (int v = 0; v < voices; v++) {
				wholes.add(new Take(ask, v));
			}
		}
		speakAll(model, cache, wholes);
		List<Found> found = inParallel(set + ", finding the cuts", wholes.size(), (recognizer, p) -> {
			float[] whole = hear(cache, wholes.get(p));
			List<String> words = words(wholes.get(p));
			List<Word> said = transcribe(recognizer, whole);
			List<String> tries = new ArrayList<>();
			for (int k : gapsFromMiddle(words)) {
				int cut = cutBefore(recognizer, tries, set + "\t" + id(p, voices) + "\tcut", whole, said, words, k,
					false);
				if (cut > 0) {
					return new Found(k, cut, tries);
				}
			}
			return new Found(-1, -1, tries);
		});

		List<Take> fillerTakes = new ArrayList<>();
		for (int p = 0; p < wholes.size(); p++) {
			int k = found.get(p).gap();
			fillerTakes.add(k < 0 ? null : withFiller(wholes.get(p), k, filler(p, voices)[1]));
		}
		speakAll(model, cache, fillerTakes.stream().filter(t -> t != null).toList());
		List<Found> fillerFound = inParallel(set + ", cutting after the fillers", wholes.size(), (recognizer, p) -> {
			Take take = fillerTakes.get(p);
			if (take == null) {
				return null;
			}
			float[] spoken = hear(cache, take);
			List<String> tries = new ArrayList<>();
			int k = found.get(p).gap() + 1;
			String what = set + "\t" + id(p, voices) + "\t" + filler(p, voices)[0];
			int cut = cutBefore(recognizer, tries, what, spoken, transcribe(recognizer, spoken), words(take), k, true);
			return new Found(k, cut, tries);
		});

		// Scored one clip at a time, as the mod scores them, so each time is a score's alone.
		int cutMiddle = 0;
		int cutOther = 0;
		int cutNone = 0;
		int fillerMade = 0;
		int fillerFailed = 0;
		List<String> examples = new ArrayList<>();
		for (int p = 0; p < wholes.size(); p++) {
			Take take = wholes.get(p);
			List<String> words = words(take);
			String id = id(p, voices);
			float[] whole = hear(cache, take);
			score(new Clip(set, id + "-whole", true, Set.of("whole")), frame(whole));
			Found f = found.get(p);
			f.tries().forEach(t -> cuts.append(t).append('\n'));
			if (f.gap() < 0) {
				cutNone++;
				continue;
			}
			score(new Clip(set, id + "-cut", false, Set.of("cut", halfLength(f.gap()))),
				frame(Arrays.copyOf(whole, f.sample())));
			if (f.gap() == gapsFromMiddle(words).getFirst()) {
				cutMiddle++;
			} else {
				cutOther++;
			}
			if (take.voice() == 0 && examples.size() < MAX_EXAMPLES && quotable(words)) {
				examples.add(String.join(" ", words.subList(0, f.gap())) + " | "
					+ String.join(" ", words.subList(f.gap(), words.size())));
			}
			Found g = fillerFound.get(p);
			g.tries().forEach(t -> cuts.append(t).append('\n'));
			if (g.sample() < 0) {
				fillerFailed++;
				continue;
			}
			fillerMade++;
			score(new Clip(set, id + "-" + filler(p, voices)[0], false, Set.of("filler", filler(p, voices)[0])),
				frame(Arrays.copyOf(hear(cache, fillerTakes.get(p)), g.sample())));
		}
		System.out.printf(Locale.ROOT, "%s: %d asks in %d voices scored%n", set, asks.size(), voices);
		return new GameSet(set, tts, asks.size(), voices, cutMiddle, cutOther, cutNone, fillerMade, fillerFailed,
			examples);
	}

	/** How long a cut's first half is, in words, as a tag: "1-2 words", "3-4 words" or "5+ words". */
	private static String halfLength(int words) {
		return words <= 2 ? "1-2 words" : words <= 4 ? "3-4 words" : "5+ words";
	}

	private static String id(int pair, int voices) {
		return String.format(Locale.ROOT, "ask%03d-v%d", pair / voices + 1, pair % voices);
	}

	private static List<String> words(Take take) {
		return List.of(take.text().split("\\s+"));
	}

	/**
	 * {@code work} on 0 to {@code n - 1}, several at once, each worker with a Parakeet of its own;
	 * the results in order. Finding the cuts is most of the run, and is not what is timed.
	 */
	private <R> List<R> inParallel(String what, int n, BiFunction<OfflineRecognizer, Integer, R> work)
			throws Exception {
		List<OfflineRecognizer> made = Collections.synchronizedList(new ArrayList<>());
		ThreadLocal<OfflineRecognizer> mine = new ThreadLocal<>();
		AtomicInteger done = new AtomicInteger();
		ExecutorService pool = Executors.newFixedThreadPool(workers());
		try {
			List<Future<R>> futures = new ArrayList<>();
			for (int i = 0; i < n; i++) {
				int item = i;
				futures.add(pool.submit(() -> {
					OfflineRecognizer recognizer = mine.get();
					if (recognizer == null) {
						recognizer = new OfflineRecognizer(SpeechEngine.parakeet(parakeetDir, 1));
						mine.set(recognizer);
						made.add(recognizer);
					}
					R r = work.apply(recognizer, item);
					if (done.incrementAndGet() % 200 == 0) {
						System.out.printf(Locale.ROOT, "%s: %d of %d%n", what, done.get(), n);
					}
					return r;
				}));
			}
			List<R> results = new ArrayList<>(n);
			for (Future<R> f : futures) {
				results.add(f.get());
			}
			return results;
		} finally {
			pool.shutdown();
			pool.awaitTermination(1, TimeUnit.MINUTES);
			for (OfflineRecognizer r : made) {
				r.release();
			}
		}
	}

	/** Half the cores, at most six: each worker's TTS or Parakeet runs on one thread. */
	private static int workers() {
		return Math.clamp(Runtime.getRuntime().availableProcessors() / 2, 1, 6);
	}

	/** "uh" or "um", taking turns along both the asks and the voices, so that every voice says both. */
	private static String[] filler(int pair, int voices) {
		return FILLERS[(pair / voices + pair % voices) % FILLERS.length];
	}

	/** The take said again with ", uh," (or the like) in front of word {@code k}. */
	private static Take withFiller(Take take, int k, String filler) {
		List<String> words = new ArrayList<>(List.of(take.text().split("\\s+")));
		words.set(k - 1, words.get(k - 1).replaceAll("[,;:]+$", "") + ",");
		words.add(k, filler + ",");
		return new Take(String.join(" ", words), take.voice());
	}

	private static Path file(Path cache, Take take) {
		try {
			byte[] hash = MessageDigest.getInstance("SHA-256").digest(take.text().getBytes(StandardCharsets.UTF_8));
			return cache.resolve("v" + take.voice() + "-" + HexFormat.of().formatHex(hash, 0, 8) + ".wav");
		} catch (NoSuchAlgorithmException e) {
			throw new IllegalStateException(e);
		}
	}

	/** A take from disk, at 16 kHz through the mod's 48 kHz path, as {@code sttCompare} hears its TTS. */
	private static float[] hear(Path cache, Take take) {
		try {
			return SttCompare.micPath(SpeechTimeline.to48k(Wav.read(file(cache, take))));
		} catch (IOException e) {
			throw new UncheckedIOException(e);
		}
	}

	/**
	 * Says every take not on disk yet, and keeps it, so a second run hears the same audio (the
	 * TTS samples noise, so every call says it a little differently). Several at once, a TTS for
	 * each worker: a Kokoro take keeps about one core busy for over a second, however many threads
	 * it is given.
	 */
	private static void speakAll(Tts.Model model, Path cache, List<Take> takes) throws Exception {
		List<Take> missing = takes.stream().filter(t -> !Files.exists(file(cache, t))).distinct().toList();
		if (missing.isEmpty()) {
			return;
		}
		System.out.printf(Locale.ROOT, "%s: saying %d takes, %d at a time (kept in %s)%n", model, missing.size(),
			workers(), cache);
		List<Tts> made = Collections.synchronizedList(new ArrayList<>());
		ThreadLocal<Tts> mine = new ThreadLocal<>();
		AtomicInteger done = new AtomicInteger();
		ExecutorService pool = Executors.newFixedThreadPool(workers());
		try {
			List<Future<Void>> futures = new ArrayList<>();
			for (Take t : missing) {
				futures.add(pool.submit((Callable<Void>) () -> {
					Tts tts = mine.get();
					if (tts == null) {
						tts = new Tts(model, 1);
						mine.set(tts);
						made.add(tts);
					}
					Wav.Audio a = tts.say(t.text(), t.voice(), 1.0f);
					Path f = file(cache, t);
					Path part = f.resolveSibling(f.getFileName() + ".part");
					Wav.write(part, a.samples(), a.rate());
					Files.move(part, f, StandardCopyOption.REPLACE_EXISTING);
					if (done.incrementAndGet() % 100 == 0) {
						System.out.printf(Locale.ROOT, "%s: %d of %d takes%n", model, done.get(), missing.size());
					}
					return null;
				}));
			}
			for (Future<Void> f : futures) {
				f.get();
			}
		} finally {
			pool.shutdown();
			pool.awaitTermination(1, TimeUnit.MINUTES);
			for (Tts t : made) {
				t.close();
			}
		}
	}

	/** The word gaps that may be cut, nearest the middle first; never after a full stop or a question mark. */
	static List<Integer> gapsFromMiddle(List<String> words) {
		List<Integer> gaps = new ArrayList<>();
		for (int k = 1; k < words.size(); k++) {
			if (!words.get(k - 1).matches(".*[.?!]$")) {
				gaps.add(k);
			}
		}
		double middle = words.size() / 2.0;
		gaps.sort((x, y) -> {
			int byDistance = Double.compare(Math.abs(x - middle), Math.abs(y - middle));
			return byDistance != 0 ? byDistance : Integer.compare(y, x);
		});
		return gaps;
	}

	/**
	 * The sample to cut {@code audio} at so that its first half holds the first {@code k} words of
	 * {@code text}, or -1 when there is no clean cut there: the quietest 10 ms just before the
	 * next word, kept only when the halves, transcribed alone, meet at the same place as in the
	 * whole. The first half must be as many words as the whole has before the cut, ending on the
	 * same one, and the second half must start on the next word, give or take a letter: heard
	 * alone, "end" often comes out as "and". A word split by the cut fails one or the other.
	 */
	private static int cutBefore(OfflineRecognizer recognizer, List<String> tries, String what, float[] audio,
			List<Word> said, List<String> text, int k, boolean endsOnFiller) {
		List<String> heard = normalWords(said);
		int b = align(text.stream().map(TurnCompare::normal).toList(), heard)[k];
		if (b <= 0 || b >= heard.size() || heard.size() != said.size()) {
			tries.add(String.join("\t", what, Integer.toString(k), "-", "no", String.join(" ", heard), "-", "-"));
			return -1;
		}
		// Anchored on the next word, so a filler the whole's transcript left out stays in the first half.
		double next = said.get(b).start();
		double from = Math.max(said.get(b - 1).start() + 0.04, next - 0.16);
		int cut = quietest(audio, (int) (from * RATE), (int) ((next + 0.08) * RATE));
		List<String> first = normalWords(transcribe(recognizer, padded(Arrays.copyOf(audio, cut))));
		List<String> second = normalWords(transcribe(recognizer, padded(Arrays.copyOfRange(audio, cut, audio.length))));
		// Parakeet hears a filler as "um", "umam", "a", "ah" or nothing, alone or not, so after one
		// only the word count is checked; otherwise the first half must end on the same word.
		boolean firstOk = endsOnFiller ? Math.abs(first.size() - b) <= 1
			: first.size() == b && first.getLast().equals(heard.get(b - 1));
		boolean clean = firstOk && !second.isEmpty() && oneLetterApart(second.getFirst(), heard.get(b));
		tries.add(String.join("\t", what, Integer.toString(k), String.format(Locale.ROOT, "%.3f", cut / (double) RATE),
			clean ? "yes" : "no", String.join(" ", heard), String.join(" ", first), String.join(" ", second)));
		return clean ? cut : -1;
	}

	/** At most one letter added, dropped or changed: "end" heard alone is often "and". */
	static boolean oneLetterApart(String a, String b) {
		if (Math.abs(a.length() - b.length()) > 1) {
			return false;
		}
		int i = 0;
		while (i < a.length() && i < b.length() && a.charAt(i) == b.charAt(i)) {
			i++;
		}
		int j = 0;
		while (j < a.length() - i && j < b.length() - i
				&& a.charAt(a.length() - 1 - j) == b.charAt(b.length() - 1 - j)) {
			j++;
		}
		return a.length() - i - j <= 1 && b.length() - i - j <= 1;
	}

	/** The middle of the quietest 10 ms in [from, to), in 2.5 ms steps. */
	private static int quietest(float[] a, int from, int to) {
		from = Math.max(0, from);
		to = Math.min(a.length, to);
		int best = (from + to) / 2;
		double lowest = Double.MAX_VALUE;
		for (int f = from; f + 160 <= to; f += 40) {
			double e = 0;
			for (int i = f; i < f + 160; i++) {
				e += a[i] * a[i];
			}
			if (e < lowest) {
				lowest = e;
				best = f + 80;
			}
		}
		return best;
	}

	/** A quarter of a second of quiet floor either side, so Parakeet hears a half's edges as it hears a segment's. */
	private static float[] padded(float[] audio) {
		int pad = RATE / 4;
		float[] out = new float[audio.length + 2 * pad];
		System.arraycopy(audio, 0, out, pad, audio.length);
		addFloor(out, 0, out.length, new Random(audio.length));
		return out;
	}

	/** Parakeet's words, from each one's first token to its last token's end; punctuation joins the word before. */
	private static List<Word> transcribe(OfflineRecognizer recognizer, float[] audio) {
		OfflineStream stream = recognizer.createStream();
		try {
			stream.acceptWaveform(audio, RATE);
			recognizer.decode(stream);
			OfflineRecognizerResult r = recognizer.getResult(stream);
			String[] tokens = r.getTokens();
			float[] starts = r.getTimestamps();
			float[] durations = r.getDurations();
			List<Word> words = new ArrayList<>();
			for (int i = 0; i < tokens.length; i++) {
				String t = tokens[i];
				double end = starts[i] + (durations != null && durations.length > i ? durations[i] : 0.08);
				boolean letters = t.chars().anyMatch(Character::isLetterOrDigit);
				if (words.isEmpty() || (t.startsWith(" ") && letters)) {
					words.add(new Word(t.strip(), starts[i], end));
				} else {
					Word w = words.removeLast();
					words.add(new Word(w.text() + t.strip(), w.start(), letters ? end : w.end()));
				}
			}
			return words;
		} finally {
			stream.release();
		}
	}

	/** Lowercase letters and digits only: "Claude," and "claude" are one word. */
	static String normal(String word) {
		return word.toLowerCase(Locale.ROOT).replaceAll("[^a-z0-9]", "");
	}

	private static List<String> normalWords(List<Word> words) {
		return words.stream().map(w -> normal(w.text())).filter(w -> !w.isEmpty()).toList();
	}

	/**
	 * For each word of {@code ref}, the index of the word of {@code hyp} it lines up with (the same
	 * word, or another heard in its place), or -1 where it was not heard; one more entry, for the
	 * end, holds {@code hyp.size()}. A word-level edit-distance alignment.
	 */
	static int[] align(List<String> ref, List<String> hyp) {
		int n = ref.size();
		int m = hyp.size();
		int[][] d = new int[n + 1][m + 1];
		for (int i = 0; i <= n; i++) {
			d[i][0] = i;
		}
		for (int j = 0; j <= m; j++) {
			d[0][j] = j;
		}
		for (int i = 1; i <= n; i++) {
			for (int j = 1; j <= m; j++) {
				int sub = d[i - 1][j - 1] + (ref.get(i - 1).equals(hyp.get(j - 1)) ? 0 : 1);
				d[i][j] = Math.min(sub, Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1));
			}
		}
		int[] at = new int[n + 1];
		Arrays.fill(at, -1);
		at[n] = m;
		int i = n;
		int j = m;
		while (i > 0 && j > 0) {
			if (d[i][j] == d[i - 1][j - 1] + (ref.get(i - 1).equals(hyp.get(j - 1)) ? 0 : 1)) {
				at[i - 1] = j - 1;
				i--;
				j--;
			} else if (d[i][j] == d[i - 1][j] + 1) {
				i--;
			} else {
				j--;
			}
		}
		return at;
	}

	/**
	 * Whether an ask may be quoted in the report: short, and no capital letter but a sentence's
	 * first, "I" and "Claude" (the AI's name), so that no player's name is in it.
	 */
	private static boolean quotable(List<String> words) {
		if (words.size() > 7) {
			return false;
		}
		boolean sentenceStart = true;
		for (String w : words) {
			String bare = w.replaceAll("[^A-Za-z']", "");
			if (!sentenceStart && !bare.isEmpty() && Character.isUpperCase(bare.charAt(0))
					&& !bare.equals("I") && !bare.startsWith("I'") && !bare.equals("Claude")) {
				return false;
			}
			sentenceStart = w.matches(".*[.?!]$");
		}
		return true;
	}

	// --- the numbers ---

	/**
	 * At {@code threshold}, over the clips {@code which} picks: {right, clips, cut off, unfinished,
	 * kept waiting, finished}.
	 */
	private int[] counts(Run r, double threshold, Predicate<Clip> which) {
		int[] c = new int[6];
		for (int i = 0; i < clips.size(); i++) {
			Clip clip = clips.get(i);
			if (!which.test(clip)) {
				continue;
			}
			boolean saysFinished = r.scores.get(i) >= threshold;
			c[1]++;
			if (saysFinished == clip.finished()) {
				c[0]++;
			}
			if (clip.finished()) {
				c[5]++;
				c[4] += saysFinished ? 0 : 1;
			} else {
				c[3]++;
				c[2] += saysFinished ? 1 : 0;
			}
		}
		return c;
	}

	private static Predicate<Clip> set(String name) {
		return c -> c.set().equals(name);
	}

	private static Predicate<Clip> tagged(String set, String tag) {
		return c -> c.set().equals(set) && c.tags().contains(tag);
	}

	/**
	 * {@code r}'s threshold on {@code set}, to two places: the one at which it cuts off that set's
	 * unfinished clips as often as Smart Turn does at 0.5 on the public set (of two equally close,
	 * the higher).
	 */
	private double pick(Run r, String set) {
		int target = counts(runs.getFirst(), DEFAULT_THRESHOLD, set("public"))[2];
		double best = DEFAULT_THRESHOLD;
		int bestGap = Integer.MAX_VALUE;
		for (int t = 1; t <= 99; t++) {
			int gap = Math.abs(counts(r, t / 100.0, set(set))[2] - target);
			if (gap <= bestGap) {
				bestGap = gap;
				best = t / 100.0;
			}
		}
		return best;
	}

	private void writeScores(Path file) throws IOException {
		StringBuilder b = new StringBuilder("set\tid\tfinished\ttags");
		for (Run r : runs) {
			b.append('\t').append(r.name);
		}
		b.append('\n');
		for (int i = 0; i < clips.size(); i++) {
			Clip c = clips.get(i);
			b.append(c.set()).append('\t').append(c.id()).append('\t').append(c.finished() ? 1 : 0).append('\t')
				.append(String.join(",", c.tags()));
			for (Run r : runs) {
				b.append('\t').append(String.format(Locale.ROOT, "%.4f", r.scores.get(i)));
			}
			b.append('\n');
		}
		Files.writeString(file, b, StandardCharsets.UTF_8);
	}

	// --- the report ---

	private void report(Path out, List<GameSet> games) throws IOException {
		Run smart = runs.get(0);
		Run mini = runs.get(1);
		double picked = pick(mini, "public");
		Object[][] points = {{smart, DEFAULT_THRESHOLD}, {mini, picked}, {mini, DEFAULT_THRESHOLD}};
		long publicClips = clips.stream().filter(set("public")).count();
		long publicFinished = clips.stream().filter(set("public")).filter(Clip::finished).count();
		long human = clips.stream().filter(tagged("public", "human")).count();
		StringBuilder md = new StringBuilder();
		md.append("# Turn model comparison\n\n");
		md.append("Smart Turn v3.2 is the mod's default turn model; turn-1-mini is measured against it. Written by "
			+ "`./gradlew turnCompare` on ").append(LocalDate.now()).append(", on ").append(SttCompare.cpu())
			.append(".\n\n");
		md.append("## Method\n\n");
		md.append(String.format(Locale.ROOT, "Each clip is given to each model as the mod gives it a line at a pause: "
				+ "from %.1f s before the first speech the mod's own VAD finds, to %.1f s after the last, with a quiet "
				+ "-60 dBFS floor wherever the clip has no audio of its own. A score at or above the threshold means "
				+ "finished. **Public set:** the %d English clips (%d finished, %d not; %d of real people, %d TTS) of "
				+ "shard `data/train-00000-of-00010.parquet` of "
				+ "[`pipecat-ai/smart-turn-data-v3.2-test`](https://huggingface.co/datasets/pipecat-ai/smart-turn-data-v3.2-test) "
				+ "(CC-BY-4.0), revision `dfc179241fee65b0efe81ca37786f41ce26f38ec`, prepared by `tools/turn_data.py`. "
				+ "It is Smart Turn's own test set, held out from speech like what it was trained on, so it favours "
				+ "Smart Turn. ",
			BEFORE_SPEECH, AFTER_SPEECH, publicClips, publicFinished, publicClips - publicFinished, human,
			publicClips - human));
		if (games.isEmpty()) {
			md.append("**Game set:** not run; the playtest's asks were not in `build/vfg-test-assets/turn/`.");
		} else {
			GameSet g = games.getFirst();
			md.append(String.format(Locale.ROOT, "**Game set:** the %d direct asks of 4 to 12 words that players said "
					+ "in the 2026-10-08 playtest, as the mod's speech-to-text heard them, spoken by %s TTS (int8, "
					+ "English) in all %d voices: whole (finished); cut at a gap between words near the middle, the "
					+ "first half only (not finished); and said again with \"uh\" or \"um\" in that gap, cut just after "
					+ "the filler (not finished). Gaps come from Parakeet's word timestamps, and every cut is checked by "
					+ "transcribing both halves. The same asks spoken by Kitten, the speech test's TTS, are scored too: "
					+ "Smart Turn hears most of Kitten's finished sentences as unfinished (below), so the game set is "
					+ "Kokoro's.",
				g.asks(), g.tts(), g.voices()));
		}
		md.append(String.format(Locale.ROOT, " turn-1-mini's threshold, **%.2f**, is the one at which it cuts off "
			+ "the public set's unfinished clips as often as Smart Turn does at 0.5. Time per score is wall time on "
			+ "this PC, one score at a time, with both models loaded as the mod loads one.%n%n", picked));

		md.append("## Results\n\n");
		md.append("| Model | Threshold | Set | Clips | Accuracy | Cut off: unfinished scored finished "
			+ "| Kept waiting: finished scored unfinished | Time per score, median / p95 |\n");
		md.append("|---|---|---|---|---|---|---|---|\n");
		List<String[]> sets = new ArrayList<>();
		sets.add(new String[] {"public", "public"});
		for (GameSet g : games) {
			sets.add(new String[] {g.set(), "game, " + g.tts()});
		}
		for (String[] s : sets) {
			for (Object[] p : points) {
				String note = p[0] == mini && (double) p[1] == picked ? " (picked)" : "";
				row(md, (Run) p[0], (double) p[1], s[0], s[1], note);
			}
		}

		if (!games.isEmpty()) {
			md.append("\n## The game sets by form\n\n");
			md.append("| TTS | Model | Threshold | Whole: kept waiting | Cut: cut off | With \"uh\": cut off "
				+ "| With \"um\": cut off |\n|---|---|---|---|---|---|---|\n");
			for (GameSet g : games) {
				for (Object[] p : points) {
					Run r = (Run) p[0];
					double t = (double) p[1];
					md.append(String.format(Locale.ROOT, "| %s | %s | %.2f | %s | %s | %s | %s |%n", g.tts(), r.label,
						t,
						share(counts(r, t, tagged(g.set(), "whole")), 4, 5),
						share(counts(r, t, tagged(g.set(), "cut")), 2, 3),
						share(counts(r, t, tagged(g.set(), "uh")), 2, 3),
						share(counts(r, t, tagged(g.set(), "um")), 2, 3)));
				}
			}
		}

		md.append("\n## Smart Turn around 0.5\n\n");
		String game = games.isEmpty() ? null : games.getFirst().set();
		md.append(game == null ? "| Threshold | Public: cut off | Public: kept waiting |\n|---|---|---|\n"
			: "| Threshold | Public: cut off | Public: kept waiting | Game, " + games.getFirst().tts() + ": cut off "
				+ "| Game, " + games.getFirst().tts() + ": kept waiting |\n"
				+ "|---|---|---|---|---|\n");
		for (double t : SWEEP) {
			int[] p = counts(smart, t, set("public"));
			md.append(String.format(Locale.ROOT, "| %.1f | %s | %s |", t, share(p, 2, 3), share(p, 4, 5)));
			if (game != null) {
				int[] g = counts(smart, t, set(game));
				md.append(String.format(Locale.ROOT, " %s | %s |", share(g, 2, 3), share(g, 4, 5)));
			}
			md.append('\n');
		}

		double later = pick(mini, "later");
		md.append(String.format(Locale.ROOT, "%n## Asked later in the pause: the public set with %.1f s after the "
			+ "speech%n%nAs if the model were asked when the VAD ends the segment, %.1f s into the pause, rather than "
			+ "%.1f s in.%n%n", LATER, LATER, AFTER_SPEECH));
		md.append("| Model | Threshold | Silence after the speech | Accuracy | Cut off | Kept waiting |\n"
			+ "|---|---|---|---|---|---|\n");
		for (Object[] p : new Object[][] {{smart, DEFAULT_THRESHOLD, "public"}, {smart, DEFAULT_THRESHOLD, "later"},
				{mini, picked, "public"}, {mini, picked, "later"}, {mini, later, "later"}}) {
			Run r = (Run) p[0];
			double t = (double) p[1];
			String s = (String) p[2];
			int[] c = counts(r, t, set(s));
			md.append(String.format(Locale.ROOT, "| %s | %.2f%s | %.1f s | %s | %s | %s |%n", r.label, t,
				r == mini && s.equals("later") && t == later && later != picked ? " (picked for 0.6 s)" : "",
				s.equals("later") ? LATER : AFTER_SPEECH, share(c, 0, 1), share(c, 2, 3), share(c, 4, 5)));
		}

		md.append("\n## The public set by kind\n\n");
		md.append("| Model | Threshold | Real people: accuracy | TTS: accuracy | Unfinished, ending on a filler: cut off "
			+ "| As published, not framed: accuracy / cut off / kept waiting |\n|---|---|---|---|---|---|\n");
		for (Object[] p : points) {
			Run r = (Run) p[0];
			double t = (double) p[1];
			int[] asIs = counts(r, t, set("published"));
			md.append(String.format(Locale.ROOT, "| %s | %.2f | %s | %s | %s | %s / %s / %s |%n", r.label, t,
				share(counts(r, t, tagged("public", "human")), 0, 1),
				share(counts(r, t, tagged("public", "tts")), 0, 1),
				share(counts(r, t, tagged("public", "endfiller")), 2, 3), share(asIs, 0, 1), share(asIs, 2, 3),
				share(asIs, 4, 5)));
		}

		md.append("\n## Notes on the data\n\n").append(notes);
		for (GameSet g : games) {
			md.append(String.format(Locale.ROOT, "- **%s cuts:** of %d ask-and-voice pairs, %d were cut at the gap "
					+ "nearest the middle and %d at another gap that both halves confirmed; %d had no clean gap and are "
					+ "in the set whole only. %d filler takes were cut cleanly just after the filler; %d were not and are "
					+ "left out.%n",
				g.tts(), g.pairs(), g.cutMiddle(), g.cutOther(), g.cutNone(), g.fillerMade(), g.fillerFailed()));
		}
		if (!games.isEmpty()) {
			String g = games.getFirst().set();
			md.append(String.format(Locale.ROOT, "- **Cut halves by length:** Smart Turn at 0.5 cuts "
					+ "off %s of %s's cut halves of one or two words, %s of those of three or four, and %s of five or "
					+ "more.%n",
				share(counts(smart, DEFAULT_THRESHOLD, tagged(g, "1-2 words")), 2, 3), games.getFirst().tts(),
				share(counts(smart, DEFAULT_THRESHOLD, tagged(g, "3-4 words")), 2, 3),
				share(counts(smart, DEFAULT_THRESHOLD, tagged(g, "5+ words")), 2, 3)));
		}
		if (!games.isEmpty() && !games.getFirst().examples().isEmpty()) {
			md.append("- **Where some asks were cut**, in Kokoro's first voice: ")
				.append(String.join("; ", games.getFirst().examples().stream().map(e -> "\"" + e + "\"").toList()))
				.append(".\n");
		}
		md.append("- Every clip's scores are in `build/turn-compare/scores.tsv`, and every cut tried, with what "
			+ "Parakeet heard in each half, in `build/turn-compare/cuts.tsv`.\n");
		Files.writeString(out.resolve("report.md"), md, StandardCharsets.UTF_8);
		System.out.println();
		System.out.println(md);
		System.out.println("Report: " + out.resolve("report.md"));
	}

	private void row(StringBuilder md, Run r, double threshold, String set, String label, String note) {
		int[] c = counts(r, threshold, set(set));
		List<Long> ns = new ArrayList<>();
		for (int i = 0; i < clips.size(); i++) {
			if (clips.get(i).set().equals(set)) {
				ns.add(r.nanos.get(i));
			}
		}
		ns.sort(null);
		md.append(String.format(Locale.ROOT, "| %s | %.2f%s | %s | %d | %s | %s | %s | %.1f / %.1f ms |%n", r.label,
			threshold, note, label, c[1], share(c, 0, 1), share(c, 2, 3), share(c, 4, 5), ns.get(ns.size() / 2) / 1e6,
			ns.get((int) Math.min(ns.size() - 1, Math.ceil(ns.size() * 0.95) - 1)) / 1e6));
	}

	/** {@code c[part]} of {@code c[whole]}, as "8.2% (33 of 401)". */
	private static String share(int[] c, int part, int whole) {
		return c[whole] == 0 ? "-"
			: String.format(Locale.ROOT, "%.1f%% (%d of %d)", 100.0 * c[part] / c[whole], c[part], c[whole]);
	}

	private static double median(List<Double> values) {
		if (values.isEmpty()) {
			return Double.NaN;
		}
		List<Double> sorted = new ArrayList<>(values);
		sorted.sort(null);
		return sorted.get(sorted.size() / 2);
	}
}
