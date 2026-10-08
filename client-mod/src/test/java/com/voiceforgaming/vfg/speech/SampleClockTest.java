package com.voiceforgaming.vfg.speech;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;

class SampleClockTest {
	@Test
	void aSampleMapsToTheWallTimeOfTheReadItArrivedIn() {
		SampleClock clock = new SampleClock();
		assertEquals(0, clock.recordRead(512, 1_000));
		assertEquals(512, clock.recordRead(512, 1_032));
		// The last sample of a read is the read's time.
		assertEquals(1_000, clock.instantOf(511));
		assertEquals(1_032, clock.instantOf(1023));
		// Earlier samples in the same read: minus the samples after them, 16 per millisecond.
		assertEquals(1_032 - 1, clock.instantOf(1023 - 16));
		assertEquals(1_032 - 10, clock.instantOf(1023 - 160));
		assertEquals(1_032 - 31, clock.instantOf(512));
		assertEquals(1_000 - 31, clock.instantOf(0));
	}

	@Test
	void aGapInCaptureDoesNotShiftLaterSamples() {
		SampleClock clock = new SampleClock();
		clock.recordRead(512, 1_000);
		clock.recordRead(512, 9_000); // the device was reopened 8 s later
		assertEquals(1_000, clock.instantOf(511));
		assertEquals(9_000, clock.instantOf(1023));
		assertEquals(9_000 - 31, clock.instantOf(512));
	}

	@Test
	void aLongReadIsSpreadBackwardsFromItsTime() {
		SampleClock clock = new SampleClock();
		clock.recordRead(1600, 50_000); // 100 ms in one read
		assertEquals(50_000, clock.instantOf(1599));
		assertEquals(50_000 - 99, clock.instantOf(0));
	}

	@Test
	void resetStartsTheCountAgain() {
		SampleClock clock = new SampleClock();
		clock.recordRead(512, 1_000);
		clock.reset();
		assertEquals(0, clock.totalSamples());
		assertEquals(0, clock.recordRead(512, 2_000));
		assertEquals(2_000, clock.instantOf(511));
	}

	@Test
	void keepsThreeMinutesAndExtrapolatesBeyond() {
		SampleClock clock = new SampleClock();
		long t = 0;
		int reads = 4 * 60 * 16000 / 512; // four minutes of reads
		for (int i = 0; i < reads; i++) {
			t += 32;
			clock.recordRead(512, t);
		}
		long last = clock.totalSamples() - 1;
		assertEquals(t, clock.instantOf(last));
		// Two minutes back is still exact.
		long twoMinutes = last - 120L * 16000;
		assertEquals(t - 120_000, clock.instantOf(twoMinutes));
	}
}
