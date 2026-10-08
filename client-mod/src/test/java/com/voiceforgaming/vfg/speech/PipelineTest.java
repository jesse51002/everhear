package com.voiceforgaming.vfg.speech;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.voiceforgaming.vfg.audio.WindowListener.StopReason;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicLong;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/**
 * The capture thread's processor, the STT worker and the assembler together, with a
 * stand-in VAD (an energy gate) and transcriber, driven by a clock that follows the
 * sample count. What the real engine adds is covered by the engine test.
 */
class PipelineTest {
	private static final long T0 = 1_760_000_000_000L;

	/** Speech is any window louder than 0.05; a segment ends after 0.6 s below it. */
	static final class EnergyVad implements VoiceDetector {
		private final List<RawSegment> done = new ArrayList<>();
		private final List<float[]> current = new ArrayList<>();
		private long count;
		private long start = -1;
		private int quietWindows;

		@Override
		public void accept(float[] w) {
			boolean loud = Levels.peak(w) > 0.05f;
			if (loud) {
				if (start < 0) {
					start = count;
				}
				quietWindows = 0;
			}
			if (start >= 0) {
				current.add(w.clone());
				if (!loud && ++quietWindows >= 19) { // 0.6 s
					finish();
				}
			}
			count += w.length;
		}

		private void finish() {
			if (start < 0) {
				return;
			}
			int n = 0;
			for (float[] w : current) {
				n += w.length;
			}
			float[] all = new float[n];
			int off = 0;
			for (float[] w : current) {
				System.arraycopy(w, 0, all, off, w.length);
				off += w.length;
			}
			done.add(new RawSegment(start, all));
			current.clear();
			start = -1;
			quietWindows = 0;
		}

		@Override
		public List<RawSegment> drain() {
			List<RawSegment> out = new ArrayList<>(done);
			done.clear();
			return out;
		}

		@Override
		public boolean speaking() {
			return start >= 0;
		}

		@Override
		public void reset() {
			done.clear();
			current.clear();
			count = 0;
			start = -1;
		}

		@Override
		public void flush() {
			finish();
		}
	}

	private final AtomicLong now = new AtomicLong(T0);
	private final List<LineAssembler.Line> lines = new ArrayList<>();
	private final List<String> heard = new ArrayList<>();
	private final EnergyVad vad = new EnergyVad();
	private int said;
	private SttWorker stt;
	private CaptureProcessor capture;
	private long samples;

	@BeforeEach
	void setUp() {
		LineAssembler assembler = new LineAssembler(5, now::get, lines::add);
		Transcriber words = s -> "phrase" + (++said);
		stt = new SttWorker(() -> words, assembler, () -> capture.speaking(), heard::add, () -> false);
		capture = new CaptureProcessor(() -> vad, stt::submit);
	}

	/** Feeds {@code seconds} of tone (speech) or silence, a window at a time, ticking the STT worker as it goes. */
	private void feed(double seconds, boolean speech) throws InterruptedException {
		int windows = (int) Math.round(seconds * 16000 / 512);
		for (int i = 0; i < windows; i++) {
			float[] w = new float[512];
			if (speech) {
				for (int k = 0; k < w.length; k++) {
					w[k] = (float) (0.3 * Math.sin(2 * Math.PI * 300 * (samples + k) / 16000.0));
				}
			}
			samples += w.length;
			now.set(T0 + samples / 16);
			capture.onWindow(w, now.get());
			stt.drainNow();
			stt.step(0);
		}
	}

	@Test
	void speechThenSilenceMakesOneTimedLine() throws Exception {
		capture.onStarted(true);
		feed(1, false);
		feed(2, true);
		feed(2, false);
		feed(1.5, true);
		feed(7, false);
		assertEquals(1, lines.size());
		LineAssembler.Line line = lines.get(0);
		assertEquals("phrase1 phrase2", line.text());
		assertEquals(T0 + 1000, line.startedAtMillis(), 40);
		assertEquals(T0 + 6500, line.endedAtMillis(), 700); // the stand-in VAD keeps 0.6 s of tail
		assertTrue(line.peakDbfs() > -11 && line.peakDbfs() <= -10, "peak " + line.peakDbfs());
	}

	@Test
	void leavingFlushesSpeechInProgressAndClosesTheLineAtOnce() throws Exception {
		capture.onStarted(true);
		feed(1, true);
		feed(1, false);
		feed(0.5, true); // still talking when the player leaves
		capture.onStopped(StopReason.LEAVE);
		stt.drainNow();
		assertEquals(1, lines.size());
		assertEquals("phrase1 phrase2", lines.get(0).text());
		assertEquals(T0 + 2500, lines.get(0).endedAtMillis(), 40);
	}

	@Test
	void mutingDiscardsTheOpenLine() throws Exception {
		capture.onStarted(true);
		feed(1, true);
		feed(1, false);
		capture.onStopped(StopReason.DISCARD);
		stt.drainNow();
		feed(10, false);
		assertTrue(lines.isEmpty());
		assertEquals(List.of("phrase1"), heard);
	}

	@Test
	void theMicTestShowsTextAndMakesNoLine() throws Exception {
		capture.onStarted(false);
		feed(1, true);
		feed(1, false);
		capture.onStopped(StopReason.TEST_ENDED);
		stt.drainNow();
		feed(10, false);
		assertEquals(List.of("phrase1"), heard);
		assertTrue(lines.isEmpty());
	}

	@Test
	void restartingResetsTheVadAndTheClockTogether() throws Exception {
		capture.onStarted(true);
		feed(3, false);
		capture.onStopped(StopReason.DISCARD);
		stt.drainNow();
		capture.onStarted(true); // sample count is back to 0; the instants still follow the reads
		feed(1, true);
		feed(7, false);
		assertEquals(1, lines.size());
		assertEquals(T0 + 3000, lines.get(0).startedAtMillis(), 40);
	}
}
