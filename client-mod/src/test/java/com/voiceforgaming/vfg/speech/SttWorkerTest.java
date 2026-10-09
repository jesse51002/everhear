package com.voiceforgaming.vfg.speech;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.voiceforgaming.vfg.turn.TurnDetector;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;
import java.util.function.Supplier;
import org.junit.jupiter.api.Test;

/**
 * The {@code vfg-stt} worker with a stand-in turn model and transcriber, and a clock the
 * test moves: which segments are scored, on what audio, and what each score does to the
 * line. Capture starts at {@link #T0}, sample 0.
 */
class SttWorkerTest {
	private static final long T0 = 1_760_000_000_000L;

	/** A turn model that answers the scores it is given, in order, and keeps what it heard. */
	static final class FakeTurn implements TurnDetector {
		final Deque<Float> scores = new ArrayDeque<>();
		final List<float[]> heard = new ArrayList<>();
		double threshold = 0.5;
		RuntimeException failure;
		long sleepMillis;
		Runnable during = () -> {};

		FakeTurn answering(float... answers) {
			for (float a : answers) {
				scores.add(a);
			}
			return this;
		}

		@Override
		public String name() {
			return "fake";
		}

		@Override
		public double defaultThreshold() {
			return threshold;
		}

		@Override
		public float score(float[] audio) {
			heard.add(audio);
			during.run();
			if (failure != null) {
				throw failure;
			}
			if (sleepMillis > 0) {
				try {
					Thread.sleep(sleepMillis);
				} catch (InterruptedException e) {
					Thread.currentThread().interrupt();
				}
			}
			return scores.removeFirst();
		}

		@Override
		public void close() {}
	}

	private final AtomicLong now = new AtomicLong(T0);
	private final AtomicBoolean speaking = new AtomicBoolean();
	private final List<LineAssembler.Line> lines = new ArrayList<>();
	private final List<String> decisions = new ArrayList<>();
	private final Deque<String> said = new ArrayDeque<>();
	private final FakeTurn turn = new FakeTurn();
	private SttWorker stt;

	private SttWorker worker(double silenceSeconds, Supplier<TurnDetector> model, Double threshold) {
		LineAssembler assembler = new LineAssembler(silenceSeconds, now::get, lines::add, decisions::add);
		Transcriber transcriber = samples -> said.removeFirst();
		stt = new SttWorker(() -> transcriber, model, threshold, assembler, speaking::get, text -> {}, () -> false);
		return stt;
	}

	private SttWorker worker() {
		return worker(3, () -> turn, null);
	}

	/** Sample {@code i} of the capture, told apart from every other one. */
	static float sample(long i) {
		return (float) ((i % 1_000_000) / 1_000_000.0);
	}

	static float[] samples(long from, long to) {
		float[] out = new float[(int) (to - from)];
		for (int k = 0; k < out.length; k++) {
			out[k] = sample(from + k);
		}
		return out;
	}

	/**
	 * Speech from {@code startMs} to {@code endMs} after {@link #T0}, as the capture thread hands
	 * it over when the VAD pops it 0.6 s later: with the last 9 s up to 0.2 s past its end.
	 */
	private static Segment speech(long startMs, long endMs, boolean cut, boolean forLine) {
		long first = startMs * 16;
		long end = endMs * 16;
		long popped = end + 9600;
		long audioStart = Math.max(0, popped - SampleRing.CAPACITY);
		float[] audio = cut || !forLine ? null : samples(audioStart, end + CaptureProcessor.TAIL_SAMPLES);
		return Segment.of(samples(first, end), T0 + startMs, T0 + endMs, forLine, first, cut, audioStart, audio);
	}

	private static Segment speech(long startMs, long endMs) {
		return speech(startMs, endMs, false, true);
	}

	/** {@code text} said from {@code startMs} to {@code endMs}: queued at its pop, transcribed 100 ms later. */
	private void say(String text, Segment segment) throws InterruptedException {
		said.add(text);
		stt.submit(new SttWorker.SegmentItem(segment));
		now.set(segment.endMillis() + 700);
		stt.step(0);
	}

	/** Ticks every 250 ms, as {@code vfg-stt} does, until {@code ms} after {@link #T0}. */
	private void runUntil(long ms) throws InterruptedException {
		while (now.get() < T0 + ms) {
			now.addAndGet(250);
			stt.step(0);
		}
	}

	/** Ticks once at {@code ms} after {@link #T0}. */
	private void tickAt(long ms) throws InterruptedException {
		now.set(T0 + ms);
		stt.step(0);
	}

	private void assertDecisions(String... patterns) {
		assertEquals(patterns.length, decisions.size(), "decisions: " + decisions);
		for (int i = 0; i < patterns.length; i++) {
			assertTrue(decisions.get(i).matches(patterns[i]), decisions.get(i) + " should match " + patterns[i]);
		}
	}

	@Test
	void aFinishedLineClosesAtItsPause() throws Exception {
		worker();
		turn.answering(0.93f);
		say("build me a castle", speech(1000, 2300));
		assertEquals(1, lines.size());
		assertEquals("build me a castle", lines.get(0).text());
		assertEquals(T0 + 2300, lines.get(0).endedAtMillis());
		assertEquals(T0 + 3000, lines.get(0).closedAtMillis(), "0.7 s after the last word, not 3.5 s");
		assertFalse(stt.lineOpen());
		assertDecisions("turn 0\\.93 in \\d+ ms: closed");
	}

	@Test
	void anUnfinishedLineWaitsAndTheNextSegmentJoinsIt() throws Exception {
		worker();
		turn.answering(0.18f, 0.91f);
		say("build me a castle", speech(1000, 2300));
		runUntil(3700);
		assertTrue(lines.isEmpty());
		assertTrue(stt.lineOpen());
		say("out of diamond", speech(3700, 4800));
		assertEquals(1, lines.size());
		assertEquals("build me a castle out of diamond", lines.get(0).text());
		assertEquals(2, lines.get(0).segments());
		assertDecisions("turn 0\\.18 in \\d+ ms: waiting", "turn 0\\.91 in \\d+ ms: closed");
	}

	@Test
	void theModelHearsTheLineFromHalfASecondBeforeItsFirstSpeechToAFifthAfterItsLast() throws Exception {
		worker();
		turn.answering(0.18f, 0.91f);
		say("build me a castle", speech(1000, 2300));
		runUntil(3700);
		say("out of diamond", speech(3700, 4800));
		long lineFirst = 1000 * 16;
		assertArrayEquals(samples(lineFirst - 8000, 2300 * 16 + 3200), turn.heard.get(0));
		assertArrayEquals(samples(lineFirst - 8000, 4800 * 16 + 3200), turn.heard.get(1));
	}

	@Test
	void aLongLineIsHeardByItsLast8Seconds() throws Exception {
		worker();
		turn.answering(0.2f, 0.2f, 0.9f);
		say("one", speech(1000, 4000));
		say("two", speech(5000, 8000));
		say("three", speech(9000, 11_000));
		float[] last = turn.heard.get(2);
		assertEquals(Segment.TURN_SAMPLES, last.length);
		assertArrayEquals(samples(11_000 * 16 + 3200 - Segment.TURN_SAMPLES, 11_000 * 16 + 3200), last);
	}

	@Test
	void speechInProgressAtThePauseMeansNoScore() throws Exception {
		worker();
		speaking.set(true); // they were already talking again when the segment was transcribed
		say("build me a castle", speech(1000, 2300));
		assertTrue(turn.heard.isEmpty());
		speaking.set(false);
		tickAt(2300 + 3499);
		assertTrue(lines.isEmpty());
		tickAt(2300 + 3500);
		assertEquals(1, lines.size());
		assertDecisions("closed after 3\\.5 s of silence");
	}

	@Test
	void speechStartingDuringTheScoreClearsTheMark() throws Exception {
		worker();
		turn.answering(0.93f).during = () -> speaking.set(true);
		say("build me a castle", speech(1000, 2300));
		assertTrue(lines.isEmpty(), "the tick right after the score saw the speech");
		assertTrue(stt.lineOpen());
		assertDecisions("turn 0\\.93 in \\d+ ms: waiting, speech again");
	}

	@Test
	void droppedCutAndMicTestSegmentsAreNotScored() throws Exception {
		worker();
		say("...", speech(1000, 2000)); // dropped: no letter or digit
		say("build me a castle and then", speech(3000, 10_000, true, true)); // cut at the 7 s cap
		assertTrue(turn.heard.isEmpty());
		assertTrue(stt.lineOpen());
		stt.submit(SttWorker.Control.DISCARD);
		stt.step(0);
		say("testing", speech(12_000, 13_000, false, false)); // the settings screen's mic test
		assertTrue(turn.heard.isEmpty());
		assertTrue(lines.isEmpty());
	}

	@Test
	void aCutSegmentJoinsTheLineAndTheNextOneIsScored() throws Exception {
		worker();
		turn.answering(0.9f);
		say("build me a castle and then", speech(1000, 8100, true, true));
		say("put a moat around it", speech(8150, 9500));
		assertEquals(1, turn.heard.size());
		assertEquals(1, lines.size());
		assertEquals("build me a castle and then put a moat around it", lines.get(0).text());
		assertArrayEquals(samples(9500 * 16 + 3200 - Segment.TURN_SAMPLES, 9500 * 16 + 3200), turn.heard.get(0));
	}

	@Test
	void aModelThatThrowsMeansNotFinished() throws Exception {
		worker();
		turn.failure = new IllegalStateException("turn model released");
		say("build me a castle", speech(1000, 2300));
		assertTrue(lines.isEmpty());
		tickAt(2300 + 3499);
		assertTrue(lines.isEmpty());
		tickAt(2300 + 3500);
		assertEquals(1, lines.size(), "the silence closes it");
		assertDecisions("closed after 3\\.5 s of silence"); // the failure is a warning of its own
	}

	@Test
	void aModelSlowerThanHalfASecondMeansNotFinished() throws Exception {
		worker();
		turn.answering(0.99f).sleepMillis = SttWorker.TURN_LIMIT_MILLIS + 20;
		say("build me a castle", speech(1000, 2300));
		assertEquals(1, turn.heard.size(), "it answered 0.99, too late to count");
		assertTrue(lines.isEmpty());
		assertTrue(stt.lineOpen());
		tickAt(2300 + 3500);
		assertEquals(1, lines.size(), "the silence closes it");
		assertDecisions("closed after 3\\.5 s of silence"); // the slow score is a warning of its own
	}

	@Test
	void theConfiguredThresholdWinsOverTheModelsOwn() throws Exception {
		worker(3, () -> turn, 0.95);
		turn.answering(0.93f);
		say("build me a castle", speech(1000, 2300));
		assertTrue(lines.isEmpty(), "0.93 is under the configured 0.95");

		decisions.clear();
		worker(3, () -> turn, null);
		turn.threshold = 0.9;
		turn.answering(0.93f);
		say("make it day", speech(1000, 2000));
		assertEquals(1, lines.size(), "0.93 is over the model's own 0.9");
		assertDecisions("turn 0\\.93 in \\d+ ms: closed");
	}

	@Test
	void withNoModelLinesEndAfterTheSilenceAsBefore() throws Exception {
		worker(5, () -> null, null); // turn_model: off, silence_seconds: 5
		say("build me", speech(1000, 2000));
		runUntil(4000);
		say("a castle", speech(4000, 5000)); // a 2 s pause joins
		tickAt(5000 + 5499);
		assertTrue(lines.isEmpty());
		tickAt(5000 + 5500);
		assertEquals(1, lines.size());
		assertEquals("build me a castle", lines.get(0).text());
		assertEquals(T0 + 10_500, lines.get(0).closedAtMillis());
		assertDecisions("closed after 5\\.5 s of silence");
	}
}
