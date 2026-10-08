package com.voiceforgaming.vfg.audio;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

class ResamplerTest {
	@Test
	void aOneKilohertzSineAt48kComesOutAt16kAThirdAsLongStillOneKilohertzWithinHalfADecibel() {
		double amplitude = 0.5;
		int n = 48000 * 2;
		float[] in = new float[n];
		for (int i = 0; i < n; i++) {
			in[i] = (float) (amplitude * Math.sin(2 * Math.PI * 1000 * i / 48000.0));
		}
		Resampler r = new Resampler();
		float[] out = new float[0];
		for (int off = 0; off < n; off += 1536) { // as the capture thread feeds it
			float[] chunk = java.util.Arrays.copyOfRange(in, off, Math.min(n, off + 1536));
			float[] part = r.process(chunk);
			float[] joined = java.util.Arrays.copyOf(out, out.length + part.length);
			System.arraycopy(part, 0, joined, out.length, part.length);
			out = joined;
		}
		assertEquals(n / 3, out.length);

		// Skip the filter's settling time, then measure over one second.
		int from = 1000;
		int to = from + 16000;
		int crossings = 0;
		double sumSq = 0;
		for (int i = from; i < to; i++) {
			if ((out[i - 1] < 0) != (out[i] < 0)) {
				crossings++;
			}
			sumSq += out[i] * out[i];
		}
		double frequency = crossings / 2.0;
		assertTrue(Math.abs(frequency - 1000) <= 2, "frequency " + frequency);
		double rms = Math.sqrt(sumSq / (to - from));
		double db = 20 * Math.log10(rms / (amplitude / Math.sqrt(2)));
		assertTrue(Math.abs(db) <= 0.5, "level off by " + db + " dB");
	}

	@Test
	void unevenChunksKeepThePhase() {
		Resampler r = new Resampler();
		int total = 0;
		for (int len : new int[] {1, 2, 5, 1536, 7, 1000}) {
			total += r.process(new float[len]).length;
		}
		assertEquals((1 + 2 + 5 + 1536 + 7 + 1000 + 2) / 3, total);
	}

	@Test
	void highFrequenciesAreRemoved() {
		int n = 48000;
		float[] in = new float[n];
		for (int i = 0; i < n; i++) {
			in[i] = (float) (0.5 * Math.sin(2 * Math.PI * 12000 * i / 48000.0)); // would alias at 16 kHz
		}
		float[] out = new Resampler().process(in);
		double sumSq = 0;
		for (int i = 1000; i < out.length; i++) {
			sumSq += out[i] * out[i];
		}
		double rms = Math.sqrt(sumSq / (out.length - 1000));
		assertTrue(20 * Math.log10(rms / (0.5 / Math.sqrt(2))) < -40, "12 kHz leaked through: rms " + rms);
	}
}
