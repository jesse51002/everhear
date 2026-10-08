package com.voiceforgaming.vfg.speech;

/**
 * Peak and RMS of float samples (full scale = 1.0) as dBFS, one decimal, floor -100.0.
 * Both are taken over a line's speech samples only, so they say how loud the player
 * spoke rather than how quiet the room was.
 */
public final class Levels {
	public static final double FLOOR_DBFS = -100.0;

	private Levels() {}

	/** The largest absolute sample value. */
	public static float peak(float[] samples) {
		float peak = 0f;
		for (float s : samples) {
			float a = Math.abs(s);
			if (a > peak) {
				peak = a;
			}
		}
		return peak;
	}

	/** The sum of squared samples, as a double so long lines do not lose precision. */
	public static double sumOfSquares(float[] samples) {
		double sum = 0;
		for (float s : samples) {
			sum += (double) s * s;
		}
		return sum;
	}

	/** {@code 20·log10(peak)}, one decimal, floor -100.0. */
	public static double peakDbfs(double peak) {
		return toDbfs(peak);
	}

	/** {@code 20·log10(RMS)} over {@code count} samples, one decimal, floor -100.0. */
	public static double meanDbfs(double sumOfSquares, long count) {
		if (count <= 0) {
			return FLOOR_DBFS;
		}
		return toDbfs(Math.sqrt(sumOfSquares / count));
	}

	/** An amplitude (0..1, full scale = 1) as dBFS, one decimal, floor -100.0. */
	public static double toDbfs(double amplitude) {
		if (!(amplitude > 0) || Double.isNaN(amplitude)) {
			return FLOOR_DBFS;
		}
		double db = 20.0 * Math.log10(amplitude);
		return oneDecimal(Math.max(FLOOR_DBFS, db));
	}

	/** Rounds to one decimal, never giving -0.0. */
	public static double oneDecimal(double value) {
		double r = Math.round(value * 10.0) / 10.0;
		return r == 0.0 ? 0.0 : r;
	}
}
