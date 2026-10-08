package com.voiceforgaming.vfg.speech;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;

class LevelsTest {
	private static float[] sine(double amplitude, int n) {
		float[] s = new float[n];
		for (int i = 0; i < n; i++) {
			s[i] = (float) (amplitude * Math.sin(2 * Math.PI * 1000 * i / 16000.0));
		}
		return s;
	}

	private static double peak(float[] s) {
		return Levels.peakDbfs(Levels.peak(s));
	}

	private static double mean(float[] s) {
		return Levels.meanDbfs(Levels.sumOfSquares(s), s.length);
	}

	@Test
	void fullScaleSquareIsZeroAndZero() {
		float[] s = new float[16000];
		for (int i = 0; i < s.length; i++) {
			s[i] = (i / 8) % 2 == 0 ? 1f : -1f;
		}
		assertEquals(0.0, peak(s));
		assertEquals(0.0, mean(s));
	}

	@Test
	void fullScaleSineIsZeroAndMinusThree() {
		float[] s = sine(1.0, 16000);
		assertEquals(0.0, peak(s));
		assertEquals(-3.0, mean(s));
	}

	@Test
	void sineAtPointOneIsMinusTwentyAndMinusTwentyThree() {
		float[] s = sine(0.1, 16000);
		assertEquals(-20.0, peak(s));
		assertEquals(-23.0, mean(s));
	}

	@Test
	void silenceIsTheFloor() {
		float[] s = new float[16000];
		assertEquals(-100.0, peak(s));
		assertEquals(-100.0, mean(s));
		assertEquals(-100.0, Levels.meanDbfs(0, 0));
	}

	@Test
	void veryQuietIsClampedToTheFloor() {
		assertEquals(-100.0, Levels.toDbfs(1e-9));
	}
}
