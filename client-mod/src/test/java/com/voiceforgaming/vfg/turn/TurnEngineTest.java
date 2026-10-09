package com.voiceforgaming.vfg.turn;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.voiceforgaming.vfg.config.VfgConfig;
import com.voiceforgaming.vfg.setup.Assets;
import com.voiceforgaming.vfg.speech.SpeechEngine;
import com.voiceforgaming.vfg.testing.TestAssets;
import com.voiceforgaming.vfg.testing.Wav;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;

/**
 * Both turn models, installed through the mod's own installer and loaded after the speech
 * engine on the ONNX Runtime it loaded, as the mod does, scoring real speech: sentences from
 * sherpa-onnx's VAD test lecture and Parakeet's test clip, each followed by the 0.2 s of
 * silence the mod passes, either whole or cut off between two words. Run with
 * {@code ./gradlew engineTest}.
 *
 * <p>Kitten TTS is not used: Smart Turn scored 36 of 40 finished Kitten phrases (five
 * phrases in all eight voices) below 0.5, most of them below 0.05.
 */
@Tag("engine")
class TurnEngineTest {
	private static final int RATE = TurnDetector.SAMPLE_RATE;
	private static final double TAIL_SECONDS = 0.2;

	/** What is said, and the audio a score is given. */
	private record Clip(String words, float[] audio) {}

	private static SpeechEngine engine;
	private static TurnDetector smartTurn;
	private static TurnDetector turnOneMini;
	private static float[] lecture;
	private static float[] portrait;

	// Finished.
	private static Clip wealthTax;
	private static Clip whoWhole;
	private static Clip goodLuck;
	// Cut off between two words.
	private static Clip whoCut;
	private static Clip literatureCut;
	private static Clip portraitCut;

	@BeforeAll
	static void load() throws Exception {
		engine = TestAssets.engine(VfgConfig.DEFAULT_MODEL, 2, 0.5f);
		long t0 = System.nanoTime();
		smartTurn = TestAssets.turnDetector(TurnDetectors.SMART_TURN);
		long t1 = System.nanoTime();
		turnOneMini = TestAssets.turnDetector(TurnDetectors.TURN_1_MINI);
		long t2 = System.nanoTime();
		System.out.printf(Locale.ROOT, "ONNX Runtime %s; loaded %s in %.0f ms, %s in %.0f ms%n",
			OnnxRuntime.open(TestAssets.nativesDir()).version(), smartTurn.name(), (t1 - t0) / 1e6, turnOneMini.name(),
			(t2 - t1) / 1e6);

		Wav.Audio vadTest = Wav.read(TestAssets.fetch(TestAssets.SILERO_TEST_WAV));
		assertEquals(RATE, vadTest.rate(), "the VAD test recording is 16 kHz");
		lecture = vadTest.samples();
		Wav.Audio parakeetClip = Wav.read(TestAssets.modelDir(VfgConfig.DEFAULT_MODEL).resolve(Assets.MODEL_TEST_WAV));
		assertEquals(RATE, parakeetClip.rate(), "Parakeet's test clip is 16 kHz");
		portrait = parakeetClip.samples();

		wealthTax = clip(lecture, 35.65, 46.70,
			"Thomas Piketty, in his book Capital in the Twenty-First Century, argues for a four percent global wealth tax.");
		goodLuck = clip(lecture, 48.37, 50.04, "Well, good luck with that.");
		whoWhole = clip(lecture, 50.61, 54.09, "Who's going to implement a four percent global wealth tax?");
		whoCut = clip(lecture, 50.61, 53.42, "Who's going to implement a four percent global");
		literatureCut = clip(lecture, 20.20, 26.27,
			"where we actually are, and how the theories that people argue about in the journals and in the literature");
		portraitCut = clip(portrait, 0, 6.55,
			"Well, I don't wish to see it any more, observed Phoebe, turning away her eyes. It is certainly very like the old");
	}

	@AfterAll
	static void release() {
		for (AutoCloseable c : new AutoCloseable[] {smartTurn, turnOneMini, engine}) {
			try {
				if (c != null) {
					c.close();
				}
			} catch (Exception ignored) {
				// the next one is still released
			}
		}
	}

	/** {@code from} to {@code to} seconds of a 16 kHz recording, then 0.2 s of silence. */
	private static Clip clip(float[] recording, double from, double to, String words) {
		int a = (int) Math.round(from * RATE);
		int b = (int) Math.round(to * RATE);
		float[] audio = new float[b - a + (int) Math.round(TAIL_SECONDS * RATE)];
		System.arraycopy(recording, a, audio, 0, b - a);
		return new Clip(words, audio);
	}

	private static float score(TurnDetector d, Clip c) {
		float p = d.score(c.audio());
		System.out.printf(Locale.ROOT, "  %-16s %.3f  %5.2f s  \"%s\"%n", d.name(), p, c.audio().length / (double) RATE,
			c.words());
		return p;
	}

	private static void assertFinished(TurnDetector d, Clip c) {
		float p = score(d, c);
		assertTrue(p >= d.defaultThreshold(), d.name() + " scored a finished sentence " + p + ": " + c.words());
	}

	private static void assertCut(TurnDetector d, Clip c) {
		float p = score(d, c);
		assertTrue(p < d.defaultThreshold(), d.name() + " scored a cut-off sentence " + p + ": " + c.words());
	}

	@Test
	void smartTurnHearsFinishedAndCutOffSpeech() {
		assertEquals(TurnDetectors.SMART_TURN, smartTurn.name());
		assertEquals(0.5, smartTurn.defaultThreshold());
		assertFinished(smartTurn, whoWhole);
		assertCut(smartTurn, whoCut); // the same question, before "wealth tax?"
		assertFinished(smartTurn, wealthTax);
		assertCut(smartTurn, literatureCut);
		assertCut(smartTurn, portraitCut);
	}

	@Test
	void turnOneMiniHearsFinishedAndCutOffSpeech() {
		assertEquals(TurnDetectors.TURN_1_MINI, turnOneMini.name());
		assertEquals(TurnOneMini.THRESHOLD, turnOneMini.defaultThreshold());
		assertFinished(turnOneMini, goodLuck);
		assertFinished(turnOneMini, wealthTax);
		assertCut(turnOneMini, whoCut);
		assertCut(turnOneMini, literatureCut);
		assertCut(turnOneMini, portraitCut);
	}

	@Test
	void aScoreDependsOnTheClipAloneAndShortClipsWork() {
		for (TurnDetector d : List.of(smartTurn, turnOneMini)) {
			float first = d.score(whoCut.audio());
			d.score(wealthTax.audio());
			assertEquals(first, d.score(whoCut.audio()), 0f, d.name() + ": the same clip scores the same after another");
			for (int samples : new int[] {0, 1, 2559, 2560, 2561, SmartTurn.WINDOW + 1}) {
				float p = d.score(Arrays.copyOf(goodLuck.audio(), samples));
				assertTrue(p >= 0 && p <= 1, d.name() + ": " + p + " for " + samples + " samples");
			}
		}
	}

	@Test
	void medianTimePerScore() {
		for (TurnDetector d : List.of(smartTurn, turnOneMini)) {
			long[] nanos = new long[30];
			for (int i = 0; i < nanos.length; i++) {
				long t = System.nanoTime();
				d.score(wealthTax.audio());
				nanos[i] = System.nanoTime() - t;
			}
			Arrays.sort(nanos);
			double median = nanos[nanos.length / 2] / 1e6;
			System.out.printf(Locale.ROOT, "  %-16s median %.1f ms per score (fastest %.1f, slowest %.1f) of %.2f s%n", d.name(),
				median, nanos[0] / 1e6, nanos[nanos.length - 1] / 1e6, wealthTax.audio().length / (double) RATE);
			assertTrue(median < 100, d.name() + ": median " + median + " ms per score");
		}
	}

	/** A player switching speech models closes the engine and loads another; the turn model stays. */
	@Test
	void stillScoresAfterTheSpeechEngineIsReplaced() throws Exception {
		float smartBefore = smartTurn.score(whoWhole.audio());
		float miniBefore = turnOneMini.score(goodLuck.audio());
		engine.close();
		engine = null;
		engine = TestAssets.engine(VfgConfig.MODEL_TINY, 2, 0.5f);
		assertEquals(smartBefore, smartTurn.score(whoWhole.audio()), 0f);
		assertEquals(miniBefore, turnOneMini.score(goodLuck.audio()), 0f);
		String text = TestAssets.normalize(engine.transcribe(Arrays.copyOf(whoWhole.audio(), whoWhole.audio().length + RATE)));
		System.out.println("  " + VfgConfig.MODEL_TINY + " after the switch: \"" + text + "\"");
		assertTrue(text.contains("implement") && text.contains("global"), "the new engine works too: " + text);
	}

	@Test
	void aClosedDetectorRefusesToScore() throws Exception {
		for (String model : List.of(TurnDetectors.SMART_TURN, TurnDetectors.TURN_1_MINI)) {
			TurnDetector d = TestAssets.turnDetector(model);
			d.score(whoCut.audio());
			d.close();
			assertThrows(IllegalStateException.class, () -> d.score(whoCut.audio()), model);
			d.close();
		}
	}

	@Test
	void aFileThatIsNotTheModelFailsAtLoad() throws Exception {
		Path vad = Assets.folder(TestAssets.root(), Assets.vad()).resolve(Assets.VAD_FILE);
		for (String model : List.of(TurnDetectors.SMART_TURN, TurnDetectors.TURN_1_MINI)) {
			OnnxRuntime.OrtException e = assertThrows(OnnxRuntime.OrtException.class,
				() -> TurnDetectors.load(model, TestAssets.nativesDir(), vad));
			System.out.println("  " + model + " on the VAD's file: " + e.getMessage());
		}
	}
}
