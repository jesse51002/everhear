package com.voiceforgaming.vfg.kws;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.voiceforgaming.vfg.kws.SpotterWorker.Via;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import org.junit.jupiter.api.Test;

/** The one-cast rule across the spotter and Vosk: one saying, one cast. */
class CastGateTest {
	@Test
	void theSameSayingHeardByBothEnginesIsCastOnce() {
		CastGate gate = new CastGate();
		// the spotter's re-check: close to the phrase's sound
		assertTrue(gate.claim("ice beam", 1_000, 1_450, Via.RECHECK));
		// Vosk's estimate covers it, starting earlier and ending later
		assertFalse(gate.claim("ice beam", 700, 1_600, Via.VOSK));
		// and the other way round, with the live stream
		CastGate other = new CastGate();
		assertTrue(other.claim("ice beam", 700, 1_600, Via.VOSK));
		assertFalse(other.claim("ice beam", 1_000, 1_450, Via.STREAM));
		assertEquals(1, other.claims().size());
	}

	@Test
	void anEnginesOwnClaimsAreNeverCompared() {
		CastGate gate = new CastGate();
		// "fireball fireball" said fast: Vosk's two estimated windows overlap far more than half
		assertTrue(gate.claim("fireball", 4_000, 4_800, Via.VOSK));
		assertTrue(gate.claim("fireball", 4_300, 5_100, Via.VOSK));
		// the spotter keeps its own sayings apart itself
		assertTrue(gate.claim("ice beam", 6_000, 6_400, Via.RECHECK));
		assertTrue(gate.claim("ice beam", 6_000, 6_400, Via.STREAM));
	}

	@Test
	void oneClaimStandsForOneSayingOfTheOtherEngine() {
		CastGate gate = new CastGate();
		// "fireball fireball" fast: the spotter heard the first, Vosk both, with windows that each cover it
		assertTrue(gate.claim("fireball", 4_000, 4_200, Via.RECHECK));
		assertFalse(gate.claim("fireball", 3_700, 4_500, Via.VOSK), "the first saying, heard by both");
		assertTrue(gate.claim("fireball", 3_950, 4_750, Via.VOSK), "the second: the spotter's claim stood for the first");
		assertEquals(List.of(Via.RECHECK, Via.VOSK), gate.claims().stream().map(CastGate.Claim::via).toList());
	}

	@Test
	void anotherIncantationOverTheSameAudioIsItsOwnCast() {
		CastGate gate = new CastGate();
		assertTrue(gate.claim("ice beam", 1_000, 1_500, Via.RECHECK));
		assertTrue(gate.claim("speed dash", 1_000, 1_500, Via.VOSK));
	}

	@Test
	void aPhraseInsideAnotherIsAnotherIncantation() {
		CastGate gate = new CastGate();
		assertTrue(gate.claim("fireball", 7_100, 7_450, Via.RECHECK), "the spotter heard the end of \"hot fireball\"");
		assertTrue(gate.claim("hot fireball", 6_800, 7_500, Via.VOSK), "Vosk heard it all: its own incantation");
		assertTrue(CastGate.related("fireball", "hot fireball"), "the spotter drops a held hit inside such a cast");
	}

	@Test
	void aPhraseInsideALongerOneTheOtherEngineCastIsThatSaying() {
		CastGate gate = new CastGate();
		assertTrue(gate.claim("hot fireball", 6_700, 7_350, Via.VOSK));
		assertFalse(gate.claim("fireball", 7_100, 7_450, Via.RECHECK), "the tail of the \"hot fireball\" Vosk cast");
		assertTrue(gate.claim("fireball", 8_400, 8_800, Via.RECHECK), "said again later: a cast of its own");
	}

	@Test
	void sayingsApartAreTwoCasts() {
		CastGate gate = new CastGate();
		assertTrue(gate.claim("fireball", 1_000, 1_500, Via.RECHECK));
		assertTrue(gate.claim("fireball", 1_900, 2_700, Via.VOSK), "past Vosk's slack: the next saying");
		assertTrue(gate.claim("fireball", 3_200, 3_600, Via.STREAM));
	}

	@Test
	void vosksWindowIsWidenedByItsSlackWhicheverClaimsFirst() {
		CastGate gate = new CastGate();
		// "Shield!": the re-check's window, and Vosk's final result's word times a little late
		assertTrue(gate.claim("shield", 1_010, 1_240, Via.RECHECK));
		assertFalse(gate.claim("shield", 1_200, 1_660, Via.VOSK), "40 ms of 230 overlap, 230 with the slack");
		CastGate other = new CastGate();
		assertTrue(other.claim("shield", 1_200, 1_660, Via.VOSK));
		assertFalse(other.claim("shield", 1_010, 1_240, Via.RECHECK), "the same, the other way round");
		CastGate apart = new CastGate();
		assertTrue(apart.claim("shield", 1_200, 1_660, Via.VOSK));
		assertTrue(apart.claim("shield", 2_000, 2_300, Via.STREAM), "past the slack: another saying");
	}

	@Test
	void theReachBackStopsAMarginBeforeAVoskCastsEnd() {
		CastGate gate = new CastGate();
		assertEquals(Long.MIN_VALUE, gate.reachBack());
		gate.claim("ice beam", 1_000, 1_600, Via.VOSK);
		assertEquals(1_600 - CastGate.VOSK_REACH_MARGIN_MILLIS, gate.reachBack());
		assertEquals(1_600, gate.lastEnd());
		gate.claim("fireball", 1_700, 2_000, Via.RECHECK);
		assertEquals(2_000, gate.reachBack(), "the spotter's own end, as it is");
		CastGate short_ = new CastGate();
		short_.claim("fire", 1_000, 1_100, Via.VOSK);
		assertEquals(1_000, short_.reachBack(), "never before the window's start");
	}

	@Test
	void overlapIsAtLeastHalfOfTheShorterWindow() {
		assertTrue(CastGate.overlapping(0, 1_000, 500, 1_000));
		assertTrue(CastGate.overlapping(0, 1_000, 900, 1_100), "half of the shorter, 200 ms");
		assertFalse(CastGate.overlapping(0, 1_000, 950, 1_150));
		assertFalse(CastGate.overlapping(0, 1_000, 1_001, 1_500));
		assertTrue(CastGate.overlapping(0, 1_000, 400, 400), "an instant inside");
		assertTrue(CastGate.overlapping(400, 400, 400, 400));
		assertFalse(CastGate.overlapping(0, 1_000, 1_200, 1_200));
	}

	@Test
	void relatedIsTheSamePhraseOrOneInsideTheOtherAsWholeWords() {
		assertTrue(CastGate.related("fire ball", "fire ball"));
		assertTrue(CastGate.related("fire", "fire ball"));
		assertTrue(CastGate.related("fireball", "hot fireball"));
		assertTrue(CastGate.related("destroy everything now", "everything now"));
		assertFalse(CastGate.related("fire", "fireball"));
		assertFalse(CastGate.related("ice beam", "ice wall"));
	}

	@Test
	void lastEndIsTheNewestCastsEndAndOldClaimsAreForgotten() {
		CastGate gate = new CastGate();
		assertEquals(Long.MIN_VALUE, gate.lastEnd());
		gate.claim("ice beam", 1_000, 1_500, Via.VOSK);
		gate.claim("fireball", 2_000, 2_400, Via.RECHECK);
		gate.claim("fireball", 2_100, 2_600, Via.VOSK); // refused: no change
		assertEquals(2_400, gate.lastEnd());
		gate.claim("speed dash", 1_600 + CastGate.KEEP_MILLIS, 2_200 + CastGate.KEEP_MILLIS, Via.STREAM);
		assertEquals(List.of("fireball", "speed dash"), gate.claims().stream().map(CastGate.Claim::phrase).toList());
		assertTrue(gate.claim("ice beam", 1_000, 1_500, Via.STREAM), "forgotten after 30 s");
	}

	@Test
	void twoThreadsClaimingTheSameSayingGetOneCast() throws Exception {
		for (int round = 0; round < 200; round++) {
			CastGate gate = new CastGate();
			List<Boolean> won = Collections.synchronizedList(new ArrayList<>());
			Thread a = new Thread(() -> won.add(gate.claim("ice beam", 1_000, 1_400, Via.RECHECK)));
			Thread b = new Thread(() -> won.add(gate.claim("ice beam", 800, 1_500, Via.VOSK)));
			a.start();
			b.start();
			a.join();
			b.join();
			assertEquals(1, won.stream().filter(x -> x).count());
		}
	}
}
