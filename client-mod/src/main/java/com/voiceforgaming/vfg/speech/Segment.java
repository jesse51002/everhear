package com.voiceforgaming.vfg.speech;

import java.util.Arrays;

/**
 * A stretch of speech the VAD popped: its 16 kHz samples, the wall-clock instants of
 * its first and last sample, its peak and sum of squares. {@code forLine} is false for
 * the settings screen's mic test, whose text is shown and never sent.
 *
 * <p>For the turn model it also carries {@code first}, the number of its first sample in
 * the capture's count (the {@link SampleClock}'s, from the last restart); {@code cut},
 * whether the VAD ended it while the player was still talking, at its 7 s cap or because
 * capture stopped, rather than after its 0.6 s of silence; and {@code audio}, the capture's
 * last 9 s up to 0.2 s past its last sample, whose first sample is number
 * {@code audioStart}. {@code audio} is {@code null} for a cut segment and for the mic test,
 * which are never scored. The numbers never span a restart, because a stop always closes
 * or discards the line.
 */
public record Segment(float[] samples, long startMillis, long endMillis, float peak, double sumOfSquares,
		boolean forLine, long first, boolean cut, long audioStart, float[] audio) {
	/** Pipecat's pre-speech padding: the turn model hears this much before the line's first sample. */
	public static final int LEAD_SAMPLES = SampleClock.SAMPLE_RATE / 2;
	/** The most the turn model hears: the end of a longer line. */
	public static final int TURN_SAMPLES = 8 * SampleClock.SAMPLE_RATE;

	public static Segment of(float[] samples, long startMillis, long endMillis, boolean forLine, long first,
			boolean cut, long audioStart, float[] audio) {
		return new Segment(samples, startMillis, endMillis, Levels.peak(samples), Levels.sumOfSquares(samples),
			forLine, first, cut, audioStart, audio);
	}

	public double seconds() {
		return samples.length / (double) SampleClock.SAMPLE_RATE;
	}

	/**
	 * What the turn model hears at this segment's pause: {@link #audio} from 0.5 s before
	 * {@code lineFirst}, the first sample of the line this segment is in (or from as far back
	 * as the copy goes), to its end, and at most the last 8 s of that.
	 */
	public float[] turnAudio(long lineFirst) {
		long end = audioStart + audio.length;
		long from = Math.max(audioStart, Math.max(lineFirst - LEAD_SAMPLES, end - TURN_SAMPLES));
		return Arrays.copyOfRange(audio, (int) (from - audioStart), audio.length);
	}
}
