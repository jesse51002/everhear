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
		while (now.get() < t) {
			now.addAndGet(250);
			assembler.tick(false, false);
		}
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
