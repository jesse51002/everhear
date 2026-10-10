package com.voiceforgaming.vfg.speech;

import static com.voiceforgaming.vfg.speech.SttWorkerTest.samples;
import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.voiceforgaming.vfg.audio.WindowListener.StopReason;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;
import org.junit.jupiter.api.Test;

/**
 * What the capture thread hands over for the turn model: a pause after each segment, at
 * once after one the VAD ended on its 0.6 s of silence and 0.6 s later after one it cut at
 * its 7 s cap, with the last 9 s of samples copied up to 0.2 s past the segment's last
 * sample. Every sample fed in is told apart from every other one
 * ({@link SttWorkerTest#sample}), so a copy shows exactly which samples it holds.
 */
class CaptureProcessorTest {
	private static final long T0 = 1_760_000_000_000L;

	/** A VAD that pops the segments a test plans, once its sample count reaches each one's pop. */
	static final class PlannedVad implements VoiceDetector {
		private record Plan(long start, long end, long popAt) {}

		private final List<Plan> plans = new ArrayList<>();
		private long count;
		private long origin; // what the fed samples were numbered at the last reset

		/** Speech from sample {@code start} to {@code end} (exclusive), popped once {@code popAt} samples are in. */
		void plan(long start, long end, long popAt) {
			plans.add(new Plan(start, end, popAt));
		}

		@Override
		public void accept(float[] window) {
			count += window.length;
		}

		@Override
		public List<RawSegment> drain() {
			List<RawSegment> out = new ArrayList<>();
			plans.removeIf(p -> {
				if (p.popAt() > count) {
					return false;
				}
				out.add(new RawSegment(p.start(), samples(origin + p.start(), origin + p.end())));
				return true;
			});
			return out;
		}

		/** Speech is in progress from a planned segment's start until it pops. */
		@Override
		public boolean speaking() {
			return plans.stream().anyMatch(p -> p.start() <= count && count < p.popAt());
		}

		@Override
		public void reset() {
			origin += count;
			count = 0;
		}

		/** Speech in progress ends where the samples do, and pops at once. */
		@Override
		public void flush() {
			plans.replaceAll(p -> new Plan(p.start(), Math.min(p.end(), count), 0));
		}
	}

	private final AtomicLong now = new AtomicLong(T0);
	private final PlannedVad vad = new PlannedVad();
	private final List<SttWorker.Item> out = new ArrayList<>();
	private final AtomicBoolean turnLoaded = new AtomicBoolean(true);
	private final CaptureProcessor capture = new CaptureProcessor(() -> vad, turnLoaded::get, out::add);
	private long fed; // every sample fed so far, across restarts

	/** Feeds {@code count} samples (a multiple of 512), a window at a time, as the mic would. */
	private void feed(long count) {
		for (long k = 0; k < count; k += 512) {
			float[] w = samples(fed, fed + 512);
			fed += 512;
			now.addAndGet(32);
			capture.onWindow(w, now.get());
		}
	}

	/** Feeds windows until {@code total} samples have been fed since the test began. */
	private void feedTo(long total) {
		feed(total - fed);
	}

	/** The window the capture thread is in when the sample count first reaches {@code samples}. */
	private static long window(long samples) {
		return (samples + 511) / 512 * 512;
	}

	private List<Segment> segments() {
		return out.stream().filter(i -> i instanceof SttWorker.SegmentItem)
			.map(i -> ((SttWorker.SegmentItem) i).segment()).toList();
	}

	private List<Pause> pauses() {
		return out.stream().filter(i -> i instanceof Pause).map(i -> (Pause) i).toList();
	}

	/** What was queued, in order: "segment", "pause", or the control item. */
	private List<String> queued() {
		return out.stream().map(i -> i instanceof SttWorker.SegmentItem ? "segment" : i instanceof Pause ? "pause"
			: i.toString()).toList();
	}

	@Test
	void aSegmentEndedByItsSilenceIsFollowedByItsPauseAtOnce() {
		capture.onStarted(true);
		vad.plan(16_384, 48_128, 48_128 + 9600); // speech from 1.0 s to 3.0 s, popped 0.6 s later
		feed(4 * 16_384);
		assertEquals(List.of("segment", "pause"), queued());
		Segment s = segments().getFirst();
		assertEquals(16_384, s.first());
		assertArrayEquals(samples(16_384, 48_128), s.samples());
		assertTrue(s.forLine());
		Pause p = pauses().getFirst();
		assertEquals(0, p.audioStart());
		assertArrayEquals(samples(0, 48_128 + CaptureProcessor.TAIL_SAMPLES), p.audio(), "up to 0.2 s past the speech");
	}

	@Test
	void theRingKeepsTheLast9Seconds() {
		capture.onStarted(true);
		long start = 10 * 16_384;
		long end = start + 16_384;
		vad.plan(start, end, end + 9600);
		feed(12 * 16_384);
		Pause p = pauses().getFirst();
		assertEquals(window(end + 9600) - SampleRing.CAPACITY, p.audioStart());
		assertArrayEquals(samples(p.audioStart(), end + CaptureProcessor.TAIL_SAMPLES), p.audio());
	}

	@Test
	void aSegmentCutAtTheCapThatEndedTheSpeechHasItsPause06SecondsAfterIt() {
		capture.onStarted(true);
		long end = 7 * 16_000;
		vad.plan(0, end, end + 1600); // the 7 s cap ends it at a 0.1 s dip, and the player has stopped
		long due = window(end + 9600);
		feedTo(due - 512);
		assertEquals(List.of("segment"), queued(), "no pause yet: 0.6 s have not passed");
		feed(512);
		assertEquals(List.of("segment", "pause"), queued());
		assertArrayEquals(samples(0, end + CaptureProcessor.TAIL_SAMPLES), pauses().getFirst().audio());
		feed(4 * 16_384);
		assertEquals(1, pauses().size(), "one pause");
	}

	@Test
	void aSegmentCutAtTheCapWhileTheTalkGoesOnHasNoPauseOfItsOwn() {
		capture.onStarted(true);
		vad.plan(0, 7 * 16_000, 7 * 16_000 + 1600); // the 7 s cap ends it at a 0.1 s dip
		vad.plan(7 * 16_000 + 1600, 8 * 16_000, 8 * 16_000 + 9600); // the talk goes on, then a pause
		feed(10 * 16_384);
		assertEquals(List.of("segment", "segment", "pause"), queued());
		assertArrayEquals(samples(0, 8 * 16_000 + CaptureProcessor.TAIL_SAMPLES), pauses().getFirst().audio());
	}

	@Test
	void leavingMidSpeechFlushesASegmentWithNoPause() {
		capture.onStarted(true);
		vad.plan(16_384, Long.MAX_VALUE, Long.MAX_VALUE); // still talking when the player leaves
		feed(2 * 16_384);
		capture.onStopped(StopReason.LEAVE);
		assertEquals(List.of("segment", "CLOSE_NOW"), queued());
		assertEquals(16_384, segments().getFirst().samples().length);
	}

	@Test
	void theMicTestHasNoPauses() {
		capture.onStarted(false);
		vad.plan(16_384, 32_768, 32_768 + 9600);
		vad.plan(40_000, 40_000 + 7 * 16_000, 40_000 + 7 * 16_000 + 1600);
		feed(14 * 16_384);
		assertEquals(List.of("segment", "segment"), queued());
		assertFalse(segments().getFirst().forLine());
	}

	@Test
	void withNoTurnModelNothingIsCopiedAndThereAreNoPauses() {
		turnLoaded.set(false); // turn_model: off, or still downloading
		capture.onStarted(true);
		vad.plan(16_384, 48_128, 48_128 + 9600);
		vad.plan(60_000, 60_000 + 7 * 16_000, 60_000 + 7 * 16_000 + 1600);
		feed(14 * 16_384);
		assertEquals(List.of("segment", "segment"), queued());
	}

	@Test
	void restartingCountsFromZeroAndForgetsTheRingAndAPauseDue() {
		capture.onStarted(true);
		vad.plan(0, 7 * 16_000, 7 * 16_000 + 1600); // cut at the cap: its pause is due 0.6 s later
		feed(window(7 * 16_000 + 1600));
		capture.onStopped(StopReason.DISCARD);
		assertEquals(List.of("segment", "DISCARD"), queued(), "stopped before the pause was due");
		long restartedAt = fed;
		capture.onStarted(true);
		vad.plan(8192, 24_576, 24_576 + 9600);
		feed(3 * 16_384);
		assertEquals(List.of("segment", "DISCARD", "segment", "pause"), queued());
		assertEquals(8192, segments().getLast().first(), "numbered from the restart");
		Pause p = pauses().getFirst();
		assertEquals(0, p.audioStart());
		assertArrayEquals(samples(restartedAt, restartedAt + 24_576 + CaptureProcessor.TAIL_SAMPLES), p.audio(),
			"nothing from before the restart");
	}

	@Test
	void theTurnModelHearsHalfASecondBeforeTheLineAndAtMost8Seconds() {
		float[] audio = samples(1000, 1000 + SampleRing.CAPACITY - 6400); // 8.6 s, as a pop leaves it
		Pause p = new Pause(1000, audio);
		long end = 1000 + audio.length;
		assertArrayEquals(samples(100_000 - 8000, end), p.turnAudio(100_000), "0.5 s before the line's first sample");
		assertArrayEquals(samples(end - Pause.TURN_SAMPLES, end), p.turnAudio(1000), "the last 8 s of a longer line");
		assertEquals(Pause.TURN_SAMPLES, p.turnAudio(0).length);

		float[] short4s = samples(1000, 1000 + 64_000);
		assertArrayEquals(short4s, new Pause(1000, short4s).turnAudio(3000),
			"no earlier than the copy goes, just after capture started");
	}

	@Test
	void aLineTheTurnModelHearsFinishedClosesWhenTheVadPops() throws Exception {
		List<LineAssembler.Line> lines = new ArrayList<>();
		SttWorkerTest.FakeTurn turn = new SttWorkerTest.FakeTurn().answering(0.9f);
		LineAssembler assembler = new LineAssembler(3, now::get, lines::add);
		Transcriber words = s -> "build me a castle";
		CaptureProcessor[] cp = new CaptureProcessor[1];
		SttWorker stt = new SttWorker(() -> words, () -> turn, null, assembler, () -> cp[0].speaking(), t -> {},
			() -> false);
		CaptureProcessor wired = new CaptureProcessor(() -> vad, () -> true, stt::submit);
		cp[0] = wired;
		wired.onStarted(true);
		long first = 16_384;
		long end = 3 * 16_384;
		vad.plan(first, end, end + 9600);
		long lastWindow = 0;
		for (long k = 0; k < 5 * 16_384 && lines.isEmpty(); k += 512) {
			float[] w = samples(fed, fed + 512);
			fed += 512;
			now.set(T0 + fed / 16);
			lastWindow = now.get();
			wired.onWindow(w, now.get());
			stt.drainNow();
		}
		assertEquals(1, lines.size());
		assertEquals(T0 + window(end + 9600) / 16, lastWindow, "closed in the window the VAD popped it");
		assertEquals(lastWindow, lines.getFirst().closedAtMillis());
		assertArrayEquals(samples(first - 8000, end + CaptureProcessor.TAIL_SAMPLES), turn.heard.getFirst());
	}

	@Test
	void aLineCutAtTheCapClosesAtThePauseAfterIt() throws Exception {
		List<LineAssembler.Line> lines = new ArrayList<>();
		SttWorkerTest.FakeTurn turn = new SttWorkerTest.FakeTurn().answering(0.9f);
		LineAssembler assembler = new LineAssembler(3, now::get, lines::add);
		Transcriber words = s -> "build me a castle out of stone with a tall tower on every corner";
		CaptureProcessor[] cp = new CaptureProcessor[1];
		SttWorker stt = new SttWorker(() -> words, () -> turn, null, assembler, () -> cp[0].speaking(), t -> {},
			() -> false);
		CaptureProcessor wired = new CaptureProcessor(() -> vad, () -> true, stt::submit);
		cp[0] = wired;
		wired.onStarted(true);
		long first = 16_384;
		long end = first + 7 * 16_000 + 4000; // 7.25 s of talk, cut at the cap on its last word
		vad.plan(first, end, end + 1600);
		long lastWindow = 0;
		for (long k = 0; k < 10 * 16_384 && lines.isEmpty(); k += 512) {
			float[] w = samples(fed, fed + 512);
			fed += 512;
			now.set(T0 + fed / 16);
			lastWindow = now.get();
			wired.onWindow(w, now.get());
			stt.drainNow();
		}
		assertEquals(1, lines.size());
		assertEquals(T0 + window(end + 9600) / 16, lastWindow, "closed 0.6 s after the speech, not 3.5 s");
		assertArrayEquals(samples(first - 8000, end + CaptureProcessor.TAIL_SAMPLES), turn.heard.getFirst(),
			"from 0.5 s before the line to 0.2 s past its speech");
	}
}
