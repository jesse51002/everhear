package com.voiceforgaming.vfg.kws;

import com.voiceforgaming.vfg.VoicePipeline;
import com.voiceforgaming.vfg.audio.WindowListener.StopReason;
import java.util.List;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.BooleanSupplier;

/**
 * The capture thread's tap for the keyword spotter and Vosk: one VAD, the spotter's
 * ({@link Spotter#vad}: speech ends after 0.15 s of silence), says for both whether someone speaks,
 * so the two hear speech start and end on the same window and the VAD runs once. It runs on
 * {@code vfg-capture}, about 70 µs a window, and only while one of them listens; each window goes on
 * to both with its answer in place of the capture's. Until the VAD is loaded, or if it will not load,
 * they get the capture's own (the lines' VAD, which ends speech after 0.35 s).
 *
 * <p>It is reset when capture starts; a window a worker drops from its full queue does not reset it,
 * as it heard every window.
 */
public final class SpeechTap implements VoicePipeline.Tap {
	private final List<VoicePipeline.Tap> taps;
	private final BooleanSupplier listening;
	private final AtomicReference<SpotterVad> vad = new AtomicReference<>();
	private SpotterVad used;
	private boolean reset = true;

	/** {@code listening}: whether either of {@code taps} queues windows now. */
	public SpeechTap(BooleanSupplier listening, VoicePipeline.Tap... taps) {
		this.listening = listening;
		this.taps = List.of(taps);
	}

	/** The loaded VAD; the tap owns it from now on, and closes the one it replaces. */
	public void setVad(SpotterVad loaded) {
		SpotterVad old = vad.getAndSet(loaded);
		if (old != null && old != loaded) {
			old.close();
		}
	}

	/** Whether a VAD has been given. */
	public boolean hasVad() {
		return vad.get() != null;
	}

	/** Releases the VAD; from any thread. */
	public void close() {
		SpotterVad v = vad.getAndSet(null);
		if (v != null) {
			v.close();
		}
	}

	// --- vfg-capture ---

	@Override
	public void onStarted(boolean forLine) {
		reset = true;
		for (VoicePipeline.Tap t : taps) {
			t.onStarted(forLine);
		}
	}

	@Override
	public void onWindow(float[] window, long readWallMillis, boolean speaking) {
		boolean heard = speaking;
		SpotterVad v = vad.get();
		if (!listening.getAsBoolean()) {
			reset = true; // nobody listens: the VAD rests, and starts afresh with the next listener
		} else if (v != null) {
			try {
				if (reset || v != used) {
					v.reset();
					used = v;
					reset = false;
				}
				heard = v.speaking(window);
			} catch (IllegalStateException e) {
				vad.compareAndSet(v, null); // released under us: the capture's flag will do
			}
		}
		for (VoicePipeline.Tap t : taps) {
			t.onWindow(window, readWallMillis, heard);
		}
	}

	@Override
	public void onStopped(StopReason reason) {
		for (VoicePipeline.Tap t : taps) {
			t.onStopped(reason);
		}
	}
}
