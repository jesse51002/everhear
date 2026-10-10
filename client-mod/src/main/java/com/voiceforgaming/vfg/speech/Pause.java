package com.voiceforgaming.vfg.speech;

import java.util.Arrays;

/**
 * A pause the capture thread heard while capturing for a line: 0.6 s of silence after the
 * last speech the VAD saw. It carries what the turn model hears there: {@code audio}, the
 * capture's last 9 s up to 0.2 s past that speech, whose first sample is number
 * {@code audioStart} in the capture's count (the {@link SampleClock}'s, from the last
 * restart). It is queued after the segment the speech ended in, and asks the turn model
 * about whatever line is open, whether or not that segment joined it. The numbers never
 * span a restart, because a stop always closes or discards the line.
 */
public record Pause(long audioStart, float[] audio) implements SttWorker.Item {
	/** Pipecat's pre-speech padding: the turn model hears this much before the line's first sample. */
	public static final int LEAD_SAMPLES = SampleClock.SAMPLE_RATE / 2;
	/** The most the turn model hears: the end of a longer line. */
	public static final int TURN_SAMPLES = 8 * SampleClock.SAMPLE_RATE;

	/**
	 * What the turn model hears at this pause: {@link #audio} from 0.5 s before
	 * {@code lineFirst}, the first sample of the open line (or from as far back as the copy
	 * goes), to its end, and at most the last 8 s of that.
	 */
	public float[] turnAudio(long lineFirst) {
		long end = audioStart + audio.length;
		long from = Math.max(audioStart, Math.max(lineFirst - LEAD_SAMPLES, end - TURN_SAMPLES));
		return Arrays.copyOfRange(audio, (int) (from - audioStart), audio.length);
	}
}
