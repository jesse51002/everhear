package com.voiceforgaming.vfg.audio;

/**
 * 48 kHz to 16 kHz for mics that will not open at 16 kHz: a windowed-sinc low-pass
 * (Blackman window, cutoff 7.2 kHz), then every third sample. Streaming: the filter's
 * history and the decimation phase carry over between calls. Not thread-safe.
 */
public final class Resampler {
	public static final int FACTOR = 3;
	private static final int TAPS = 95;
	private static final double CUTOFF = 7200.0 / 48000.0; // cycles per input sample

	private static final float[] KERNEL = buildKernel();

	private final float[] history = new float[TAPS - 1];
	private long position; // input samples seen

	private static float[] buildKernel() {
		double[] k = new double[TAPS];
		int mid = (TAPS - 1) / 2;
		double sum = 0;
		for (int i = 0; i < TAPS; i++) {
			int n = i - mid;
			double sinc = n == 0 ? 2 * CUTOFF : Math.sin(2 * Math.PI * CUTOFF * n) / (Math.PI * n);
			double window = 0.42 - 0.5 * Math.cos(2 * Math.PI * i / (TAPS - 1))
				+ 0.08 * Math.cos(4 * Math.PI * i / (TAPS - 1));
			k[i] = sinc * window;
			sum += k[i];
		}
		float[] out = new float[TAPS];
		for (int i = 0; i < TAPS; i++) {
			out[i] = (float) (k[i] / sum); // unity gain at DC
		}
		return out;
	}

	/** Forgets the filter history. */
	public void reset() {
		java.util.Arrays.fill(history, 0f);
		position = 0;
	}

	/** Filters and decimates {@code in}; returns the 16 kHz samples it yields. */
	public float[] process(float[] in) {
		return process(in, in.length);
	}

	public float[] process(float[] in, int length) {
		int firstOffset = (int) ((FACTOR - position % FACTOR) % FACTOR);
		int outCount = length > firstOffset ? (length - firstOffset + FACTOR - 1) / FACTOR : 0;
		float[] out = new float[outCount];
		int h = history.length;
		int o = 0;
		for (int i = firstOffset; i < length; i += FACTOR) {
			// y[i] = sum_k kernel[k] * x[i - k], x before this call taken from history.
			double acc = 0;
			for (int k = 0; k < TAPS; k++) {
				int j = i - k;
				float x = j >= 0 ? in[j] : history[h + j];
				acc += KERNEL[k] * x;
			}
			out[o++] = (float) acc;
		}
		// Keep the last TAPS-1 input samples.
		if (length >= h) {
			System.arraycopy(in, length - h, history, 0, h);
		} else {
			System.arraycopy(history, length, history, 0, h - length);
			System.arraycopy(in, 0, history, h - length, length);
		}
		position += length;
		return out;
	}
}
