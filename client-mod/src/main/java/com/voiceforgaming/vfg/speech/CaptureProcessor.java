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
 */
public final class CaptureProcessor implements WindowListener {
	private static final Logger LOG = LoggerFactory.getLogger("vfg");
	private static final float PEAK_DECAY = 0.85f;

	private final Supplier<VoiceDetector> detector;
	private final Consumer<SttWorker.Item> out;
	private final SampleClock clock = new SampleClock();
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
		livePeak = Math.max(Levels.peak(window), livePeak * PEAK_DECAY);
		VoiceDetector vad = detector.get();
		if (vad == null) {
			speaking = false;
			return;
		}
		try {
			vad.accept(window);
			emit(vad);
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
				emit(vad);
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

	private void emit(VoiceDetector vad) {
		for (VoiceDetector.RawSegment raw : vad.drain()) {
			float[] samples = raw.samples();
			if (samples.length == 0 || clock.totalSamples() == 0) {
				continue;
			}
			long first = raw.start();
			long last = first + samples.length - 1;
			Segment segment = Segment.of(samples, clock.instantOf(first), clock.instantOf(last), forLine);
			LOG.debug("segment of {} samples", samples.length);
			out.accept(new SttWorker.SegmentItem(segment));
		}
	}
}
