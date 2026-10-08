package com.voiceforgaming.vfg.speech;

import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.function.BooleanSupplier;
import java.util.function.Consumer;
import java.util.function.Supplier;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * The {@code vfg-stt} thread: transcribes segments in order, owns the
 * {@link LineAssembler}, and ticks its silence timer every 250 ms. Control items
 * travel in the same queue as segments, so "the player left" is handled only after
 * every segment captured before it.
 */
public final class SttWorker implements Runnable {
	private static final Logger LOG = LoggerFactory.getLogger("vfg");
	public static final long TICK_MILLIS = 250;

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
	private final LineAssembler assembler;
	private final BooleanSupplier speechInProgress;
	private final Consumer<String> heard;
	private final BooleanSupplier logText;
	private volatile boolean lineOpen;
	private volatile long openLineEndedAt;
	private volatile boolean stopped;

	public SttWorker(Supplier<Transcriber> transcriber, LineAssembler assembler, BooleanSupplier speechInProgress,
			Consumer<String> heard, BooleanSupplier logText) {
		this.transcriber = transcriber;
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

	/** Handles at most one item (waiting up to {@code waitMillis} for it), then ticks the silence timer. */
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
		}
	}
}
