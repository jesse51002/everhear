package com.voiceforgaming.vfg.speech;

import com.voiceforgaming.vfg.turn.TurnDetector;
import java.util.Locale;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.function.BooleanSupplier;
import java.util.function.Consumer;
import java.util.function.Supplier;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * The {@code vfg-stt} thread: transcribes segments in order, asks the turn model at each
 * pause whether the line is finished, owns the {@link LineAssembler}, and ticks it every
 * 250 ms and after every item. Control items travel in the same queue as segments, so
 * "the player left" is handled only after every segment captured before it.
 *
 * <p>The turn model scores a segment once it is transcribed and used, if no speech is in
 * progress and the VAD did not cut it mid-speech, on the line's audio from 0.5 s before
 * its first speech ({@link Segment#turnAudio}). The tick right after closes the line if the
 * score is at or above the threshold. A score that throws, or takes longer than 500 ms,
 * counts as not finished, so the silence closes the line; either is logged as a warning in
 * place of the decision's line: {@code turn 0.97 in 612 ms, over 500 ms: waiting}.
 */
public final class SttWorker implements Runnable {
	private static final Logger LOG = LoggerFactory.getLogger("vfg");
	public static final long TICK_MILLIS = 250;
	/** A score that takes longer than this counts as not finished. */
	public static final long TURN_LIMIT_MILLIS = 500;

	/** What the capture thread sends. */
	public sealed interface Item permits SegmentItem, Control {}

	public record SegmentItem(Segment segment) implements Item {}

	public enum Control implements Item {
		/** Drop the open line (muted, settings). */
		DISCARD,
		/** Close the open line now (left the server). */
		CLOSE_NOW
	}

	private final LinkedBlockingQueue<Item> queue = new LinkedBlockingQueue<>();
	private final AtomicInteger pendingSegments = new AtomicInteger();
	private final Supplier<Transcriber> transcriber;
	private final Supplier<TurnDetector> turn;
	private final Double turnThreshold;
	private final LineAssembler assembler;
	private final BooleanSupplier speechInProgress;
	private final Consumer<String> heard;
	private final BooleanSupplier logText;
	private volatile boolean lineOpen;
	private volatile long openLineEndedAt;
	private volatile boolean stopped;
	private long lineFirst; // the open line's first sample, in the capture's count

	/**
	 * @param transcriber speech-to-text, or {@code null} while the engine is not loaded
	 * @param turn the turn model, or {@code null} while none is loaded; always with {@code turn_model: off}
	 * @param turnThreshold the score at or above which a line is finished, or {@code null} for the model's own
	 */
	public SttWorker(Supplier<Transcriber> transcriber, Supplier<TurnDetector> turn, Double turnThreshold,
			LineAssembler assembler, BooleanSupplier speechInProgress, Consumer<String> heard,
			BooleanSupplier logText) {
		this.transcriber = transcriber;
		this.turn = turn;
		this.turnThreshold = turnThreshold;
		this.assembler = assembler;
		this.speechInProgress = speechInProgress;
		this.heard = heard;
		this.logText = logText;
	}

	public void submit(Item item) {
		if (item instanceof SegmentItem) {
			pendingSegments.incrementAndGet();
		}
		queue.add(item);
	}

	public boolean lineOpen() {
		return lineOpen;
	}

	/** The end of the open line's last speech (wall-clock ms); meaningful while {@link #lineOpen()}. */
	public long openLineEndedAt() {
		return openLineEndedAt;
	}

	public int pendingSegments() {
		return pendingSegments.get();
	}

	public void stop() {
		stopped = true;
	}

	@Override
	public void run() {
		while (!stopped) {
			try {
				step(TICK_MILLIS);
			} catch (InterruptedException e) {
				return;
			} catch (RuntimeException e) {
				LOG.warn("speech-to-text worker: {}", e.toString());
			}
		}
	}

	/** Handles at most one item (waiting up to {@code waitMillis} for it), then ticks the assembler. */
	public void step(long waitMillis) throws InterruptedException {
		Item item = waitMillis > 0 ? queue.poll(waitMillis, TimeUnit.MILLISECONDS) : queue.poll();
		if (item != null) {
			handle(item);
		}
		assembler.tick(speechInProgress.getAsBoolean(), pendingSegments.get() > 0);
		publish();
	}

	/** Handles everything queued now, without waiting; for tests and shutdown. */
	public void drainNow() throws InterruptedException {
		while (!queue.isEmpty()) {
			step(0);
		}
	}

	private void handle(Item item) {
		switch (item) {
			case SegmentItem(Segment segment) -> {
				try {
					transcribeInto(segment);
				} finally {
					pendingSegments.decrementAndGet();
				}
			}
			case Control c -> {
				if (c == Control.DISCARD) {
					assembler.discard();
				} else {
					assembler.closeNow();
				}
			}
		}
		publish();
	}

	private void publish() {
		openLineEndedAt = assembler.endedAtMillis();
		lineOpen = assembler.isOpen();
	}

	private void transcribeInto(Segment segment) {
		Transcriber t = transcriber.get();
		if (t == null) {
			return;
		}
		String raw;
		try {
			raw = t.transcribe(segment.samples());
		} catch (RuntimeException e) {
			LOG.warn("could not transcribe a {} s segment: {}", String.format("%.1f", segment.seconds()), e.toString());
			return;
		}
		String text = LineAssembler.clean(raw);
		if (!text.isEmpty()) {
			heard.accept(text);
		}
		if (logText.getAsBoolean()) {
			LOG.info("heard ({} s): {}", String.format("%.1f", segment.seconds()), text);
		}
		if (!segment.forLine()) {
			return;
		}
		LineAssembler.Rejection why = assembler.accept(new LineAssembler.Heard(text, segment.startMillis(),
			segment.endMillis(), segment.peak(), segment.sumOfSquares(), segment.samples().length));
		if (why != LineAssembler.Rejection.NONE) {
			LOG.debug("dropped a {} s segment: {}", String.format("%.1f", segment.seconds()), why);
			return;
		}
		if (assembler.segments() == 1) {
			lineFirst = segment.first();
		}
		scoreTurn(segment);
	}

	/** Asks the turn model whether the open line is finished, at the pause after {@code segment}. */
	private void scoreTurn(Segment segment) {
		TurnDetector model = turn.get();
		if (model == null || segment.cut() || segment.audio() == null || !assembler.isOpen()
				|| speechInProgress.getAsBoolean()) {
			return;
		}
		float[] audio = segment.turnAudio(lineFirst);
		long t0 = System.nanoTime();
		float score;
		try {
			score = model.score(audio);
		} catch (RuntimeException | LinkageError e) {
			// the line stays as it is, not finished; this warning is the decision's one line
			LOG.warn("turn failed in {} ms ({}): waiting", (System.nanoTime() - t0) / 1_000_000, e.toString());
			return;
		}
		long millis = (System.nanoTime() - t0) / 1_000_000;
		String words = String.format(Locale.ROOT, "turn %.2f in %d ms", score, millis);
		if (millis > TURN_LIMIT_MILLIS) {
			LOG.warn("{}, over {} ms: waiting", words, TURN_LIMIT_MILLIS);
			return;
		}
		double threshold = turnThreshold != null ? turnThreshold : model.defaultThreshold();
		assembler.turn(words, score >= threshold);
	}
}
