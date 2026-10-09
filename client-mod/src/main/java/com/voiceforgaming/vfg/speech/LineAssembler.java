package com.voiceforgaming.vfg.speech;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.function.Consumer;
import java.util.function.LongSupplier;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Turns transcribed speech segments into lines.
 *
 * <p>A segment that starts within {@code silenceSeconds} of the open line's end joins it,
 * so a shorter pause never splits a line. At each pause the turn model's verdict on the
 * line comes in through {@link #turn}: a line it heard finished is marked, and
 * {@link #tick} closes it as soon as no speech is in progress and no segment waits to be
 * transcribed. Speech starting again clears the mark, and so does a segment joining the
 * line, which is scored at its own pause. A line that is not finished closes once no
 * speech is in progress, no segment waits, and
 * {@code now >= ended_at + silenceSeconds + 0.5 s}. A line also closes early at 60 s of
 * speech or 1500 characters. {@code ended_at} is always the end of the last speech, not
 * of the silence after it.
 *
 * <p>Each decision is logged at INFO, never with the text: {@code turn 0.93 in 31 ms: closed},
 * {@code turn 0.18 in 29 ms: waiting}, {@code closed after 3.6 s of silence}. A score that
 * failed never reaches here; {@link SttWorker} logs it.
 *
 * <p>Owned by one thread ({@code vfg-stt}); not thread-safe.
 */
public final class LineAssembler {
	private static final Logger LOG = LoggerFactory.getLogger("vfg");
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
	private final Consumer<String> decisions;

	private final List<String> texts = new ArrayList<>();
	private int textLength;
	private long startedAt;
	private long endedAt;
	private float peak;
	private double sumOfSquares;
	private long samples;
	private boolean finished;
	private String verdict; // the turn model's words that marked the open line finished

	public LineAssembler(double silenceSeconds, LongSupplier clock, Consumer<Line> sink) {
		this(silenceSeconds, clock, sink, decision -> {});
	}

	/** {@code decisions} also gets the words of each decision, as they are logged. */
	public LineAssembler(double silenceSeconds, LongSupplier clock, Consumer<Line> sink, Consumer<String> decisions) {
		this.silenceMillis = Math.round(silenceSeconds * 1000.0);
		this.clock = clock;
		this.sink = sink;
		this.decisions = decisions;
	}

	public boolean isOpen() {
		return !texts.isEmpty();
	}

	/** How many segments the open line holds. */
	public int segments() {
		return texts.size();
	}

	/** Whether the turn model heard the open line finished, and no speech has come since. */
	public boolean finished() {
		return finished;
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
	 * Adds a segment: it joins the open line, clearing the turn model's mark, or opens one.
	 * Returns why it was dropped, or {@link Rejection#NONE} when it was used.
	 */
	public Rejection accept(Heard heard) {
		String text = clean(heard.text());
		Rejection why = check(text, heard.sampleCount());
		if (why != Rejection.NONE) {
			return why;
		}
		if (isOpen() && heard.startMillis() - endedAt > silenceMillis) {
			decide(finished ? verdict + ": closed" : silence(heard.startMillis() - endedAt));
			close();
		}
		if (isOpen()) {
			unmark();
		} else {
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

	/**
	 * The turn model's verdict on the open line, at the pause after the segment that just
	 * joined or opened it. {@code verdict} is its words, such as {@code turn 0.93 in 31 ms}.
	 * A finished line is marked, and closes at the next {@link #tick} that finds no speech in
	 * progress and nothing waiting; one that is not finished waits for more speech, or for
	 * the silence to run out.
	 */
	public void turn(String verdict, boolean finished) {
		if (!isOpen()) {
			return;
		}
		this.finished = finished;
		this.verdict = finished ? verdict : null;
		if (!finished) {
			decide(verdict + ": waiting");
		}
	}

	/**
	 * Closes the open line once no speech is in progress and no segment waits: at once if
	 * the turn model heard it finished, otherwise once the silence after it has run out.
	 * Speech in progress clears the turn model's mark.
	 */
	public void tick(boolean speechInProgress, boolean segmentsPending) {
		if (!isOpen()) {
			return;
		}
		if (speechInProgress) {
			unmark();
			return;
		}
		if (segmentsPending) {
			return;
		}
		long now = clock.getAsLong();
		if (finished) {
			decide(verdict + ": closed");
			close();
		} else if (now >= endedAt + silenceMillis + GRACE_MILLIS) {
			decide(silence(now - endedAt));
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
		finished = false;
		verdict = null;
	}

	/** More speech after the turn model heard the line finished: the line goes on. */
	private void unmark() {
		if (finished) {
			decide(verdict + ": waiting, speech again");
			finished = false;
			verdict = null;
		}
	}

	private static String silence(long millis) {
		return String.format(Locale.ROOT, "closed after %.1f s of silence", millis / 1000.0);
	}

	private void decide(String decision) {
		LOG.info("{}", decision);
		decisions.accept(decision);
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
