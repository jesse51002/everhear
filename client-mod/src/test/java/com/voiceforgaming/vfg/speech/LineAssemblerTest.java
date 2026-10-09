package com.voiceforgaming.vfg.speech;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicLong;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class LineAssemblerTest {
	private final AtomicLong now = new AtomicLong();
	private final List<LineAssembler.Line> lines = new ArrayList<>();
	private final List<String> decisions = new ArrayList<>();
	private LineAssembler assembler;

	@BeforeEach
	void setUp() {
		assembler = new LineAssembler(5, now::get, lines::add);
	}

	/** A segment with {@code text}, from {@code startMs} to {@code endMs}, at a constant amplitude. */
	private static LineAssembler.Heard seg(String text, long startMs, long endMs, float amplitude) {
		long samples = (endMs - startMs) * 16;
		return new LineAssembler.Heard(text, startMs, endMs, amplitude, (double) amplitude * amplitude * samples, samples);
	}

	private static LineAssembler.Heard seg(String text, long startMs, long endMs) {
		return seg(text, startMs, endMs, 0.1f);
	}

	/** Advances the clock in 250 ms ticks, as vfg-stt does. */
	private void runUntil(long t) {
		runUntil(assembler, t);
	}

	private void runUntil(LineAssembler a, long t) {
		while (now.get() < t) {
			now.addAndGet(250);
			a.tick(false, false);
		}
	}

	/** The assembler as the mod has it now: 3 s of silence, and the turn model's verdicts. */
	private LineAssembler withTurns() {
		return new LineAssembler(3, now::get, lines::add, decisions::add);
	}

	@Test
	void twoSegmentsTwoSecondsApartMakeOneLine() {
		now.set(1000);
		assembler.accept(seg("build me", 0, 1000));
		runUntil(3000);
		assembler.accept(seg("a castle", 3000, 4000));
		runUntil(20_000);
		assertEquals(1, lines.size());
		assertEquals("build me a castle", lines.get(0).text());
	}

	@Test
	void twoSegmentsSixSecondsApartMakeTwoLines() {
		now.set(1000);
		assembler.accept(seg("build me", 0, 1000));
		runUntil(7000);
		assembler.accept(seg("a castle", 7000, 8000));
		runUntil(30_000);
		assertEquals(2, lines.size());
		assertEquals("build me", lines.get(0).text());
		assertEquals("a castle", lines.get(1).text());
	}

	@Test
	void aLateSegmentClosesTheOpenLineEvenWithoutATick() {
		assembler.accept(seg("first", 0, 1000));
		assembler.accept(seg("second", 7000, 8000)); // 6 s after the first ended
		assertEquals(1, lines.size());
		assertEquals("first", lines.get(0).text());
		assertTrue(assembler.isOpen());
	}

	@Test
	void aSegmentStartingExactlyAtTheSilenceLimitStillJoins() {
		assembler.accept(seg("one", 0, 1000));
		assembler.accept(seg("two", 6000, 7000));
		assembler.closeNow();
		assertEquals(1, lines.size());
		assertEquals("one two", lines.get(0).text());
	}

	@Test
	void aLineDoesNotCloseBeforeEndedAtPlusSilencePlusHalfASecond() {
		assembler.accept(seg("hello there", 1000, 2000));
		now.set(2000 + 5000 + 499);
		assembler.tick(false, false);
		assertTrue(lines.isEmpty());
		now.set(2000 + 5000 + 500);
		assembler.tick(true, false); // speech in progress holds it open
		assertTrue(lines.isEmpty());
		assembler.tick(false, true); // so does a segment waiting to be transcribed
		assertTrue(lines.isEmpty());
		assembler.tick(false, false);
		assertEquals(1, lines.size());
		assertEquals(7500, lines.get(0).closedAtMillis());
	}

	@Test
	void startedAtAndEndedAtComeFromTheFirstAndLastSegment() {
		assembler.accept(seg("one", 10_100, 11_000));
		assembler.accept(seg("two", 12_000, 13_400));
		assembler.accept(seg("three", 14_000, 15_250));
		runUntil(30_000);
		LineAssembler.Line line = lines.get(0);
		assertEquals(10_100, line.startedAtMillis());
		assertEquals(15_250, line.endedAtMillis());
	}

	@Test
	void peakAndRmsCombineAcrossSegments() {
		assembler.accept(seg("loud", 0, 1000, 0.5f)); // 16000 samples at 0.5
		assembler.accept(seg("quiet", 2000, 5000, 0.1f)); // 48000 samples at 0.1
		runUntil(20_000);
		LineAssembler.Line line = lines.get(0);
		assertEquals(Levels.toDbfs(0.5), line.peakDbfs());
		double meanSquare = (0.25 * 16000 + 0.01 * 48000) / 64000;
		assertEquals(Levels.toDbfs(Math.sqrt(meanSquare)), line.meanDbfs());
		assertEquals(-6.0, line.peakDbfs());
		assertEquals(-11.5, line.meanDbfs());
	}

	@Test
	void emptyAndLetterlessTextIsDropped() {
		assertEquals(LineAssembler.Rejection.NO_LETTER_OR_DIGIT, assembler.accept(seg("", 0, 1000)));
		assertEquals(LineAssembler.Rejection.NO_LETTER_OR_DIGIT, assembler.accept(seg("   ", 0, 1000)));
		assertEquals(LineAssembler.Rejection.NO_LETTER_OR_DIGIT, assembler.accept(seg("... !?", 0, 1000)));
		assertEquals(LineAssembler.Rejection.NO_LETTER_OR_DIGIT, assembler.accept(seg("♪ ♪", 0, 1000)));
		assertFalse(assembler.isOpen());
		runUntil(20_000);
		assertTrue(lines.isEmpty());
	}

	@Test
	void aRepetitionLoopIsDropped() {
		// 50 characters from 1 s of audio is over 40 per second.
		assertEquals(LineAssembler.Rejection.TOO_FAST, assembler.accept(seg("x".repeat(50), 0, 1000)));
		assertEquals(LineAssembler.Rejection.NONE, assembler.accept(seg("x".repeat(40), 0, 1000)));
	}

	@Test
	void whitespaceIsCollapsed() {
		assembler.accept(seg("  build \n me\t a   castle ", 0, 2000));
		assembler.closeNow();
		assertEquals("build me a castle", lines.get(0).text());
	}

	@Test
	void a2100CharacterLineIsCutTo2000OrFewerAtASpace() {
		String words1400 = words(1400);
		String words699 = words(699);
		assembler.accept(seg(words1400, 0, 35_000)); // 40 characters a second, allowed
		assembler.accept(seg(words699, 36_000, 54_000));
		assertEquals(1, lines.size(), "1500 characters closes the line early");
		String text = lines.get(0).text();
		assertTrue(text.length() <= 2000, "length " + text.length());
		assertTrue(text.length() >= 1990, "length " + text.length());
		assertTrue((words1400 + " " + words699).startsWith(text + " "), "cut at a space");
		assertFalse(text.endsWith(" "));
	}

	@Test
	void cutIsAHardCutWithNoSpace() {
		assertEquals("a".repeat(2000), LineAssembler.cut("a".repeat(2100), 2000));
		assertEquals("short", LineAssembler.cut("short", 2000));
		assertEquals("aaa bb", LineAssembler.cut("aaa bb cc", 7));
	}

	@Test
	void sixtySecondsOfSpeechClosesALineEarly() {
		for (int i = 0; i < 6; i++) {
			long start = i * 11_000L;
			assembler.accept(seg("segment " + i, start, start + 10_000));
		}
		assertEquals(1, lines.size(), "closed the moment it held 60 s");
		assertFalse(assembler.isOpen());
		assembler.accept(seg("next", 66_000, 67_000));
		assertTrue(assembler.isOpen(), "the next segment opens a new line");
		assembler.closeNow();
		assertEquals(2, lines.size());
		assertEquals("next", lines.get(1).text());
	}

	@Test
	void closingOnLeaveSendsTheOpenLineAtOnceWithEndedAtAtItsLastSpeech() {
		assembler.accept(seg("build me a castle", 1000, 2300));
		now.set(2600); // the player leaves 0.3 s after speaking
		assembler.closeNow();
		assertEquals(1, lines.size());
		assertEquals(2300, lines.get(0).endedAtMillis());
		assertEquals(2600, lines.get(0).closedAtMillis());
	}

	@Test
	void discardDropsTheOpenLine() {
		assembler.accept(seg("muted now", 0, 1000));
		assembler.discard();
		runUntil(20_000);
		assertTrue(lines.isEmpty());
	}

	@Test
	void theSilenceSettingIsHonoured() {
		LineAssembler tenSeconds = new LineAssembler(10, now::get, lines::add);
		tenSeconds.accept(seg("one", 0, 1000));
		tenSeconds.accept(seg("two", 9000, 10_000)); // 8 s pause: joins at 10 s
		now.set(10_000 + 10_000 + 499);
		tenSeconds.tick(false, false);
		assertTrue(lines.isEmpty());
		now.set(10_000 + 10_500);
		tenSeconds.tick(false, false);
		assertEquals("one two", lines.get(0).text());
	}

	// --- the turn model's verdicts ---

	@Test
	void aLineHeardFinishedClosesAtItsPause() {
		LineAssembler a = withTurns();
		a.accept(seg("build me a castle", 1000, 2300));
		now.set(2900); // the VAD's 0.6 s, then the score
		a.turn("turn 0.93 in 31 ms", true);
		assertTrue(a.finished());
		a.tick(false, false);
		assertEquals(1, lines.size());
		assertEquals("build me a castle", lines.get(0).text());
		assertEquals(2300, lines.get(0).endedAtMillis());
		assertEquals(2900, lines.get(0).closedAtMillis(), "at the pause, not 3.5 s after it");
		assertEquals(List.of("turn 0.93 in 31 ms: closed"), decisions);
	}

	@Test
	void aLineNotFinishedWaitsAndTheNextSegmentJoinsIt() {
		LineAssembler a = withTurns();
		a.accept(seg("build me a castle", 1000, 2300));
		now.set(2900);
		a.turn("turn 0.18 in 29 ms", false);
		runUntil(a, 4500);
		assertTrue(lines.isEmpty(), "waiting");
		a.accept(seg("out of diamond", 3700, 4800));
		assertEquals(2, a.segments());
		now.set(5400);
		a.turn("turn 0.91 in 30 ms", true);
		a.tick(false, false);
		assertEquals(1, lines.size());
		assertEquals("build me a castle out of diamond", lines.get(0).text());
		assertEquals(1000, lines.get(0).startedAtMillis());
		assertEquals(4800, lines.get(0).endedAtMillis());
		assertEquals(List.of("turn 0.18 in 29 ms: waiting", "turn 0.91 in 30 ms: closed"), decisions);
	}

	@Test
	void speechStartingAgainClearsTheMark() {
		LineAssembler a = withTurns();
		a.accept(seg("build me a castle", 1000, 2300));
		now.set(2900);
		a.turn("turn 0.93 in 31 ms", true);
		a.tick(true, false); // they started talking again
		assertFalse(a.finished());
		assertTrue(lines.isEmpty());
		now.set(3500);
		a.tick(false, false); // whatever they said was dropped: the line waits for the silence now
		assertTrue(lines.isEmpty());
		now.set(2300 + 3500);
		a.tick(false, false);
		assertEquals(1, lines.size());
		assertEquals(List.of("turn 0.93 in 31 ms: waiting, speech again", "closed after 3.5 s of silence"), decisions);
	}

	@Test
	void aSegmentJoiningClearsTheMark() {
		LineAssembler a = withTurns();
		a.accept(seg("build me a castle", 1000, 2300));
		now.set(2900);
		a.turn("turn 0.93 in 31 ms", true);
		a.tick(false, true); // another segment waits to be transcribed
		assertTrue(lines.isEmpty());
		a.accept(seg("out of diamond", 2700, 3600));
		assertFalse(a.finished(), "the joined segment is scored at its own pause");
		a.tick(false, false);
		assertTrue(lines.isEmpty());
		assertEquals(List.of("turn 0.93 in 31 ms: waiting, speech again"), decisions);
	}

	@Test
	void aFinishedLineWaitsForASegmentBeingTranscribedAndClosesIfItIsDropped() {
		LineAssembler a = withTurns();
		a.accept(seg("make it day", 1000, 1900));
		now.set(2500);
		a.turn("turn 0.88 in 30 ms", true);
		a.tick(false, true);
		assertTrue(lines.isEmpty());
		assertEquals(LineAssembler.Rejection.NO_LETTER_OR_DIGIT, a.accept(seg("...", 2200, 2600)));
		a.tick(false, false);
		assertEquals(1, lines.size());
		assertEquals("make it day", lines.get(0).text());
		assertEquals(List.of("turn 0.88 in 30 ms: closed"), decisions);
	}

	@Test
	void silenceClosesALineTheModelKeepsWaitingAt3PlusHalfASecond() {
		LineAssembler a = withTurns();
		a.accept(seg("build me a", 1000, 2000));
		now.set(2600);
		a.turn("turn 0.10 in 30 ms", false);
		now.set(2000 + 3000 + 499);
		a.tick(false, false);
		assertTrue(lines.isEmpty());
		now.set(2000 + 3500);
		a.tick(true, false); // speech in progress still holds it open
		a.tick(false, true); // and so does a segment waiting to be transcribed
		assertTrue(lines.isEmpty());
		a.tick(false, false);
		assertEquals(1, lines.size());
		assertEquals(5500, lines.get(0).closedAtMillis());
		assertEquals(List.of("turn 0.10 in 30 ms: waiting", "closed after 3.5 s of silence"), decisions);
	}

	@Test
	void aSegmentAfterTheSilenceClosesTheLineBeforeIt() {
		LineAssembler a = withTurns();
		a.accept(seg("build me a", 1000, 2000));
		a.turn("turn 0.10 in 30 ms", false);
		a.accept(seg("castle", 5200, 6000)); // 3.2 s later: started in the grace, while speech held the line
		assertEquals(1, lines.size());
		assertEquals("build me a", lines.get(0).text());
		assertEquals(List.of("turn 0.10 in 30 ms: waiting", "closed after 3.2 s of silence"), decisions);
	}

	@Test
	void theCapsHoldWhileTheModelWaits() {
		LineAssembler a = withTurns();
		for (int i = 0; i < 6; i++) {
			long start = i * 11_000L;
			a.accept(seg("segment " + i, start, start + 10_000));
			a.turn("turn 0.10 in 30 ms", false);
		}
		assertEquals(1, lines.size(), "closed the moment it held 60 s");
		assertFalse(a.isOpen());

		a.accept(seg(words(1400), 70_000, 105_000));
		a.turn("turn 0.10 in 30 ms", false);
		a.accept(seg(words(200), 106_000, 111_000));
		assertEquals(2, lines.size(), "closed the moment it held 1500 characters");
		a.turn("turn 0.99 in 30 ms", true); // no line is open to mark
		assertFalse(a.finished());
	}

	@Test
	void leavingOrDiscardingDropsTheMark() {
		LineAssembler a = withTurns();
		a.accept(seg("build me a castle", 1000, 2300));
		a.turn("turn 0.93 in 31 ms", true);
		a.closeNow();
		assertEquals(1, lines.size());
		a.accept(seg("make it day", 5000, 5900));
		assertFalse(a.finished(), "a new line starts unmarked");
		a.turn("turn 0.95 in 31 ms", true);
		a.discard();
		assertFalse(a.finished());
		a.tick(false, false);
		assertEquals(1, lines.size());
		assertTrue(decisions.isEmpty(), "leaving and muting are not the turn model's decisions");
	}

	@Test
	void withNoVerdictsTheLineEndsOnSilenceAsBefore() {
		LineAssembler five = new LineAssembler(5, now::get, lines::add, decisions::add);
		five.accept(seg("hello there", 1000, 2000));
		now.set(2000 + 5499);
		five.tick(false, false);
		assertTrue(lines.isEmpty());
		now.set(2000 + 5500);
		five.tick(false, false);
		assertEquals(7500, lines.get(0).closedAtMillis());
		assertEquals(List.of("closed after 5.5 s of silence"), decisions);
	}

	private static String words(int length) {
		StringBuilder b = new StringBuilder();
		int i = 0;
		while (b.length() < length) {
			if (b.length() > 0) {
				b.append(' ');
			}
			b.append("word").append(i++ % 10);
		}
		return b.substring(0, length).trim();
	}
}
