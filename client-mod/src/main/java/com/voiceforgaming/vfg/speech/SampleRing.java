package com.voiceforgaming.vfg.speech;

/**
 * The last 9 s of 16 kHz samples, numbered as the {@link SampleClock} numbers them: from 0
 * at the last {@link #reset()}, the same count the VAD sees. It is what the turn model
 * hears: when the VAD pops a segment, the line's end is still in here.
 *
 * <p>Used from the capture thread only.
 */
public final class SampleRing {
	public static final int SECONDS = 9;
	public static final int CAPACITY = SECONDS * SampleClock.SAMPLE_RATE;

	private final float[] ring = new float[CAPACITY];
	private long total;

	/** Forgets every sample and starts counting from 0 again. */
	public void reset() {
		total = 0;
	}

	/** Samples written since the last reset; the next one is numbered this. */
	public long total() {
		return total;
	}

	/** The number of the oldest sample still kept. */
	public long oldest() {
		return Math.max(0, total - CAPACITY);
	}

	public void write(float[] samples) {
		int from = 0;
		int count = samples.length;
		if (count > CAPACITY) { // only the last 9 s of a longer write can be kept
			from = count - CAPACITY;
			total += from;
			count = CAPACITY;
		}
		int slot = (int) (total % CAPACITY);
		int first = Math.min(count, CAPACITY - slot);
		System.arraycopy(samples, from, ring, slot, first);
		System.arraycopy(samples, from + first, ring, 0, count - first);
		total += count;
	}

	/** Samples {@code from} to {@code to} (exclusive), both between {@link #oldest()} and {@link #total()}. */
	public float[] copy(long from, long to) {
		if (from < oldest() || to > total || from > to) {
			throw new IllegalArgumentException("samples " + from + " to " + to + " are not kept (" + oldest() + " to "
				+ total + ")");
		}
		float[] out = new float[(int) (to - from)];
		int slot = (int) (from % CAPACITY);
		int first = Math.min(out.length, CAPACITY - slot);
		System.arraycopy(ring, slot, out, 0, first);
		System.arraycopy(ring, 0, out, first, out.length - first);
		return out;
	}
}
