package com.voiceforgaming.vfg.turn;

/**
 * Whether a player has finished what they were saying, from the audio of their line: the
 * probability that the turn is over. One implementation per model; each runs on ONNX
 * Runtime through the library sherpa-onnx has already loaded.
 *
 * <p>{@link #score} is called on {@code vfg-stt} and {@link #close} on whichever thread
 * releases the speech engine, so an implementation takes a lock in both, and a score
 * after close throws {@link IllegalStateException} instead of touching freed memory.
 */
public interface TurnDetector extends AutoCloseable {
	int SAMPLE_RATE = 16000;

	/** The config's {@code turn_model} value: {@code smart-turn-v3.2} or {@code turn-1-mini}. */
	String name();

	/** The threshold used when the config sets none. */
	double defaultThreshold();

	/**
	 * The probability, 0..1, that the line is finished, from 16 kHz mono samples in
	 * [-1, 1], most recent last: from 0.5 s before the line's first speech to 0.2 s after
	 * its last. The model keeps as much of the end as it hears.
	 */
	float score(float[] audio);

	@Override
	void close();
}
