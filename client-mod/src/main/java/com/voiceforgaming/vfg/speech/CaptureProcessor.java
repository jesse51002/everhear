package com.voiceforgaming.vfg.speech;

import com.voiceforgaming.vfg.audio.WindowListener;
import java.util.function.Consumer;
import java.util.function.Supplier;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * The capture thread's half of the pipeline: each 512-sample window goes to the VAD,
 * the {@link SampleClock} turns every popped segment's sample range into wall-clock
 * instants, and the segment is queued to {@code vfg-stt}. It also keeps whether speech
 * is in progress and the live peak, for the icon and the mic test.
 *
 * <p>For the turn model it keeps the last 9 s of samples in a {@link SampleRing}, and gives
 * each segment for a line a copy of it up to 0.2 s past the segment's last sample: what
 * Smart Turn hears upstream, where Pipecat asks it after 0.2 s of silence. That sample is
 * already in the ring, because the VAD pops a segment only after 0.6 s of silence. A
 * segment popped sooner was cut while the player was still talking, and gets no copy.
 */
public final class CaptureProcessor implements WindowListener {
	private static final Logger LOG = LoggerFactory.getLogger("vfg");
	private static final float PEAK_DECAY = 0.85f;
	/** The silence after a segment's last sample that the turn model hears. */
	public static final int TAIL_SAMPLES = SampleClock.SAMPLE_RATE / 5;
	/**
	 * A segment popped with less silence after it than this, half of 0.6 s, was cut mid-speech:
	 * the VAD pops one 0.6 s after a pause, and 0.1 s after a cut at its 7 s cap.
	 */
	private static final long CUT_BELOW_SAMPLES =
		Math.round(SpeechEngine.MIN_SILENCE_SECONDS * SampleClock.SAMPLE_RATE / 2);

	private final Supplier<VoiceDetector> detector;
	private final Consumer<SttWorker.Item> out;
	private final SampleClock clock = new SampleClock();
	private final SampleRing ring = new SampleRing();
	private boolean forLine;
	private volatile boolean speaking;
	private volatile float livePeak;
	private volatile boolean active;

	public CaptureProcessor(Supplier<VoiceDetector> detector, Consumer<SttWorker.Item> out) {
		this.detector = detector;
		this.out = out;
	}

	/** Speech in progress right now. */
	public boolean speaking() {
		return speaking;
	}

	/** The recent peak, 0..1, decaying. */
	public float livePeak() {
		return livePeak;
	}

	/** Whether the mic is open and windows are arriving. */
	public boolean active() {
		return active;
	}

	@Override
	public void onStarted(boolean forLine) {
		this.forLine = forLine;
		clock.reset();
		ring.reset();
		VoiceDetector vad = detector.get();
		if (vad != null) {
			try {
				vad.reset();
			} catch (IllegalStateException e) {
				// engine released; windows will only update the level until it is back
			}
		}
		speaking = false;
		livePeak = 0f;
		active = true;
	}

	@Override
	public void onWindow(float[] window, long readWallMillis) {
		clock.recordRead(window.length, readWallMillis);
		ring.write(window);
		livePeak = Math.max(Levels.peak(window), livePeak * PEAK_DECAY);
		VoiceDetector vad = detector.get();
		if (vad == null) {
			speaking = false;
			return;
		}
		try {
			vad.accept(window);
			emit(vad, false);
			speaking = vad.speaking();
		} catch (IllegalStateException e) {
			speaking = false;
		}
	}

	@Override
	public void onStopped(StopReason reason) {
		active = false;
		VoiceDetector vad = detector.get();
		try {
			if (reason == StopReason.LEAVE && vad != null) {
				vad.flush();
				emit(vad, true);
			}
			if (vad != null) {
				vad.reset();
			}
		} catch (IllegalStateException e) {
			// engine released
		}
		speaking = false;
		livePeak = 0f;
		if (forLine) {
			out.accept(reason == StopReason.LEAVE ? SttWorker.Control.CLOSE_NOW : SttWorker.Control.DISCARD);
		}
	}

	/** Queues every segment the VAD popped; {@code flushed} when capture stopped in the middle of one. */
	private void emit(VoiceDetector vad, boolean flushed) {
		for (VoiceDetector.RawSegment raw : vad.drain()) {
			float[] samples = raw.samples();
			if (samples.length == 0 || clock.totalSamples() == 0) {
				continue;
			}
			long first = raw.start();
			long last = first + samples.length - 1;
			boolean cut = flushed || ring.total() - (last + 1) < CUT_BELOW_SAMPLES;
			long audioStart = ring.oldest();
			float[] audio = null;
			if (forLine && !cut) {
				audio = ring.copy(audioStart, Math.max(audioStart, Math.min(ring.total(), last + 1 + TAIL_SAMPLES)));
			}
			Segment segment = Segment.of(samples, clock.instantOf(first), clock.instantOf(last), forLine, first, cut,
				audioStart, audio);
			LOG.debug("segment of {} samples", samples.length);
			out.accept(new SttWorker.SegmentItem(segment));
		}
	}
}
