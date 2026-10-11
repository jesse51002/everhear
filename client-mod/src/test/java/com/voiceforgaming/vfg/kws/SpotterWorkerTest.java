package com.voiceforgaming.vfg.kws;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.voiceforgaming.vfg.audio.WindowListener.StopReason;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/**
 * The spotter thread's logic against a stand-in model whose streams decode a chunk per 320 ms of
 * audio (ten windows) and hit where a test says, driven window by window on the test's thread.
 */
class SpotterWorkerTest {
	private static final long T0 = 1_760_000_000_000L;
	private static final Incantation FIRE_BALL = new Incantation("fire ball", List.of("fireball"), 3);

	/** A stream that is ready for a chunk per 5120 samples and hits on the decodes a test scripts. */
	static final class FakeStream implements KeywordModel.Stream {
		final Map<Long, KeywordModel.Hit> script;
		long samples;
		long decoded;
		int resets;
		boolean closed;
		final List<Float> firstSamples = new ArrayList<>();
		private KeywordModel.Hit pending;

		FakeStream(Map<Long, KeywordModel.Hit> script) {
			this.script = script;
		}

		@Override
		public void accept(float[] s) {
			samples += s.length;
			firstSamples.add(s[0]);
		}

		@Override
		public boolean ready() {
			return samples / 5120 > decoded;
		}

		@Override
		public void decode() {
			decoded++;
			pending = script.get(decoded);
		}

		@Override
		public KeywordModel.Hit hit() {
			KeywordModel.Hit h = pending;
			pending = null;
			return h;
		}

		@Override
		public void reset() {
			resets++;
		}

		@Override
		public void close() {
			closed = true;
		}
	}

	static final class FakeModel implements KeywordModel {
		final List<String> opened = new ArrayList<>();
		final List<FakeStream> streams = new ArrayList<>();
		final Map<Long, Hit> script = new HashMap<>();
		boolean closed;

		@Override
		public Stream open(String keywords) {
			opened.add(keywords);
			FakeStream s = new FakeStream(script);
			streams.add(s);
			return s;
		}

		@Override
		public SentencePiece pieces() {
			try {
				return SentencePieceTest.model();
			} catch (Exception e) {
				throw new IllegalStateException(e);
			}
		}

		@Override
		public Set<String> tokens() {
			return Set.of("▁FI", "RE", "▁BA", "LL", "B", "AL", "L", "▁I", "CE", "▁WA");
		}

		@Override
		public int chunkFrames() {
			return 8;
		}

		@Override
		public int trailingBlanks() {
			return 1;
		}

		@Override
		public void close() {
			closed = true;
		}

		FakeStream last() {
			return streams.getLast();
		}
	}

	private FakeModel model;
	private List<SpotterWorker.Cast> casts;
	private SpotterWorker worker;
	private int windows;

	@BeforeEach
	void setUp() {
		model = new FakeModel();
		casts = new ArrayList<>();
		worker = new SpotterWorker(true, 0.25, 1.5, casts::add);
		worker.setModel(model);
		worker.setIncantations(List.of(FIRE_BALL));
		worker.onStarted(true);
	}

	/** The next window, read at T0 + 32 ms times its number, on the test's thread; then the worker has it. */
	private void window(boolean speaking) {
		windows++;
		float[] w = new float[512];
		w[0] = windows;
		worker.onWindow(w, T0 + windows * 32L, speaking);
		worker.drainNow();
	}

	private void windows(int n, boolean speaking) {
		for (int i = 0; i < n; i++) {
			window(speaking);
		}
	}

	@Test
	void theCaptureThreadNeverBlocksWhenTheQueueIsFull() {
		long t0 = System.nanoTime();
		for (int i = 0; i < 10_000; i++) {
			worker.onWindow(new float[512], T0 + i * 32L, true); // nobody takes them
		}
		long millis = (System.nanoTime() - t0) / 1_000_000;
		assertTrue(millis < 500, "10,000 offers took " + millis + " ms");
		assertEquals(10_000 - SpotterWorker.QUEUE_WINDOWS, worker.dropped());
	}

	@Test
	void theWindowIsCopiedBeforeItIsQueued() {
		float[] w = new float[512];
		w[0] = 0.5f;
		worker.onWindow(w, T0, true);
		w[0] = -1f; // the capture thread reuses or changes its array
		worker.drainNow();
		assertEquals(List.of(0.5f), model.last().firstSamples);
	}

	@Test
	void nothingIsQueuedForTheMicTestWithoutIncantationsOrWithTheSpotterOff() {
		assertTrue(worker.listening());
		worker.onStarted(false);
		assertFalse(worker.listening(), "the settings screen's mic test");
		worker.onStarted(true);
		worker.setIncantations(List.of());
		assertFalse(worker.listening(), "no incantations");
		worker.setIncantations(List.of(FIRE_BALL));
		worker.configure(false, 0.25, 1.5);
		assertFalse(worker.listening(), "incantations: false");
		worker.configure(true, 0.25, 1.5);
		worker.onStopped(StopReason.LEAVE);
		assertFalse(worker.listening(), "left the server");
	}

	@Test
	void aStreamOpensOnSpeechWithThePreRollAndClosesAfterTheQuiet() {
		windows(30, false);
		assertTrue(model.streams.isEmpty(), "silence opens no stream");
		window(true);
		assertEquals(1, model.streams.size());
		assertEquals(SpotterWorker.PRE_ROLL_WINDOWS * 512L, model.last().samples, "fed the last 0.64 s first");
		windows(9, true);
		windows(SpotterWorker.QUIET_WINDOWS - 1, false);
		assertFalse(model.last().closed);
		window(false);
		assertTrue(model.last().closed, "closed after 0.32 s more with no speech");
		windows(20, false);
		assertEquals(1, model.streams.size());
	}

	@Test
	void aHitOnAnAliasCastsThePhraseWithItsTimesOnTheAudioClock() {
		// on the third chunk: tokens at 0.40 to 0.64 s of the stream, which starts with window 1
		model.script.put(3L, new KeywordModel.Hit("fireball", new float[] {0.40f, 0.48f, 0.56f, 0.60f, 0.64f}));
		windows(30, true);
		assertEquals(1, casts.size());
		SpotterWorker.Cast c = casts.getFirst();
		assertEquals("fire ball", c.phrase());
		assertEquals("fireball", c.heard());
		// sample 0 was read with window 1, at T0 + 32 ms; a sample's instant is its read's time less the samples after it
		assertEquals(T0 + 400 - SpotterWorker.START_LEAD_MILLIS + 1, c.startedAtMillis(), "first token less the lead");
		assertEquals(T0 + 680 - SpotterWorker.END_LAG_MILLIS + 1, c.endedAtMillis(),
			"the end of the last token's 40 ms frame less the lag");
		assertEquals(T0 + 30 * 32, c.heardAtMillis(), "the newest sample when it fired");
		assertEquals(1, model.last().resets, "reset after the hit");
	}

	@Test
	void aSecondHitCountsItsFramesFromTheResetAfterTheFirst() {
		model.script.put(3L, new KeywordModel.Hit("fire_ball", new float[] {0.40f, 0.64f}));
		model.script.put(6L, new KeywordModel.Hit("fire_ball", new float[] {0.32f, 0.40f}));
		windows(60, true);
		assertEquals(2, casts.size());
		SpotterWorker.Cast second = casts.get(1);
		// reset after 3 chunks (24 frames, 0.96 s): the tokens are at 0.96 + 0.32 and 0.96 + 0.40 s
		assertEquals(T0 + 1280 - SpotterWorker.START_LEAD_MILLIS + 1, second.startedAtMillis());
		assertEquals(T0 + 1400 - SpotterWorker.END_LAG_MILLIS + 1, second.endedAtMillis());
	}

	@Test
	void aResetTheModelMadeInSilenceIsFoundFromTheChunkTheHitFiredIn() {
		// no reset since the start: frames count from 0, and the hit fires in the chunk just decoded
		assertEquals(0, SpotterWorker.frameOffset(0, 3, 16, 8, 1));
		assertEquals(24, SpotterWorker.frameOffset(3, 6, 10, 8, 1));
		// 20 chunks in, a hit whose last token is at frame 5: it fired at 7, so counted from a reset at chunk 19
		assertEquals(152, SpotterWorker.frameOffset(0, 20, 5, 8, 1));
		// a hit that fired late by up to two chunks is still counted from the reset the worker made
		assertEquals(0, SpotterWorker.frameOffset(0, 5, 16, 8, 1));
	}

	@Test
	void aNewListReopensTheOpenStreamWithItsKeywordsAndTheRecentAudio() {
		windows(25, true);
		assertEquals(1, model.opened.size());
		worker.setIncantations(List.of(FIRE_BALL, new Incantation("ice wall", List.of(), 3)));
		window(true);
		assertEquals(2, model.opened.size());
		assertTrue(model.streams.getFirst().closed);
		assertTrue(model.opened.get(1).endsWith("@ice_wall"), model.opened.get(1));
		assertEquals(21 * 512L, model.last().samples, "the last 0.64 s again, and the new window");
	}

	@Test
	void windowsDroppedFromAFullQueueStartAFreshStream() {
		for (int i = 0; i < SpotterWorker.QUEUE_WINDOWS + 6; i++) {
			worker.onWindow(new float[512], T0 + i * 32L, true); // the last six are dropped
		}
		worker.drainNow();
		assertEquals(1, model.streams.size());
		worker.onWindow(new float[512], T0 + 70 * 32L, true);
		worker.drainNow();
		assertEquals(2, model.streams.size(), "the audio is not continuous across the gap");
		assertTrue(model.streams.getFirst().closed);
		assertEquals(512, model.last().samples, "and the pre-roll from before the gap is not replayed");
	}

	@Test
	void leavingClosesTheStreamAtTheNextWait() {
		windows(5, true);
		worker.onStopped(StopReason.LEAVE);
		worker.step(null);
		assertTrue(model.last().closed);
	}

	@Test
	void shutdownReleasesTheModelItOwns() {
		windows(5, true);
		worker.shutdown(0);
		assertTrue(model.last().closed);
		assertTrue(model.closed);
	}
}
