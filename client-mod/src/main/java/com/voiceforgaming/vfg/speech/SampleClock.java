package com.voiceforgaming.vfg.speech;

/**
 * Maps a 16 kHz sample index (counted from the last {@link #reset()}, the same count
 * the VAD sees) to a wall-clock instant.
 *
 * <p>For every read from the mic it records the sample index the read ended on and the
 * wall time of the read, and keeps three minutes of these. A sample's instant is the
 * time of the read it arrived in minus the samples after it in that read, at 16 per
 * millisecond. Because each read carries its own time, a gap in capture (a reopened
 * device) does not shift later samples.
 *
 * <p>Used from the capture thread only.
 */
public final class SampleClock {
	public static final int SAMPLE_RATE = 16000;
	private static final int SAMPLES_PER_MS = SAMPLE_RATE / 1000;
	private static final long KEEP_SAMPLES = 180L * SAMPLE_RATE;
	private static final int CAPACITY = 1 << 14;

	// Ring buffer of reads: the exclusive end index of each read, and its wall time.
	private final long[] ends = new long[CAPACITY];
	private final long[] times = new long[CAPACITY];
	private int head; // oldest
	private int size;
	private long total;

	/** Forgets every read and starts counting samples from 0 again. */
	public void reset() {
		head = 0;
		size = 0;
		total = 0;
	}

	/** Samples recorded since the last reset. */
	public long totalSamples() {
		return total;
	}

	/**
	 * Records a read of {@code count} samples that finished at {@code wallMillis}.
	 *
	 * @return the index of the first sample of this read
	 */
	public long recordRead(int count, long wallMillis) {
		long first = total;
		total += count;
		if (size == CAPACITY) {
			head = (head + 1) % CAPACITY;
			size--;
		}
		int slot = (head + size) % CAPACITY;
		ends[slot] = total;
		times[slot] = wallMillis;
		size++;
		while (size > 1 && ends[(head + 1) % CAPACITY] <= total - KEEP_SAMPLES) {
			head = (head + 1) % CAPACITY;
			size--;
		}
		return first;
	}

	/** The wall-clock instant, in epoch milliseconds, of sample {@code index}. */
	public long instantOf(long index) {
		if (size == 0) {
			throw new IllegalStateException("no reads recorded");
		}
		// First read whose (exclusive) end is past the index: the read the sample arrived in.
		int lo = 0;
		int hi = size - 1;
		if (index >= ends[(head + hi) % CAPACITY]) {
			lo = hi; // past the last read: extrapolate from it
		} else {
			while (lo < hi) {
				int mid = (lo + hi) >>> 1;
				if (ends[(head + mid) % CAPACITY] > index) {
					hi = mid;
				} else {
					lo = mid + 1;
				}
			}
		}
		int slot = (head + lo) % CAPACITY;
		long samplesAfter = ends[slot] - 1 - index;
		return times[slot] - Math.floorDiv(samplesAfter, SAMPLES_PER_MS);
	}
}
