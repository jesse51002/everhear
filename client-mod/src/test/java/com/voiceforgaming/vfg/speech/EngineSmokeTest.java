package com.voiceforgaming.vfg.speech;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.voiceforgaming.vfg.audio.Resampler;
import com.voiceforgaming.vfg.audio.WindowListener.StopReason;
import com.voiceforgaming.vfg.config.VfgConfig;
import com.voiceforgaming.vfg.testing.SpeechTimeline;
import com.voiceforgaming.vfg.testing.TestAssets;
import com.voiceforgaming.vfg.testing.Wav;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.atomic.AtomicLong;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;

/**
 * The real natives, Silero VAD and the default model (Parakeet TDT 0.6B v2, with the hotwords, as
 * the mod runs it), installed into {@code build/vfg-test-assets/} once through the mod's own installer, pushed
 * through {@link CaptureProcessor}, {@link SttWorker} and {@link LineAssembler} with the
 * clock driven by the sample count. Run with {@code ./gradlew engineTest}.
 */
@Tag("engine")
class EngineSmokeTest {
	private static final String PHRASE = "ask not what your country can do for you";
	private static final long T0 = 1_760_000_000_000L;

	private static SpeechEngine engine;
	private static float[] speech;

	@BeforeAll
	static void install() throws Exception {
		long t0 = System.nanoTime();
		engine = TestAssets.engine(VfgConfig.DEFAULT_MODEL, 2, 0.5f);
		System.out.printf(Locale.ROOT, "%s installed and loaded in %.2f s%n", VfgConfig.DEFAULT_MODEL,
			(System.nanoTime() - t0) / 1e9);
		Wav.Audio clip = TestAssets.kennedy();
		assertEquals(24000, clip.rate(), "the model's test clip is 24 kHz");
		speech = to16k(clip.samples());
		System.out.printf(Locale.ROOT, "test clip: %.2f s at 16 kHz%n", speech.length / 16000.0);
	}

	@AfterAll
	static void release() {
		if (engine != null) {
			engine.close();
		}
	}

	/** One run of the pipeline over a list of audio pieces; collects lines and decode times. */
	private static final class Run {
		final AtomicLong now = new AtomicLong(T0);
		final List<LineAssembler.Line> lines = new ArrayList<>();
		final List<double[]> decodes = new ArrayList<>(); // {seconds of audio, seconds to decode}
		final SttWorker stt;
		final CaptureProcessor capture;
		long samples;

		Run() {
			LineAssembler assembler = new LineAssembler(5, now::get, lines::add);
			Transcriber timed = s -> {
				long t = System.nanoTime();
				String text = engine.transcribe(s);
				decodes.add(new double[] {s.length / 16000.0, (System.nanoTime() - t) / 1e9});
				System.out.printf(Locale.ROOT, "  segment %.2f s -> \"%s\"%n", s.length / 16000.0, text);
				return text;
			};
			CaptureProcessor[] cp = new CaptureProcessor[1];
			stt = new SttWorker(() -> timed, () -> null, null, assembler, () -> cp[0].speaking(), t -> {}, () -> false);
			cp[0] = new CaptureProcessor(() -> engine, stt::submit);
			capture = cp[0];
			capture.onStarted(true);
		}

		void feed(float[] audio) throws InterruptedException {
			for (int off = 0; off + 512 <= audio.length; off += 512) {
				float[] w = new float[512];
				System.arraycopy(audio, off, w, 0, 512);
				samples += 512;
				now.set(T0 + samples * 1000 / 16000); // the clock follows the sample count
				capture.onWindow(w, now.get());
				stt.drainNow();
				stt.step(0);
			}
		}

		void silence(double seconds) throws InterruptedException {
			feed(new float[(int) Math.round(seconds * 16000 / 512) * 512]);
		}

		void stop() throws InterruptedException {
			capture.onStopped(StopReason.LEAVE);
			stt.drainNow();
		}
	}

	private static String normalize(String s) {
		return s.toLowerCase(Locale.ROOT).replaceAll("[^a-z0-9 ]", " ").replaceAll("\\s+", " ").trim();
	}

	private static int count(String haystack, String needle) {
		int n = 0;
		for (int i = haystack.indexOf(needle); i >= 0; i = haystack.indexOf(needle, i + 1)) {
			n++;
		}
		return n;
	}

	private static void assertFastEnough(Run run) {
		for (double[] d : run.decodes) {
			double per5s = d[1] / (d[0] / 5.0);
			System.out.printf(Locale.ROOT, "  decode %.3f s for %.2f s of speech (%.3f s per 5 s)%n", d[1], d[0], per5s);
			assertTrue(per5s < 0.5, "decode took " + per5s + " s per 5 s of speech");
		}
	}

	@Test
	void speechTwoSecondsSpeechSixSecondsMakesOneLine() throws Exception {
		Run run = new Run();
		run.silence(0.5);
		run.feed(speech);
		run.silence(2);
		run.feed(speech);
		run.silence(6);
		assertEquals(1, run.lines.size(), "lines: " + run.lines);
		LineAssembler.Line line = run.lines.get(0);
		System.out.println("line: " + line);
		assertEquals(2, count(normalize(line.text()), PHRASE), "text: " + line.text());
		assertTrue(Double.isFinite(line.peakDbfs()) && line.peakDbfs() > -100 && line.peakDbfs() <= 0);
		assertTrue(Double.isFinite(line.meanDbfs()) && line.meanDbfs() > -100 && line.meanDbfs() <= 0);
		double seconds = (line.endedAtMillis() - line.startedAtMillis()) / 1000.0;
		assertTrue(seconds >= 8 && seconds <= 11, "ended_at - started_at = " + seconds);
		assertFastEnough(run);
		run.stop();
		assertEquals(1, run.lines.size());
	}

	@Test
	void speechSixSecondsSpeechSixSecondsMakesTwoLines() throws Exception {
		Run run = new Run();
		run.silence(0.5);
		run.feed(speech);
		run.silence(6);
		run.feed(speech);
		run.silence(6);
		assertEquals(2, run.lines.size(), "lines: " + run.lines);
		for (LineAssembler.Line line : run.lines) {
			assertEquals(1, count(normalize(line.text()), PHRASE), "text: " + line.text());
		}
		assertFastEnough(run);
	}

	@Test
	void leavingMidSentenceStillDeliversIt() throws Exception {
		Run run = new Run();
		run.silence(0.5);
		run.feed(speech);
		assertTrue(run.lines.isEmpty());
		run.stop();
		assertEquals(1, run.lines.size(), "the open line closes at once on leaving");
		assertEquals(1, count(normalize(run.lines.get(0).text()), PHRASE), run.lines.get(0).text());
	}

	/** Every model a player can pick loads through the mod's own loader and hears the clip. */
	@Test
	void everySelectableModelLoadsAndTranscribes() throws Exception {
		for (String model : VfgConfig.MODELS) {
			long t0 = System.nanoTime();
			try (SpeechEngine e = model.equals(VfgConfig.DEFAULT_MODEL) ? null : TestAssets.engine(model, 2, 0.5f)) {
				SpeechEngine use = e == null ? engine : e;
				String text = use.transcribe(speech);
				System.out.printf(Locale.ROOT, "  %-18s %.2f s -> \"%s\"%n", model, (System.nanoTime() - t0) / 1e9, text);
				assertEquals(1, count(normalize(text), PHRASE), model + ": " + text);
			}
		}
	}

	/** With hotwords (beam search over the pieces' code names) a Parakeet model hears what it hears greedily. */
	@Test
	void hotwordsDecodeTheClipAsGreedyDoesAndANameComesThrough() throws Exception {
		assertTrue(engine.hotwords(), "the default model decodes with hotwords");
		for (String model : List.of(VfgConfig.MODEL_PARAKEET, VfgConfig.MODEL_PARAKEET_110M)) {
			Hotwords h = new Hotwords();
			h.players(List.of("SteveJM"));
			h.phrases(List.of("fire ball"), null);
			try (SpeechEngine greedy = TestAssets.engine(model, 2, 0.5f, null);
					SpeechEngine hot = TestAssets.engine(model, 2, 0.5f, h)) {
				assertTrue(hot.hotwords());
				String g = greedy.transcribe(speech);
				String t = hot.transcribe(speech);
				System.out.printf(Locale.ROOT, "  %-20s greedy \"%s\", hotwords \"%s\"%n", model, g, t);
				assertEquals(normalize(g), normalize(t), model);
			}
		}
	}

	/** The 0.6B is swapped into a running engine whose 110M listened while it downloaded; the VAD stays. */
	@Test
	void aBiggerModelIsSwappedIntoARunningEngine() throws Exception {
		Hotwords h = new Hotwords();
		try (SpeechEngine e = TestAssets.engine(VfgConfig.MODEL_PARAKEET_110M, 2, 0.5f, h)) {
			String before = e.transcribe(speech);
			assertTrue(e.replaceRecognizer(SpeechEngine.recognition(TestAssets.modelDir(VfgConfig.MODEL_PARAKEET), true, 2,
				true)));
			String after = e.transcribe(speech);
			System.out.printf(Locale.ROOT, "  110M \"%s\", then 0.6B \"%s\"%n", before, after);
			assertEquals(1, count(normalize(after), PHRASE), after);
			assertTrue(e.hotwords());
			e.close();
			assertTrue(!e.replaceRecognizer(SpeechEngine.recognition(TestAssets.modelDir(VfgConfig.MODEL_PARAKEET_110M),
				true, 2, false)), "a released engine takes no recognizer");
		}
	}

	@Test
	void aSegmentLongerThanMoonshineTakesIsDecodedInPieces() {
		// Moonshine returns "" for about 9.3 s or more in one go; the engine splits such segments.
		float[] three = new float[speech.length * 3];
		for (int i = 0; i < 3; i++) {
			System.arraycopy(speech, 0, three, i * speech.length, speech.length);
		}
		assertTrue(three.length / 16000.0 > SpeechEngine.MAX_DECODE_SECONDS, "test clip should be long");
		String text = engine.transcribe(three);
		System.out.printf(Locale.ROOT, "  %.2f s in one segment -> \"%s\"%n", three.length / 16000.0, text);
		assertTrue(count(normalize(text), "country") >= 4, "text: " + text);
	}

	// --- audio helpers ---

	/** 24 kHz to 16 kHz: to 48 kHz by interpolation, then the mod's own Resampler. */
	private static float[] to16k(float[] at24k) {
		return new Resampler().process(SpeechTimeline.to48k(new Wav.Audio(at24k, 24000)));
	}
}
