package com.voiceforgaming.vfg.speech;

/**
 * A stretch of speech the VAD popped: its 16 kHz samples, the wall-clock instants of
 * its first and last sample, its peak and sum of squares. {@code forLine} is false for
 * the settings screen's mic test, whose text is shown and never sent.
 */
public record Segment(float[] samples, long startMillis, long endMillis, float peak, double sumOfSquares,
		boolean forLine) {

	public static Segment of(float[] samples, long startMillis, long endMillis, boolean forLine) {
		return new Segment(samples, startMillis, endMillis, Levels.peak(samples), Levels.sumOfSquares(samples),
			forLine);
	}

	public double seconds() {
		return samples.length / (double) SampleClock.SAMPLE_RATE;
	}
}
