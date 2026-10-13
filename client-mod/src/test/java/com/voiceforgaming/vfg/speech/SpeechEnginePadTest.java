package com.voiceforgaming.vfg.speech;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;

/** The silence an empty beam-search result is decoded again with (no natives: only the arithmetic). */
class SpeechEnginePadTest {
	@Test
	void silenceIsAddedBeforeAndAfter() {
		float[] s = {0.5f, -0.5f};
		float[] p = SpeechEngine.padded(s, 0.001, 0.0005); // 16 and 8 samples at 16 kHz
		assertEquals(16 + 2 + 8, p.length);
		assertArrayEquals(s, java.util.Arrays.copyOfRange(p, 16, 18));
		assertEquals(0f, p[0]);
		assertEquals(0f, p[p.length - 1]);
		assertArrayEquals(s, SpeechEngine.padded(s, 0, 0));
	}

	@Test
	void theRetriesAreAfterThenBefore() {
		assertEquals(2, SpeechEngine.EMPTY_RETRIES.length);
		assertArrayEquals(new double[] {0, 0.3}, SpeechEngine.EMPTY_RETRIES[0]);
		assertArrayEquals(new double[] {0.1, 0}, SpeechEngine.EMPTY_RETRIES[1]);
	}
}
