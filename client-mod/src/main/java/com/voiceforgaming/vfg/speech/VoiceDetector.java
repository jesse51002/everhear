package com.voiceforgaming.vfg.speech;

import java.util.List;

/** The VAD as the capture thread sees it. Implemented over sherpa-onnx's Silero VAD by {@link SpeechEngine}. */
public interface VoiceDetector {
	/** A finished speech segment: the index of its first sample since the last reset, and its samples. */
	record RawSegment(long start, float[] samples) {}

	/** Feeds one 512-sample window. */
	void accept(float[] window);

	/** Pops every finished segment. */
	List<RawSegment> drain();

	/** Whether speech is in progress right now. */
	boolean speaking();

	/** Forgets all state, including the sample count {@code getStart()} counts from. */
	void reset();

	/** Turns speech in progress into a last segment. */
	void flush();
}
