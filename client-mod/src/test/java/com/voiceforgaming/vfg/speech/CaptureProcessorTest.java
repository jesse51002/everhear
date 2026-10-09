package com.voiceforgaming.vfg.speech;

import static com.voiceforgaming.vfg.speech.SttWorkerTest.samples;
import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.voiceforgaming.vfg.audio.WindowListener.StopReason;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicLong;
import org.junit.jupiter.api.Test;

/**
 * What the capture thread keeps for the turn model and hands over with each segment: the
 * last 9 s of samples, copied up to 0.2 s past a segment's last sample, and whether the VAD
 * cut the segment mid-speech. Every sample fed in is told apart from every other one
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

		@Override
		public boolean speaking() {
			return false;
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
	private final CaptureProcessor capture = new CaptureProcessor(() -> vad, out::add);
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

	private List<Segment> segments() {
		return out.stream().filter(i -> i instanceof SttWorker.SegmentItem)
			.map(i -> ((SttWorker.SegmentItem) i).segment()).toList();
	}

	@Test
	void aSegmentGetsTheRingUpToAFifthOfASecondPastItsLastSample() {
		capture.onStarted(true);
		vad.plan(16_384, 48_128, 48_128 + 9600); // speech from 1.0 s to 3.0 s, popped 0.6 s later
		feed(4 * 16_384);
		List<Segment> got = segments();
		assertEquals(1, got.size());
		Segment s = got.getFirst();
		assertEquals(16_384, s.first());
		assertFalse(s.cut());
		assertEquals(0, s.audioStart());
		assertArrayEquals(samples(0, 48_128 + CaptureProcessor.TAIL_SAMPLES), s.audio());
		assertArrayEquals(samples(16_384, 48_128), s.samples());
		assertTrue(s.forLine());
	}

	@Test
	void theRingKeepsTheLast9Seconds() {
		capture.onStarted(true);
		long start = 10 * 16_384;
		long end = start + 16_384;
		vad.plan(start, end, end + 9600);
		feed(12 * 16_384);
		Segment s = segments().getFirst();
		long poppedAt = (end + 9600 + 511) / 512 * 512; // the window the VAD popped it in
		assertEquals(poppedAt - SampleRing.CAPACITY, s.audioStart());
		assertArrayEquals(samples(s.audioStart(), end + CaptureProcessor.TAIL_SAMPLES), s.audio());
	}

	@Test
	void aSegmentPoppedWithoutItsFullSilenceWasCutAndGetsNoAudio() {
		capture.onStarted(true);
		vad.plan(0, 7 * 16_000, 7 * 16_000 + 1600); // the 7 s cap ends it at a 0.1 s dip
		vad.plan(7 * 16_000 + 1600, 8 * 16_000, 8 * 16_000 + 9600); // then a pause
		feed(10 * 16_384);
		List<Segment> got = segments();
		assertTrue(got.get(0).cut());
		assertNull(got.get(0).audio());
		assertFalse(got.get(1).cut());
		assertArrayEquals(samples(0, 8 * 16_000 + CaptureProcessor.TAIL_SAMPLES), got.get(1).audio());
	}

	@Test
	void leavingMidSpeechFlushesACutSegment() {
		capture.onStarted(true);
		vad.plan(16_384, Long.MAX_VALUE, Long.MAX_VALUE); // still talking when the player leaves
		feed(2 * 16_384);
		capture.onStopped(StopReason.LEAVE);
		Segment s = segments().getFirst();
		assertTrue(s.cut());
		assertNull(s.audio());
		assertEquals(16_384, s.samples().length);
		assertEquals(SttWorker.Control.CLOSE_NOW, out.getLast());
	}

	@Test
	void theMicTestGetsNoAudio() {
		capture.onStarted(false);
		vad.plan(16_384, 32_768, 32_768 + 9600);
		feed(4 * 16_384);
		Segment s = segments().getFirst();
		assertFalse(s.forLine());
		assertFalse(s.cut());
		assertNull(s.audio());
	}

	@Test
	void restartingCountsFromZeroAndForgetsTheRing() {
		capture.onStarted(true);
		feed(3 * 16_384);
		capture.onStopped(StopReason.DISCARD);
		long restartedAt = fed;
		capture.onStarted(true);
		vad.plan(8192, 24_576, 24_576 + 9600);
		feed(3 * 16_384);
		Segment s = segments().getFirst();
		assertEquals(8192, s.first(), "numbered from the restart");
		assertEquals(0, s.audioStart());
		assertArrayEquals(samples(restartedAt, restartedAt + 24_576 + CaptureProcessor.TAIL_SAMPLES), s.audio(),
			"nothing from before the restart");
	}

	@Test
	void theTurnModelHearsHalfASecondBeforeTheLineAndAtMost8Seconds() {
		float[] audio = samples(1000, 1000 + SampleRing.CAPACITY - 6400); // 8.6 s, as a pop leaves it
		Segment s = Segment.of(new float[16_000], 0, 1000, true, 100_000, false, 1000, audio);
		long end = 1000 + audio.length;
		assertArrayEquals(samples(100_000 - 8000, end), s.turnAudio(100_000), "0.5 s before the line's first sample");
		assertArrayEquals(samples(end - Segment.TURN_SAMPLES, end), s.turnAudio(1000), "the last 8 s of a longer line");
		assertEquals(Segment.TURN_SAMPLES, s.turnAudio(0).length);

		float[] short4s = samples(1000, 1000 + 64_000);
		Segment early = Segment.of(new float[16_000], 0, 1000, true, 3000, false, 1000, short4s);
		assertArrayEquals(short4s, early.turnAudio(3000), "no earlier than the copy goes, just after capture started");
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
		CaptureProcessor wired = new CaptureProcessor(() -> vad, stt::submit);
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
		assertEquals(T0 + (end + 9600 + 511) / 512 * 512 / 16, lastWindow, "closed in the window the VAD popped it");
		assertEquals(lastWindow, lines.getFirst().closedAtMillis());
		assertArrayEquals(samples(first - 8000, end + CaptureProcessor.TAIL_SAMPLES), turn.heard.getFirst());
	}
}
