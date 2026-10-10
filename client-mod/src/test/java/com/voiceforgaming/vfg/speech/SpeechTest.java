package com.voiceforgaming.vfg.speech;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.k2fsa.sherpa.onnx.OfflineRecognizer;
import com.voiceforgaming.vfg.VoicePipeline;
import com.voiceforgaming.vfg.audio.Resampler;
import com.voiceforgaming.vfg.config.VfgConfig;
import com.voiceforgaming.vfg.audio.WindowListener.StopReason;
import com.voiceforgaming.vfg.testing.PlaybackDevice;
import com.voiceforgaming.vfg.testing.SpeechTimeline;
import com.voiceforgaming.vfg.testing.TestAssets;
import com.voiceforgaming.vfg.testing.Tts;
import com.voiceforgaming.vfg.testing.Wav;
import com.voiceforgaming.vfg.tools.TurnCompare;
import com.voiceforgaming.vfg.turn.TurnDetector;
import com.voiceforgaming.vfg.turn.TurnDetectors;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.atomic.AtomicLong;
import java.util.stream.IntStream;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;

/**
 * Real speech through the mod's real pipeline, in real time: recordings and TTS voices are
 * played into {@link com.voiceforgaming.vfg.audio.MicCapture} through a {@link PlaybackDevice}
 * that delivers samples as a sound card would, at 16 kHz or at 48 kHz (the resampling
 * path), and from there everything is the mod's own: {@link VoicePipeline}, the Silero VAD,
 * the default model (Parakeet TDT 110M), the turn model, the line assembler, the wall clock.
 * It checks how many lines come out, where they split, what they say, and how long after the
 * end of speech each was ready.
 *
 * <ul>
 *   <li>With the mod's defaults (Smart Turn, its own threshold, {@code silence_seconds: 3}),
 *       finished requests spoken by Kokoro become lines within 1 s of their last word, median,
 *       and every line is ready by {@code silence_seconds} + 0.5 s and a tick, whatever Smart
 *       Turn heard. Kokoro, because Smart Turn hears most of Kitten's finished sentences as
 *       unfinished ({@code reports/turn-compare.md}).
 *   <li>Requests spoken whole and split at a gap between words, with 1.5 to 2 s of silence put
 *       in: how many become two lines is reported, not checked; only that every word arrives.
 *   <li>With {@code turn_model: off} and {@code silence_seconds: 5}, the old rule: pauses up to
 *       4.5 s join, 5.5 s and more split, and a line is ready 5 to 7 s after its speech.
 * </ul>
 *
 * Run with {@code ./gradlew speechTest}; the WAVs it played and {@code report.txt} are left in
 * {@code build/speech-test/}.
 */
@Tag("speech")
class SpeechTest {
	private static final double SILENCE_SECONDS = 5.0; // with no turn model: the rule these checks were written for
	/** Silence after each request: more than the silence rule ever waits, so only the turn model closes one sooner. */
	private static final double BETWEEN_REQUESTS = 5.0;
	/** How many finished requests the latency check plays. */
	private static final int FINISHED_REQUESTS = 12;
	private static final int KOKORO_VOICES = 11;
	private static final long T0 = 1_760_000_000_000L;

	/**
	 * Short requests a player might make, each one finished sentence, and words its line must
	 * hold. More than are played: those Smart Turn does not hear finished offline are left out.
	 */
	private static final String[][] REQUESTS = {
		{"Build me a castle by the cave.", "castle cave"},
		{"Make it day.", "day"},
		{"Give everyone diamonds.", "diamonds"},
		{"Spawn a zombie behind the house.", "zombie house"},
		{"Turn off the rain, please.", "rain"},
		{"Who is winning right now?", "winning"},
		{"Put a torch on the pillar.", "torch pillar"},
		{"Craft me a diamond pickaxe.", "pickaxe"},
		{"Set my spawn point at the village.", "village"},
		{"Summon an ender dragon.", "dragon"},
		{"Can you build a bridge over the river?", "bridge river"},
		{"Plant some wheat next to the barn.", "wheat barn"},
		{"Find me some iron.", "iron"},
		{"Light up the mine with lanterns.", "lanterns"},
		{"Give me an enchanted sword.", "sword"},
		{"Tame that wolf for me.", "wolf"},
		{"Fill the moat with water.", "moat water"},
		{"Take me to the nether portal.", "nether portal"},
	};

	/**
	 * Longer requests, each one sentence, for the mid-sentence pauses, and words that must
	 * arrive from both halves.
	 */
	private static final String[][] LONG_REQUESTS = {
		{"Build me a big stone castle right next to the river.", "castle river"},
		{"Can you go and find some diamonds for me down in the cave?", "diamonds cave"},
		{"Put a long row of torches along the wall of the house.", "torches wall house"},
		{"Spawn three zombies behind the church in the village.", "zombies church village"},
		{"Craft me an iron sword and a shield for the fight.", "sword shield fight"},
		{"Plant a whole field of wheat right next to the barn.", "field wheat barn"},
		{"Dig a tunnel straight through the mountain to the east.", "tunnel mountain east"},
		{"Make the sun come up and stop the rain over the farm.", "sun rain farm"},
	};

	/**
	 * Long requests said in one breath, for the VAD's 7 s cap. Said at the right speed, the cap
	 * cuts the last segment at the very end of the speech, so the pause after it, 0.35 s later,
	 * is the only one the turn model gets.
	 */
	private static final String[] RUN_ONS = {
		"Build me a really big castle out of stone with a tall tower on every corner and a moat all the way around it.",
		"Can you go down into the cave and bring me back all the diamonds and gold and iron that you can find down there.",
		"I want you to plant a whole field of wheat and carrots next to the barn and put a fence all the way around it.",
		"Go and find the village up to the north and trade all of our emeralds for books and then come straight back.",
	};
	private static final float[] RUN_ON_SPEEDS = {1.0f, 1.1f, 0.9f, 1.2f, 0.8f};
	/** A segment this long was ended at the VAD's 7 s cap: one ended by its 0.35 s of silence is shorter. */
	private static final double CAPPED_SECONDS = 6.8;

	private static SpeechEngine engine;
	private static Tts tts;
	private static Tts kokoroTts;
	private static Wav.Audio kennedy;
	private static Wav.Audio silero;
	private static Path out;

	@BeforeAll
	static void setUp() throws Exception {
		engine = TestAssets.engine(VfgConfig.DEFAULT_MODEL, 2, 0.5f);
		tts = new Tts();
		kennedy = TestAssets.kennedy();
		silero = Wav.read(TestAssets.fetch(TestAssets.SILERO_TEST_WAV));
		out = Path.of(System.getProperty("vfg.speechOut", "build/speech-test")).toAbsolutePath();
		Files.createDirectories(out);
		Files.deleteIfExists(out.resolve("report.txt"));
	}

	@AfterAll
	static void tearDown() {
		if (tts != null) {
			tts.close();
		}
		if (kokoroTts != null) {
			kokoroTts.close();
		}
		if (engine != null) {
			engine.close();
		}
	}

	private static Wav.Audio cut(Wav.Audio a, double from, double to) {
		return new Wav.Audio(Arrays.copyOfRange(a.samples(), (int) (from * a.rate()), (int) (to * a.rate())), a.rate());
	}

	/** One segment as {@code vfg-stt} transcribed it. */
	record Segment(double seconds, long sttMillis, String text) {}

	/** One line as the mod closed it, the wall-clock moment it was ready to send, and the decisions on its end. */
	record Got(LineAssembler.Line line, long readyAtMillis, List<String> decisions) {}

	record Run(PlaybackDevice device, List<Got> lines, List<Segment> segments) {
		long wall(long sample48k) {
			return device.openedAtMillis() + SpeechTimeline.millisOf(sample48k);
		}
	}

	/** Plays the timeline into the mod's pipeline as it was before the turn model: none, and 5 s of silence. */
	private static Run play(String name, SpeechTimeline timeline, int deviceRate, int expectedLines) throws Exception {
		return play(name, timeline, deviceRate, expectedLines, null, SILENCE_SECONDS);
	}

	/** Plays the timeline into the mod's pipeline in real time and collects what comes out. */
	private static Run play(String name, SpeechTimeline timeline, int deviceRate, int expectedLines, TurnDetector turn,
			double silenceSeconds) throws Exception {
		float[] audio = deviceRate == 48000 ? timeline.at48k() : timeline.at16k();
		Wav.write(out.resolve(name + ".wav"), audio, deviceRate);
		PlaybackDevice device = new PlaybackDevice(audio, deviceRate, timeline.floor(deviceRate));
		List<Got> lines = Collections.synchronizedList(new ArrayList<>());
		List<String> decided = new ArrayList<>(); // since the last line; both events come on vfg-stt
		List<Segment> segments = Collections.synchronizedList(new ArrayList<>());
		Transcriber timed = samples -> {
			long t = System.nanoTime();
			String text = engine.transcribe(samples);
			segments.add(new Segment(samples.length / 16000.0, (System.nanoTime() - t) / 1_000_000, text));
			return text;
		};
		VoicePipeline pipeline = new VoicePipeline(device, () -> engine, () -> timed, () -> turn, null,
			silenceSeconds, System::currentTimeMillis, () -> false, new VoicePipeline.Events() {
				@Override
				public void line(LineAssembler.Line line) {
					lines.add(new Got(line, System.currentTimeMillis(), List.copyOf(decided)));
					decided.clear();
				}

				@Override
				public void decided(String decision) {
					decided.add(decision);
				}
			});
		pipeline.start();
		pipeline.startCapture("", true);
		long limit = System.currentTimeMillis() + (long) (device.recordingSeconds() * 1000) + 15_000;
		long recordingEnd = Long.MAX_VALUE;
		while (System.currentTimeMillis() < limit) {
			if (device.openedAtMillis() > 0) {
				recordingEnd = device.openedAtMillis() + (long) (device.recordingSeconds() * 1000);
			}
			if (lines.size() >= expectedLines && System.currentTimeMillis() > recordingEnd + 500) {
				break;
			}
			Thread.sleep(50);
		}
		pipeline.stopCapture(StopReason.DISCARD);
		pipeline.shutdown(2000);
		return new Run(device, List.copyOf(lines), List.copyOf(segments));
	}

	/**
	 * Checks each line against the clips it should hold: their words are in it, the next
	 * line's are not, it starts and ends where they do, and it was ready 5–7 s after the
	 * end of its speech.
	 */
	private static void check(String name, SpeechTimeline timeline, Run run, List<List<Integer>> groups)
			throws IOException {
		report(name, timeline, run, groups);
		checkLines(timeline, run, groups);
		List<SpeechTimeline.Clip> clips = timeline.clips();
		for (int i = 0; i < groups.size(); i++) {
			long latency = run.lines().get(i).readyAtMillis() - run.wall(clips.get(groups.get(i).getLast()).end());
			assertTrue(latency >= SILENCE_SECONDS * 1000 && latency <= SILENCE_SECONDS * 1000 + 2000,
				"line " + (i + 1) + " ready " + latency + " ms after the end of speech");
		}
	}

	/** Prints the segments and lines of a run, and adds them to {@code report.txt}. */
	private static void report(String name, SpeechTimeline timeline, Run run, List<List<Integer>> groups)
			throws IOException {
		List<SpeechTimeline.Clip> clips = timeline.clips();
		StringBuilder report = new StringBuilder();
		report.append(String.format(Locale.ROOT, "%n== %s (device %d Hz, %.1f s played in real time) ==%n", name,
			run.device().rate(), run.device().recordingSeconds()));
		for (Segment s : run.segments()) {
			report.append(String.format(Locale.ROOT, "  segment %5.2f s  STT %4d ms  \"%s\"%n", s.seconds(), s.sttMillis(),
				s.text()));
		}
		for (int i = 0; i < run.lines().size(); i++) {
			Got g = run.lines().get(i);
			LineAssembler.Line l = g.line();
			report.append(String.format(Locale.ROOT, "  LINE %d (%d segments, peak %.1f, mean %.1f dBFS): \"%s\"%n", i + 1,
				l.segments(), l.peakDbfs(), l.meanDbfs(), l.text()));
			if (i < groups.size()) {
				List<Integer> group = groups.get(i);
				SpeechTimeline.Clip first = clips.get(group.getFirst());
				SpeechTimeline.Clip last = clips.get(group.getLast());
				report.append(String.format(Locale.ROOT,
					"         expected %s%n         started_at %+d ms from the speech, ended_at %+d ms, "
						+ "ready %.2f s after the end of speech (%.2f s after ended_at)%n",
					group.stream().map(c -> clips.get(c).label()).toList(),
					l.startedAtMillis() - run.wall(first.start()), l.endedAtMillis() - run.wall(last.end()),
					(g.readyAtMillis() - run.wall(last.end())) / 1000.0, (g.readyAtMillis() - l.endedAtMillis()) / 1000.0));
			}
			report.append("         decided: ").append(String.join("; ", g.decisions())).append('\n');
		}
		write(report);
	}

	private static void write(CharSequence report) throws IOException {
		System.out.print(report);
		Files.writeString(out.resolve("report.txt"), report, StandardCharsets.UTF_8, StandardOpenOption.CREATE,
			StandardOpenOption.APPEND);
	}

	/**
	 * Checks each line against the clips it should hold: their words are in it, the other
	 * lines' are not, and it starts and ends where they do; and every segment decoded in time.
	 */
	private static void checkLines(SpeechTimeline timeline, Run run, List<List<Integer>> groups) {
		List<SpeechTimeline.Clip> clips = timeline.clips();
		assertEquals(groups.size(), run.lines().size(), "number of lines");
		for (int i = 0; i < groups.size(); i++) {
			Got g = run.lines().get(i);
			LineAssembler.Line l = g.line();
			String text = " " + TestAssets.normalize(l.text()) + " ";
			List<Integer> group = groups.get(i);
			for (int c : group) {
				for (String word : clips.get(c).keywords()) {
					assertTrue(text.contains(" " + word + " "), "line " + (i + 1) + " should say \"" + word + "\": " + l.text());
				}
			}
			for (int j = 0; j < groups.size(); j++) {
				if (j == i) {
					continue;
				}
				for (int c : groups.get(j)) {
					for (String word : clips.get(c).keywords()) {
						assertFalse(text.contains(" " + word + " "), "line " + (i + 1) + " should not say \"" + word + "\": " + l.text());
					}
				}
			}
			assertTrue(l.segments() >= group.size(), "line " + (i + 1) + " segments " + l.segments());
			long startError = l.startedAtMillis() - run.wall(clips.get(group.getFirst()).start());
			long endError = l.endedAtMillis() - run.wall(clips.get(group.getLast()).end());
			assertTrue(Math.abs(startError) <= 600, "line " + (i + 1) + " started_at off by " + startError + " ms");
			assertTrue(Math.abs(endError) <= 800, "line " + (i + 1) + " ended_at off by " + endError + " ms");
			assertTrue(Double.isFinite(l.peakDbfs()) && l.peakDbfs() <= 0 && l.peakDbfs() > -60, "peak " + l.peakDbfs());
			assertTrue(Double.isFinite(l.meanDbfs()) && l.meanDbfs() < l.peakDbfs(), "mean " + l.meanDbfs());
		}
		for (Segment s : run.segments()) {
			assertTrue(s.sttMillis() < 500 * Math.max(1, s.seconds() / 5), "decode took " + s.sttMillis() + " ms");
		}
	}

	@Test
	void minecraftCommandsAndRealSpeechAt16kHz() throws Exception {
		SpeechTimeline t = new SpeechTimeline()
			.silence(1.0)
			.speech("castle (TTS voice 0)", List.of("castle", "cave"), tts.say("Build me a castle by the cave.", 0, 1.0f))
			.silence(1.5) // joins
			.speech("make it day (TTS voice 1, slow)", List.of("make", "day"), tts.say("Make it day.", 1, 0.9f))
			.silence(4.5) // under 5 s: still joins
			.speech("who's winning (TTS voice 5, slow)", List.of("winning"), tts.say("Who's winning?", 5, 0.9f))
			.silence(5.5) // over 5 s: splits
			.speech("Kennedy (recording)", List.of("ask not what your country can do for you"), kennedy)
			.silence(2.0) // joins
			.speech("diamonds (TTS voice 3, fast)", List.of("diamonds"), tts.say("Give everyone diamonds.", 3, 1.15f))
			.silence(6.5) // splits
			.speech("Dublin (recording)", List.of("dublin"), cut(silero, 0.0, 2.3))
			.silence(7.0);
		Run run = play("commands-16k", t, 16000, 3);
		check("pauses of 1.5 and 4.5 s join, 5.5 and 6.5 s split", t, run,
			List.of(List.of(0, 1, 2), List.of(3, 4), List.of(5)));
	}

	@Test
	void longSpeechAndTheResamplingPathAt48kHz() throws Exception {
		SpeechTimeline t = new SpeechTimeline()
			.silence(1.0)
			.speech("zombie (TTS voice 2, slow)", List.of("zombie", "steve"), tts.say("Spawn a zombie behind Steve.", 2, 0.9f))
			.silence(3.0) // joins
			.speech("rain (TTS voice 4, slow)", List.of("rain"), tts.say("Turn off the rain, please.", 4, 0.9f))
			.silence(5.5) // splits
			// 18 s of a lecture with no pause long enough to end a segment: the VAD cuts it into
			// pieces under Moonshine's ~9.3 s limit, and every piece must come back as text.
			.speech("18 s of unbroken lecture (recording)", List.of("theories", "journals", "normative"),
				cut(silero, 20.2, 38.95))
			.silence(7.0);
		Run run = play("long-speech-48k", t, 48000, 2);
		check("a 3 s pause joins, 5.5 s splits, 18 s of speech is transcribed whole", t, run,
			List.of(List.of(0, 1), List.of(2)));
		assertTrue(run.lines().get(1).line().segments() >= 3, "the long speech was cut into pieces");
	}

	// --- with the turn model ---

	/**
	 * Finished requests, each followed by more silence than the silence rule waits, played with
	 * the mod's defaults: Smart Turn at its own threshold and {@code silence_seconds: 3}. Each
	 * must be a line of its own with its words; the median from a request's last word to its
	 * line being ready must be 1 s or less; and every line must be ready by the silence rule's
	 * time after its speech, whatever Smart Turn heard.
	 *
	 * <p>The requests are screened first: each take is put through the mod's own pipeline offline
	 * ({@link #offline}), framed and scored exactly as the mod does it at a pause, and only those
	 * Smart Turn hears finished, and whose words Parakeet hears, are played. That keeps this check
	 * about the pipeline's timing rather than about how a TTS ends a sentence, which is what
	 * {@code ./gradlew turnCompare} measures.
	 */
	@Test
	void finishedRequestsBecomeLinesWithinASecond() throws Exception {
		try (TurnDetector smart = TestAssets.turnDetector(TurnDetectors.SMART_TURN)) {
			StringBuilder screened = new StringBuilder(String.format(Locale.ROOT,
				"%n== finished requests (Kokoro), screened offline with Smart Turn ==%n"));
			SpeechTimeline t = new SpeechTimeline().silence(1.0);
			int kept = 0;
			for (int i = 0; i < REQUESTS.length && kept < FINISHED_REQUESTS; i++) {
				int voice = i % KOKORO_VOICES;
				Wav.Audio take = kokoro(REQUESTS[i][0], voice);
				List<String> words = List.of(REQUESTS[i][1].split(" "));
				Offline o = offline(take, smart);
				String heard = " " + TestAssets.normalize(o.text()) + " ";
				boolean keep = o.finished() && words.stream().allMatch(w -> heard.contains(" " + w + " "));
				screened.append(String.format(Locale.ROOT, "  %-8s voice %2d  %-42s %s  heard \"%s\"%n",
					keep ? "played" : "left out", voice, "\"" + REQUESTS[i][0] + "\"", String.join("; ", o.decisions()),
					o.text()));
				if (keep) {
					t.speech(REQUESTS[i][0] + " (Kokoro voice " + voice + ")", words, take).silence(BETWEEN_REQUESTS);
					kept++;
				}
			}
			write(screened);
			assertTrue(kept >= 10, "only " + kept + " requests were heard finished offline");

			Run run = play("finished-16k", t, 16000, kept, smart, VfgConfig.DEFAULT_SILENCE_SECONDS);
			List<List<Integer>> groups = IntStream.range(0, kept).mapToObj(List::of).toList();
			report("finished requests: Smart Turn, its own threshold, 3 s of silence", t, run, groups);
			checkLines(t, run, groups);
			List<SpeechTimeline.Clip> clips = t.clips();
			long backstop = Math.round(VfgConfig.DEFAULT_SILENCE_SECONDS * 1000) + LineAssembler.GRACE_MILLIS
				+ SttWorker.TICK_MILLIS + 250;
			List<Long> latencies = new ArrayList<>();
			StringBuilder summary = new StringBuilder("  from each request's last word to its line:\n");
			for (int i = 0; i < kept; i++) {
				Got g = run.lines().get(i);
				long latency = g.readyAtMillis() - run.wall(clips.get(i).end());
				latencies.add(latency);
				summary.append(String.format(Locale.ROOT, "    %5.2f s  %-58s %s%n", latency / 1000.0,
					clips.get(i).label(), String.join("; ", g.decisions())));
				assertTrue(latency > 0, "line " + (i + 1) + " was ready before its speech ended");
				long afterEnd = g.readyAtMillis() - g.line().endedAtMillis();
				assertTrue(afterEnd <= backstop, "line " + (i + 1) + " ready " + afterEnd
					+ " ms after its ended_at, later than the silence rule's " + backstop + " ms");
			}
			List<Long> sorted = latencies.stream().sorted().toList();
			double median = (sorted.get((kept - 1) / 2) + sorted.get(kept / 2)) / 2.0;
			summary.append(String.format(Locale.ROOT, "  median %.2f s over %d requests (at most 1.00 s)%n",
				median / 1000.0, kept));
			write(summary);
			assertTrue(median <= 1000, "median " + median + " ms from the last word to the line");
		}
	}

	/**
	 * A request said in one breath for longer than the VAD's 7 s cap, ending on a finished
	 * sentence, played with the mod's defaults: the cap ends its last segment at the end of
	 * the speech, 0.1 s after it rather than 0.35 s, and the turn model is asked at the pause
	 * the capture thread hears 0.35 s after that segment. The line must get a turn decision and
	 * be ready well under the silence rule's 3.5 s: under 2 s after the last word.
	 *
	 * <p>Which takes do that is found offline ({@link #offline}): every run-on, at a few
	 * speeds, until the mod's own pipeline ends one in a segment cut at the cap and Smart Turn
	 * hears it finished. Each is then played on its own, as it was heard offline.
	 */
	@Test
	void aLongRequestEndingAtTheCapIsScoredAtItsEnd() throws Exception {
		try (TurnDetector smart = TestAssets.turnDetector(TurnDetectors.SMART_TURN)) {
			StringBuilder screened = new StringBuilder(String.format(Locale.ROOT,
				"%n== long requests in one breath (Kokoro), screened offline for a last segment cut at the cap ==%n"));
			List<String> labels = new ArrayList<>();
			List<Wav.Audio> takes = new ArrayList<>();
			for (int r = 0; r < RUN_ONS.length && takes.size() < 2; r++) {
				int voice = (3 * r + 2) % KOKORO_VOICES;
				for (float speed : RUN_ON_SPEEDS) {
					Wav.Audio take = kokoro(RUN_ONS[r], voice, speed);
					Offline o = offline(take, smart);
					boolean capped = !o.segmentSeconds().isEmpty() && o.segmentSeconds().getLast() >= CAPPED_SECONDS;
					boolean keep = capped && o.finished();
					screened.append(String.format(Locale.ROOT, "  %-8s voice %2d x%.1f  %5.2f s  segments %s  %s%n",
						keep ? "played" : "left out", voice, speed, take.samples().length / (double) take.rate(),
						o.segmentSeconds().stream().map(x -> String.format(Locale.ROOT, "%.2f", x)).toList(),
						String.join("; ", o.decisions())));
					if (keep) {
						labels.add(String.format(Locale.ROOT, "run-on %d (Kokoro voice %d, x%.1f)", r + 1, voice, speed));
						takes.add(take);
						break;
					}
				}
			}
			write(screened);
			assertFalse(takes.isEmpty(), "no take ended in a segment cut at the cap that Smart Turn heard finished");

			StringBuilder summary = new StringBuilder("  long requests, from the last word to the line:\n");
			for (int i = 0; i < takes.size(); i++) {
				SpeechTimeline t = new SpeechTimeline().silence(1.0).speech(labels.get(i), List.of(), takes.get(i))
					.silence(BETWEEN_REQUESTS);
				Run run = play("capped-" + (i + 1) + "-16k", t, 16000, 1, smart, VfgConfig.DEFAULT_SILENCE_SECONDS);
				report("a long request ending at the cap: Smart Turn, its own threshold, 3 s of silence", t, run,
					List.of(List.of(0)));
				checkLines(t, run, List.of(List.of(0)));
				Got g = run.lines().getFirst();
				long latency = g.readyAtMillis() - run.wall(t.clips().getFirst().end());
				double lastSegment = run.segments().getLast().seconds();
				summary.append(String.format(Locale.ROOT, "    %5.2f s  %s, last segment %.2f s  %s%n", latency / 1000.0,
					labels.get(i), lastSegment, String.join("; ", g.decisions())));
				assertTrue(lastSegment >= CAPPED_SECONDS, "the line's last segment, " + lastSegment
					+ " s, was not cut at the cap as offline");
				assertTrue(g.decisions().stream().anyMatch(d -> d.matches("turn .*: closed")),
					"the turn model closed it: " + g.decisions());
				assertTrue(latency > 0 && latency < 2000, "ready " + latency + " ms after the last word");
			}
			write(summary);
		}
	}

	/** One request spoken whole and split at a gap between words, with a pause put in; what Parakeet heard whole. */
	record Paused(String text, int voice, int before, double gap, String heardWhole) {}

	/**
	 * Requests spoken whole by Kokoro, then split at the clean gap between words nearest the
	 * middle (Parakeet's word times, both halves checked by transcribing them alone, as
	 * {@code turnCompare} cuts), with 1.5 to 2 s of silence put in: the first half keeps the
	 * rise of a sentence that is not over. The halves go in as they were checked, untrimmed,
	 * so the words either side of the cut keep their quiet ends. Played with the mod's defaults. How many become two
	 * lines is reported, not checked: that is Smart Turn's call, and {@code turnCompare}
	 * measures it. Checked: every word arrives, in one line or two, and no line is ready before
	 * the speech it holds has ended. Every word transcribed must be in a line, once and in order;
	 * and the request's own words must arrive, those Parakeet hears in the whole take, but for
	 * the two either side of the cut, which a segment's edge can clip at any pause.
	 */
	@Test
	void pausesMidSentenceAreReported() throws Exception {
		List<Paused> paused = new ArrayList<>();
		StringBuilder notes = new StringBuilder(String.format(Locale.ROOT,
			"%n== pauses mid-sentence (Kokoro), split at a gap between words ==%n"));
		SpeechTimeline t = new SpeechTimeline().silence(1.0);
		OfflineRecognizer parakeet = new OfflineRecognizer(
			SpeechEngine.parakeet(TestAssets.modelDir(VfgConfig.DEFAULT_MODEL), 1));
		try {
			for (int p = 0; p < LONG_REQUESTS.length; p++) {
				String text = LONG_REQUESTS[p][0];
				int voice = (2 * p + 1) % KOKORO_VOICES;
				float[] at48k = SpeechTimeline.to48k(kokoro(text, voice));
				float[] at16k = new Resampler().process(at48k);
				int[] cut = TurnCompare.cutNearMiddle(parakeet, at16k, text);
				if (cut == null) {
					notes.append(String.format(Locale.ROOT, "  voice %2d  \"%s\": no clean gap, left out%n", voice,
						text));
					continue;
				}
				// The words that must arrive: those Parakeet hears in the whole take, but for the two either
				// side of the cut. There the pause starts and ends, and the VAD's segment edges, as at any
				// pause, can clip the quiet end of one word or the start of the next.
				List<String> words = List.of(text.split(" "));
				List<String> edges = List.of(TestAssets.normalize(words.get(cut[1] - 1)),
					TestAssets.normalize(words.get(cut[1])));
				String whole = engine.transcribe(at16k).strip();
				String keywords = String.join(" ", keywordsIn(whole, LONG_REQUESTS[p][1]).stream()
					.filter(w -> !edges.contains(w)).toList());
				String first = String.join(" ", words.subList(0, cut[1]));
				String second = String.join(" ", words.subList(cut[1], words.size()));
				double gap = 1.5 + 0.5 * p / (LONG_REQUESTS.length - 1);
				int at = cut[0] * Resampler.FACTOR;
				t.speechAsIs(first + " ...", keywordsIn(first, keywords), new Wav.Audio(
						Arrays.copyOfRange(at48k, 0, at), SpeechTimeline.RATE))
					.silence(gap)
					.speechAsIs("... " + second, keywordsIn(second, keywords), new Wav.Audio(
						Arrays.copyOfRange(at48k, at, at48k.length), SpeechTimeline.RATE))
					.silence(BETWEEN_REQUESTS);
				paused.add(new Paused(text, voice, cut[1], gap, whole));
			}
		} finally {
			parakeet.release();
		}
		assertTrue(paused.size() >= 6, "only " + paused.size() + " requests had a clean gap to split at");

		Run run;
		try (TurnDetector smart = TestAssets.turnDetector(TurnDetectors.SMART_TURN)) {
			run = play("paused-16k", t, 16000, paused.size(), smart, VfgConfig.DEFAULT_SILENCE_SECONDS);
		}
		report("pauses mid-sentence: Smart Turn, its own threshold, 3 s of silence", t, run, List.of());
		List<SpeechTimeline.Clip> clips = t.clips();
		List<List<Got>> linesOf = new ArrayList<>();
		int split = 0;
		int joined = 0;
		for (int p = 0; p < paused.size(); p++) {
			Paused ps = paused.get(p);
			SpeechTimeline.Clip first = clips.get(2 * p);
			SpeechTimeline.Clip second = clips.get(2 * p + 1);
			long from = run.wall(first.start()) - 1500;
			long to = run.wall(second.end()) + 1500;
			List<Got> mine = run.lines().stream()
				.filter(g -> g.line().startedAtMillis() >= from && g.line().startedAtMillis() <= to).toList();
			linesOf.add(mine);
			if (mine.size() >= 2) {
				split++;
			} else if (mine.size() == 1) {
				joined++;
			}
			List<String> words = List.of(ps.text().split(" "));
			notes.append(String.format(Locale.ROOT, "  %-6s voice %2d, %.2f s after \"%s\" (heard whole: \"%s\")%n",
				mine.size() >= 2 ? "split" : mine.size() == 1 ? "joined" : "LOST", ps.voice(), ps.gap(),
				String.join(" ", words.subList(0, ps.before())), ps.heardWhole()));
			for (Got g : mine) {
				notes.append(String.format(Locale.ROOT, "           \"%s\" (%s)%n", g.line().text(),
					String.join("; ", g.decisions())));
			}
		}
		notes.append(String.format(Locale.ROOT, "  %d of %d split into two lines, %d joined%n", split, paused.size(),
			joined));
		write(notes);

		String transcribed = TestAssets.normalize(String.join(" ", run.segments().stream().map(Segment::text).toList()));
		String inLines = TestAssets.normalize(String.join(" ", run.lines().stream().map(g -> g.line().text()).toList()));
		assertEquals(transcribed, inLines, "every word transcribed went into a line, once and in order");
		assertEquals(run.lines().size(), linesOf.stream().mapToInt(List::size).sum(), "every line belongs to a request");
		for (int p = 0; p < paused.size(); p++) {
			Paused ps = paused.get(p);
			SpeechTimeline.Clip first = clips.get(2 * p);
			SpeechTimeline.Clip second = clips.get(2 * p + 1);
			List<Got> mine = linesOf.get(p);
			assertTrue(mine.size() >= 1 && mine.size() <= 2, "\"" + ps.text() + "\" became " + mine.size() + " lines");
			String all = " " + TestAssets.normalize(String.join(" ", mine.stream().map(g -> g.line().text()).toList()))
				+ " ";
			for (SpeechTimeline.Clip c : List.of(first, second)) {
				for (String w : c.keywords()) {
					assertTrue(all.contains(" " + w + " "), "\"" + w + "\" of \"" + ps.text() + "\" never arrived: "
						+ all);
				}
			}
			for (Got g : mine) {
				SpeechTimeline.Clip last = g.line().endedAtMillis() > run.wall(second.start()) ? second : first;
				assertTrue(g.readyAtMillis() >= run.wall(last.end()), "\"" + g.line().text() + "\" was ready "
					+ (run.wall(last.end()) - g.readyAtMillis()) + " ms before its speech ended");
			}
		}
	}

	/** The words of {@code keywords} (space-separated) that are in {@code text}. */
	private static List<String> keywordsIn(String text, String keywords) {
		String t = " " + TestAssets.normalize(text) + " ";
		return Arrays.stream(keywords.split(" ")).filter(w -> t.contains(" " + w + " ")).toList();
	}

	/** What the mod's pipeline made of one take offline: what it heard, and its decisions on the line's end. */
	record Offline(String text, List<String> decisions, List<Double> segmentSeconds) {
		/** Smart Turn heard it finished at its one pause. */
		boolean finished() {
			return decisions.size() == 1 && decisions.getFirst().matches("turn .*: closed");
		}
	}

	/**
	 * {@code take} through the mod's own capture processor, STT worker and line assembler,
	 * offline: a window at a time, with the clock following the samples. The turn model hears it
	 * framed exactly as the mod frames a line at its pause.
	 */
	private static Offline offline(Wav.Audio take, TurnDetector turn) throws InterruptedException {
		float[] audio = new SpeechTimeline().silence(1.0).speech("", List.of(), take).silence(2.0).at16k();
		for (int i = 0; i < audio.length; i++) {
			audio[i] = Wav.toShort(audio[i]) / 32768f; // 16-bit, as the playback device delivers it
		}
		AtomicLong now = new AtomicLong(T0);
		List<String> heard = new ArrayList<>();
		List<String> decisions = new ArrayList<>();
		List<Double> segmentSeconds = new ArrayList<>();
		LineAssembler assembler = new LineAssembler(VfgConfig.DEFAULT_SILENCE_SECONDS, now::get, line -> {},
			decisions::add);
		Transcriber measured = samples -> {
			segmentSeconds.add(samples.length / 16000.0);
			return engine.transcribe(samples);
		};
		CaptureProcessor[] cp = new CaptureProcessor[1];
		SttWorker stt = new SttWorker(() -> measured, () -> turn, null, assembler, () -> cp[0].speaking(), heard::add,
			() -> false);
		cp[0] = new CaptureProcessor(() -> engine, () -> true, stt::submit);
		cp[0].onStarted(true);
		for (int off = 0; off + SpeechEngine.WINDOW <= audio.length; off += SpeechEngine.WINDOW) {
			now.set(T0 + (off + SpeechEngine.WINDOW) / 16);
			cp[0].onWindow(Arrays.copyOfRange(audio, off, off + SpeechEngine.WINDOW), now.get());
			stt.drainNow();
		}
		cp[0].onStopped(StopReason.DISCARD);
		stt.drainNow();
		return new Offline(String.join(" ", heard), decisions, segmentSeconds);
	}

	/**
	 * {@code text} said by Kokoro in {@code voice}, kept on disk the first time: a Kokoro take
	 * takes a second or more, and every later run hears the same one.
	 */
	private static Wav.Audio kokoro(String text, int voice) throws Exception {
		return kokoro(text, voice, 1.0f);
	}

	/** {@code text} said by Kokoro in {@code voice} at {@code speed} (1 is normal), kept on disk the first time. */
	private static Wav.Audio kokoro(String text, int voice, float speed) throws Exception {
		String name = "kokoro-v" + voice + (speed == 1.0f ? "" : String.format(Locale.ROOT, "-x%.2f", speed)) + "-"
			+ TestAssets.normalize(text).replace(' ', '-');
		Path file = TestAssets.root().resolve("speech-test-tts").resolve(name + ".wav");
		if (!Files.exists(file)) {
			if (kokoroTts == null) {
				kokoroTts = new Tts(Tts.Model.KOKORO, 2);
			}
			Wav.Audio a = kokoroTts.say(text, voice, speed);
			Files.createDirectories(file.getParent());
			Path part = file.resolveSibling(file.getFileName() + ".part");
			Wav.write(part, a.samples(), a.rate());
			Files.move(part, file, StandardCopyOption.REPLACE_EXISTING);
		}
		return Wav.read(file);
	}
}
