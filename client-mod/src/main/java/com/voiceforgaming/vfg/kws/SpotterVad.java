package com.voiceforgaming.vfg.kws;

/**
 * The spotter's own VAD, which says when the player stops sooner than the capture's: speech ends
 * for it after 0.15 s of silence, where the capture's VAD waits 0.35 s to end a segment for the
 * lines. {@link Spotter#vad} is sherpa-onnx's Silero VAD, a second instance of the model file the
 * speech engine loads, at the same threshold; the tests have stand-ins. Used from {@code vfg-kws}
 * only; {@link #close()} may come from another thread.
 */
public interface SpotterVad extends AutoCloseable {
	/** Feeds one 512-sample window; whether the player is speaking after it. */
	boolean speaking(float[] window);

	/** Forgets all state, for audio that does not follow on from the last window. */
	void reset();

	@Override
	void close();
}
