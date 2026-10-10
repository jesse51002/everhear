package com.voiceforgaming.vfg.speech;

import com.voiceforgaming.vfg.audio.WindowListener;
import java.util.function.BooleanSupplier;
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
 * <p>For the turn model it keeps the last 9 s of samples in a {@link SampleRing}, and at
 * every pause while capturing for a line, 0.35 s of silence after the last speech the VAD
 * saw, it queues a {@link Pause} after that speech's segment: a copy of the ring up to 0.2 s
 * past the segment's last sample, what Smart Turn hears upstream, where Pipecat asks it
 * after 0.2 s of silence. A segment the VAD ended after its 0.35 s of silence has its pause
 * at once. One it ended at its 7 s cap, 0.1 s after its last sample, has its pause only
 * once the VAD is not hearing speech and 0.35 s of samples have passed since that sample
 * with no newer segment: if the player was still talking, the speech that follows ends in
 * a segment of its own, with its own pause. A segment flushed because capture stopped has
 * no pause, and with no turn model loaded nothing is copied and there are no pauses.
 */
public final class CaptureProcessor implements WindowListener {
	private static final Logger LOG = LoggerFactory.getLogger("vfg");
	private static final float PEAK_DECAY = 0.85f;
	/** The silence after a segment's last sample that the turn model hears. */
	public static final int TAIL_SAMPLES = SampleClock.SAMPLE_RATE / 5;
	/** The silence that makes a pause: the VAD's own, 0.35 s. */
	private static final long PAUSE_SAMPLES = Math.round(SpeechEngine.MIN_SILENCE_SECONDS * SampleClock.SAMPLE_RATE);
	/**
	 * A segment popped with less silence after it than this, half of 0.35 s, was ended at the
	 * VAD's 7 s cap: it pops one 0.35 s after a pause, and 0.1 s after a cut at its cap.
	 */
	private static final long CAPPED_BELOW_SAMPLES = PAUSE_SAMPLES / 2;

	private final Supplier<VoiceDetector> detector;
	private final BooleanSupplier turnLoaded;
	private final Consumer<SttWorker.Item> out;
	private final SampleClock clock = new SampleClock();
	private final SampleRing ring = new SampleRing();
	private boolean forLine;
	private volatile boolean speaking;
	private volatile float livePeak;
	private volatile boolean active;
	private long capped = -1; // the end of a segment cut at the cap, until its pause or a newer segment

	/** With no turn model: segments only, no pauses. */
	public CaptureProcessor(Supplier<VoiceDetector> detector, Consumer<SttWorker.Item> out) {
		this(detector, () -> false, out);
	}

	/** {@code turnLoaded}: whether a turn model is loaded now, to hear the pauses. */
	public CaptureProcessor(Supplier<VoiceDetector> detector, BooleanSupplier turnLoaded,
			Consumer<SttWorker.Item> out) {
		this.detector = detector;
		this.turnLoaded = turnLoaded;
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
		capped = -1;
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
			if (capped >= 0 && !speaking && ring.total() - capped >= PAUSE_SAMPLES) {
				pause(capped);
			}
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
		capped = -1;
		if (forLine) {
			out.accept(reason == StopReason.LEAVE ? SttWorker.Control.CLOSE_NOW : SttWorker.Control.DISCARD);
		}
	}

	/**
	 * Queues every segment the VAD popped, each with its pause when it has one now;
	 * {@code flushed} when capture stopped in the middle of one.
	 */
	private void emit(VoiceDetector vad, boolean flushed) {
		for (VoiceDetector.RawSegment raw : vad.drain()) {
			float[] samples = raw.samples();
			if (samples.length == 0 || clock.totalSamples() == 0) {
				continue;
			}
			long first = raw.start();
			long last = first + samples.length - 1;
			LOG.debug("segment of {} samples", samples.length);
			out.accept(new SttWorker.SegmentItem(Segment.of(samples, clock.instantOf(first), clock.instantOf(last),
				forLine, first)));
			capped = -1;
			if (flushed || !forLine || !turnLoaded.getAsBoolean()) {
				continue;
			}
			if (ring.total() - (last + 1) < CAPPED_BELOW_SAMPLES) {
				capped = last + 1;
			} else {
				pause(last + 1);
			}
		}
	}

	/** Queues the pause after speech that ended at sample {@code end} (exclusive), with the ring up to 0.2 s past it. */
	private void pause(long end) {
		capped = -1;
		long audioStart = ring.oldest();
		out.accept(new Pause(audioStart, ring.copy(audioStart, Math.max(audioStart,
			Math.min(ring.total(), end + TAIL_SAMPLES)))));
	}
}
