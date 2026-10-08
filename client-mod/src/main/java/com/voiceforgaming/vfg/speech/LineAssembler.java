package com.voiceforgaming.vfg.speech;

import java.util.ArrayList;
import java.util.List;
import java.util.function.Consumer;
import java.util.function.LongSupplier;

/**
 * Turns transcribed speech segments into lines.
 *
 * <p>A segment that starts within {@code silenceSeconds} of the open line's end joins it,
 * so a shorter pause never splits a line. {@link #tick} closes the open line once no
 * speech is in progress, no segment waits to be transcribed, and
 * {@code now >= ended_at + silenceSeconds + 0.5 s}. A line also closes early at 60 s of
 * speech or 1500 characters. {@code ended_at} is always the end of the last speech, not
 * of the silence after it.
 *
 * <p>Owned by one thread ({@code vfg-stt}); not thread-safe.
 */
public final class LineAssembler {
	public static final int SAMPLE_RATE = 16000;
	public static final long GRACE_MILLIS = 500;
	public static final long MAX_SPEECH_SAMPLES = 60L * SAMPLE_RATE;
	public static final int MAX_LINE_CHARS = 1500;
	public static final int MAX_TEXT_CHARS = 2000;
	public static final double MAX_CHARS_PER_SECOND = 40.0;

	/** One transcribed segment and its audio statistics. */
	public record Heard(String text, long startMillis, long endMillis, float peak, double sumOfSquares,
			long sampleCount) {}

	/** A closed line, ready to become a §2 body; {@code segments} is how many segments it holds. */
	public record Line(String text, long startedAtMillis, long endedAtMillis, double peakDbfs, double meanDbfs,
			long closedAtMillis, int segments) {}

	/** Why a segment was not used. */
	public enum Rejection { NONE, NO_LETTER_OR_DIGIT, TOO_FAST }

	private final long silenceMillis;
	private final LongSupplier clock;
	private final Consumer<Line> sink;

	private final List<String> texts = new ArrayList<>();
	private int textLength;
	private long startedAt;
	private long endedAt;
	private float peak;
	private double sumOfSquares;
	private long samples;

	public LineAssembler(double silenceSeconds, LongSupplier clock, Consumer<Line> sink) {
		this.silenceMillis = Math.round(silenceSeconds * 1000.0);
		this.clock = clock;
		this.sink = sink;
	}

	public boolean isOpen() {
		return !texts.isEmpty();
	}

	public long endedAtMillis() {
		return endedAt;
	}

	/** Trims and collapses whitespace. */
	public static String clean(String raw) {
		if (raw == null) {
			return "";
		}
		return raw.trim().replaceAll("\\s+", " ");
	}

	/** Whether a cleaned segment text is usable, and if not, why. */
	public static Rejection check(String cleaned, long sampleCount) {
		boolean hasLetterOrDigit = cleaned.codePoints().anyMatch(Character::isLetterOrDigit);
		if (!hasLetterOrDigit) {
			return Rejection.NO_LETTER_OR_DIGIT;
		}
		double seconds = sampleCount / (double) SAMPLE_RATE;
		if (seconds <= 0 || cleaned.length() / seconds > MAX_CHARS_PER_SECOND) {
			return Rejection.TOO_FAST;
		}
		return Rejection.NONE;
	}

	/**
	 * Adds a segment: it joins the open line, or opens one. Returns why it was dropped,
	 * or {@link Rejection#NONE} when it was used.
	 */
	public Rejection accept(Heard heard) {
		String text = clean(heard.text());
		Rejection why = check(text, heard.sampleCount());
		if (why != Rejection.NONE) {
			return why;
		}
		if (isOpen() && heard.startMillis() - endedAt > silenceMillis) {
			close();
		}
		if (!isOpen()) {
			startedAt = heard.startMillis();
			endedAt = heard.endMillis();
			peak = 0f;
			sumOfSquares = 0;
			samples = 0;
			textLength = -1;
		}
		texts.add(text);
		textLength += 1 + text.length();
		endedAt = Math.max(endedAt, heard.endMillis());
		peak = Math.max(peak, heard.peak());
		sumOfSquares += heard.sumOfSquares();
		samples += heard.sampleCount();
		if (samples >= MAX_SPEECH_SAMPLES || textLength >= MAX_LINE_CHARS) {
			close();
		}
		return Rejection.NONE;
	}

	/** Closes the open line once the silence after it has run out. */
	public void tick(boolean speechInProgress, boolean segmentsPending) {
		if (!isOpen() || speechInProgress || segmentsPending) {
			return;
		}
		if (clock.getAsLong() >= endedAt + silenceMillis + GRACE_MILLIS) {
			close();
		}
	}

	/** Closes the open line now, without waiting out the silence (the player left). */
	public void closeNow() {
		if (isOpen()) {
			close();
		}
	}

	/** Drops the open line (muted, or settings stopped listening). */
	public void discard() {
		texts.clear();
		textLength = 0;
	}

	private void close() {
		String text = cut(String.join(" ", texts).trim(), MAX_TEXT_CHARS);
		Line line = new Line(text, startedAt, endedAt, Levels.peakDbfs(peak),
			Levels.meanDbfs(sumOfSquares, samples), clock.getAsLong(), texts.size());
		discard();
		if (!text.isEmpty()) {
			sink.accept(line);
		}
	}

	/** Cuts {@code text} to at most {@code max} characters at the last space; a hard cut if there is none. */
	public static String cut(String text, int max) {
		if (text.length() <= max) {
			return text;
		}
		int space = text.lastIndexOf(' ', max);
		if (space > 0) {
			return text.substring(0, space).trim();
		}
		int end = max;
		if (Character.isHighSurrogate(text.charAt(end - 1))) {
			end--;
		}
		return text.substring(0, end);
	}
}
