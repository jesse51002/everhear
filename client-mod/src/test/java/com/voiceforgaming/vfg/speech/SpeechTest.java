package com.voiceforgaming.vfg.speech;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.voiceforgaming.vfg.VoicePipeline;
import com.voiceforgaming.vfg.config.VfgConfig;
import com.voiceforgaming.vfg.audio.WindowListener.StopReason;
import com.voiceforgaming.vfg.testing.PlaybackDevice;
import com.voiceforgaming.vfg.testing.SpeechTimeline;
import com.voiceforgaming.vfg.testing.TestAssets;
import com.voiceforgaming.vfg.testing.Tts;
import com.voiceforgaming.vfg.testing.Wav;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.Locale;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;

/**
 * Real speech through the mod's real pipeline, in real time: recordings and TTS voices are
 * played into {@link com.voiceforgaming.vfg.audio.MicCapture} through a {@link PlaybackDevice}
 * that delivers samples as a sound card would, at 16 kHz or at 48 kHz (the resampling
 * path), and from there everything is the mod's own: {@link VoicePipeline}, the Silero VAD,
 * the default model (Parakeet TDT 110M), the line assembler, the wall clock. It checks how many lines come out,
 * where they split, what they say, and how long after the end of speech each was ready.
 * Run with {@code ./gradlew speechTest}; the WAVs it played are left in {@code build/speech-test/}.
 */
@Tag("speech")
class SpeechTest {
	private static final double SILENCE_SECONDS = 5.0;

	private static SpeechEngine engine;
	private static Tts tts;
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
		if (engine != null) {
			engine.close();
		}
	}

	private static Wav.Audio cut(Wav.Audio a, double from, double to) {
		return new Wav.Audio(Arrays.copyOfRange(a.samples(), (int) (from * a.rate()), (int) (to * a.rate())), a.rate());
	}

	/** One segment as {@code vfg-stt} transcribed it. */
	record Segment(double seconds, long sttMillis, String text) {}

	/** One line as the mod closed it, and the wall-clock moment it was ready to send. */
	record Got(LineAssembler.Line line, long readyAtMillis) {}

	record Run(PlaybackDevice device, List<Got> lines, List<Segment> segments) {
		long wall(long sample48k) {
			return device.openedAtMillis() + SpeechTimeline.millisOf(sample48k);
		}
	}

	/** Plays the timeline into the mod's pipeline in real time and collects what comes out. */
	private static Run play(String name, SpeechTimeline timeline, int deviceRate, int expectedLines) throws Exception {
		float[] audio = deviceRate == 48000 ? timeline.at48k() : timeline.at16k();
		Wav.write(out.resolve(name + ".wav"), audio, deviceRate);
		PlaybackDevice device = new PlaybackDevice(audio, deviceRate, timeline.floor(deviceRate));
		List<Got> lines = Collections.synchronizedList(new ArrayList<>());
		List<Segment> segments = Collections.synchronizedList(new ArrayList<>());
		Transcriber timed = samples -> {
			long t = System.nanoTime();
			String text = engine.transcribe(samples);
			segments.add(new Segment(samples.length / 16000.0, (System.nanoTime() - t) / 1_000_000, text));
			return text;
		};
		VoicePipeline pipeline = new VoicePipeline(device, () -> engine, () -> timed, SILENCE_SECONDS,
			System::currentTimeMillis, () -> false, new VoicePipeline.Events() {
				@Override
				public void line(LineAssembler.Line line) {
					lines.add(new Got(line, System.currentTimeMillis()));
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
		}
		System.out.print(report);
		Files.writeString(out.resolve("report.txt"), report, StandardCharsets.UTF_8, StandardOpenOption.CREATE,
			StandardOpenOption.APPEND);

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
			long latency = g.readyAtMillis() - run.wall(clips.get(group.getLast()).end());
			assertTrue(latency >= SILENCE_SECONDS * 1000 && latency <= SILENCE_SECONDS * 1000 + 2000,
				"line " + (i + 1) + " ready " + latency + " ms after the end of speech");
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
}
