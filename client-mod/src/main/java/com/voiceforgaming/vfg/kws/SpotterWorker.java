package com.voiceforgaming.vfg.kws;

import com.voiceforgaming.vfg.VoicePipeline;
import com.voiceforgaming.vfg.audio.WindowListener.StopReason;
import com.voiceforgaming.vfg.speech.SampleClock;
import java.util.ArrayDeque;
import java.util.List;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Consumer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * The {@code vfg-kws} thread: the keyword spotter, a pipeline of its own beside the sentence
 * pipeline. The capture thread hands it a copy of every 32 ms window, with whether the VAD hears
 * speech, by a non-blocking offer into a queue of 2 s; a full queue drops the window, so neither
 * the capture thread nor speech-to-text ever waits on it. It listens only while capturing for a
 * line, with the spotter on, the model loaded, and at least one incantation.
 *
 * <p><b>Streams follow speech.</b> A stream opens when the VAD hears speech, fed first the last
 * 0.64 s of audio, so the start of the speech the VAD needed to hear is in it, and closes once the
 * VAD has heard none for 0.32 s more (about 0.9 s after the last word, by when a keyword at the
 * end has fired). Silence is never decoded, so the spotter costs nothing while nobody speaks, and
 * no stream sits in 1.5 s of silence, where sherpa-onnx resets it itself and drops the start of a
 * keyword said at that moment (issue #3990, about one keyword in ten after a pause).
 *
 * <p><b>A hit</b> maps its tag back to the incantation's phrase ({@link Keywords}), and its
 * tokens' frames to instants on the same audio clock a line uses: a {@link SampleClock} that
 * records the same reads, at the same wall times, as the capture's, so a sample has the same
 * instant in both. A token's frame counts from the stream's start or its last reset; the reset
 * after each hit is the spotter's own, and one sherpa-onnx makes in silence is found from the
 * chunk the keyword fired in. A token comes out a little after its sound, so {@code started_at} is
 * the first token less {@link #START_LEAD_MILLIS} and {@code ended_at} the end of the last token's
 * frame less {@link #END_LAG_MILLIS} (on Kokoro speech both then land within about 0.05 s of where
 * the phrase's sound starts and ends). Then the stream is reset, as sherpa-onnx requires after a hit.
 *
 * <p><b>A new list</b> rebuilds the keywords; an open stream is replaced at once by one with the
 * new keywords, fed the last 0.64 s again (from after the last hit), so a phrase being said is
 * not lost.
 */
public final class SpotterWorker implements VoicePipeline.Tap {
	private static final Logger LOG = LoggerFactory.getLogger("vfg");

	/** The queue from the capture thread: 2 s of windows. */
	public static final int QUEUE_WINDOWS = 64;
	/** The audio a new stream is fed first: 0.64 s. */
	public static final int PRE_ROLL_WINDOWS = 20;
	/** Windows without speech, once the VAD has stopped hearing it, after which a stream closes: 0.32 s. */
	public static final int QUIET_WINDOWS = 10;
	/** How much earlier than its first token a keyword's sound starts. */
	public static final long START_LEAD_MILLIS = 240;
	/** How much earlier than the end of its last token's frame a keyword's sound ends. */
	public static final long END_LAG_MILLIS = 100;
	private static final long POLL_MILLIS = 250;

	/**
	 * A keyword heard: the incantation's phrase, the keyword the spotter matched, when it was said
	 * (on the audio clock) and the instant of the newest sample the spotter had when it fired.
	 */
	public record Cast(String phrase, String heard, long startedAtMillis, long endedAtMillis, long heardAtMillis) {}

	/** One window from the capture thread; {@code session} counts captures, {@code seq} windows within one. */
	public record Window(float[] samples, long wallMillis, boolean speaking, int session, long seq) {}

	private record Settings(boolean on, double threshold, double boost, List<Incantation> incantations) {}

	private final ArrayBlockingQueue<Window> queue = new ArrayBlockingQueue<>(QUEUE_WINDOWS);
	private final Consumer<Cast> casts;
	private final AtomicLong dropped = new AtomicLong();
	private final AtomicReference<KeywordModel> offered = new AtomicReference<>();
	private volatile Settings settings;
	private volatile boolean modelReady;
	private volatile boolean forLine;
	private volatile boolean stopped;
	private Thread thread;

	// capture thread
	private int session;
	private long seq;

	// vfg-kws
	private KeywordModel model;
	private Settings built;
	private Keywords keywords = Keywords.NONE;
	private KeywordModel.Stream stream;
	private final SampleClock clock = new SampleClock();
	private final ArrayDeque<Window> ring = new ArrayDeque<>(PRE_ROLL_WINDOWS + 1);
	private int at = -1;
	private long nextSeq;
	private long decodes;
	private long resetAt;
	private int quiet;
	private long lastHitEnd = Long.MIN_VALUE;
	private boolean warnedDropping;

	/** {@code casts} is called on {@code vfg-kws} and must return at once. */
	public SpotterWorker(boolean on, double threshold, double boost, Consumer<Cast> casts) {
		this.casts = casts;
		this.settings = new Settings(on, threshold, boost, List.of());
	}

	// --- from any thread ---

	/** The player's incantations, from the bridge; an empty list stops the spotter. */
	public void setIncantations(List<Incantation> incantations) {
		Settings s = settings;
		settings = new Settings(s.on(), s.threshold(), s.boost(), List.copyOf(incantations));
	}

	/** The config's {@code incantations}, {@code incantation_threshold} and {@code incantation_boost}. */
	public void configure(boolean on, double threshold, double boost) {
		Settings s = settings;
		settings = new Settings(on, threshold, boost, s.incantations());
	}

	/** The loaded model; the worker owns it from now on, and closes the one it replaces. */
	public void setModel(KeywordModel loaded) {
		KeywordModel old = offered.getAndSet(loaded);
		if (old != null) {
			old.close(); // offered and never picked up
		}
		modelReady = loaded != null;
	}

	/** Whether a model has been offered. */
	public boolean hasModel() {
		return modelReady;
	}

	/** Whether windows are being queued now. */
	public boolean listening() {
		Settings s = settings;
		return forLine && s.on() && modelReady && !s.incantations().isEmpty();
	}

	/** Windows dropped because the queue was full. */
	public long dropped() {
		return dropped.get();
	}

	public synchronized void start() {
		if (thread == null) {
			thread = new Thread(this::run, "vfg-kws");
			thread.setDaemon(true);
			thread.start();
		}
	}

	/** Stops {@code vfg-kws}, waiting up to {@code joinMillis}, and releases the stream and the model. */
	public void shutdown(long joinMillis) {
		stopped = true;
		Thread t;
		synchronized (this) {
			t = thread;
		}
		if (t != null) {
			t.interrupt();
			try {
				t.join(joinMillis);
			} catch (InterruptedException e) {
				Thread.currentThread().interrupt();
			}
		}
		if (t == null || !t.isAlive()) {
			release();
		}
	}

	// --- vfg-capture ---

	@Override
	public void onStarted(boolean forLine) {
		session++;
		seq = 0;
		this.forLine = forLine;
	}

	/** Never blocks: a copy of the window is offered, and dropped when the queue is full. */
	@Override
	public void onWindow(float[] window, long readWallMillis, boolean speaking) {
		long n = seq++;
		if (!listening()) {
			return;
		}
		if (!queue.offer(new Window(window.clone(), readWallMillis, speaking, session, n))) {
			dropped.incrementAndGet();
		}
	}

	@Override
	public void onStopped(StopReason reason) {
		forLine = false;
	}

	// --- vfg-kws ---

	private void run() {
		try {
			while (!stopped) {
				step(queue.poll(POLL_MILLIS, TimeUnit.MILLISECONDS));
			}
		} catch (InterruptedException e) {
			// stopping
		} catch (RuntimeException e) {
			LOG.warn("keyword spotter stopped: {}", e.toString());
		} finally {
			release();
		}
	}

	/** Handles every window queued now, without waiting; for tests and tools that run no {@code vfg-kws}. */
	public void drainNow() {
		Window w;
		while ((w = queue.poll()) != null) {
			step(w);
		}
	}

	/** Handles one window, or {@code null} after a wait with none; on {@code vfg-kws}, or a test's thread. */
	public void step(Window w) {
		refresh();
		if (w == null) {
			if (!forLine) {
				closeStream();
				ring.clear();
			}
			return;
		}
		if (w.session() != at || w.seq() != nextSeq) {
			closeStream(); // a new capture, or windows dropped: the audio is not continuous
			ring.clear();
			at = w.session();
			if (w.seq() != nextSeq && dropped.get() > 0 && !warnedDropping) {
				warnedDropping = true;
				LOG.warn("keyword spotter fell behind: {} windows dropped", dropped.get());
			}
		}
		nextSeq = w.seq() + 1;
		if (model == null || keywords.isEmpty()) {
			closeStream();
			return;
		}
		ring.addLast(w);
		if (ring.size() > PRE_ROLL_WINDOWS) {
			ring.removeFirst();
		}
		try {
			if (stream == null) {
				if (w.speaking()) {
					open();
				}
				return;
			}
			feed(w);
			quiet = w.speaking() ? 0 : quiet + 1;
			if (quiet >= QUIET_WINDOWS) {
				closeStream();
			}
		} catch (IllegalStateException e) {
			closeStream(); // the model was released under us
		}
	}

	/** Picks up a new model and a new list or settings, rebuilding the keywords and an open stream. */
	private void refresh() {
		KeywordModel next = offered.getAndSet(null);
		if (next != null) {
			closeStream();
			if (model != null) {
				model.close();
			}
			model = next;
			built = null;
		}
		Settings s = settings;
		if (s == built || model == null) {
			return;
		}
		built = s;
		Keywords before = keywords;
		keywords = s.on() ? Keywords.build(s.incantations(), model.pieces(), model.tokens(), s.threshold(), s.boost())
			: Keywords.NONE;
		if (keywords.spec().equals(before.spec())) {
			return;
		}
		LOG.info("keyword spotter: {} keywords for {} incantations{}", keywords.size(), s.incantations().size(),
			keywords.skipped().isEmpty() ? "" : "; left out, as the model has no tokens for them: " + keywords.skipped());
		if (stream != null) {
			closeStream();
			if (!keywords.isEmpty()) {
				open();
			}
		}
	}

	/** Opens a stream with the keywords and feeds it the pre-roll, from after the last hit. */
	private void open() {
		try {
			stream = model.open(keywords.spec());
		} catch (IllegalArgumentException e) {
			LOG.warn("keyword spotter: {}; listening for none", e.getMessage());
			keywords = Keywords.NONE;
			return;
		}
		clock.reset();
		decodes = 0;
		resetAt = 0;
		quiet = 0;
		for (Window r : ring) {
			if (r.wallMillis() > lastHitEnd) {
				feed(r);
			}
		}
	}

	private void feed(Window w) {
		clock.recordRead(w.samples().length, w.wallMillis());
		stream.accept(w.samples());
		while (stream != null && stream.ready()) {
			stream.decode();
			decodes++;
			KeywordModel.Hit hit = stream.hit();
			if (hit != null) {
				hit(hit);
				stream.reset();
				resetAt = decodes;
			}
		}
	}

	private void hit(KeywordModel.Hit hit) {
		Keywords.Target target = keywords.target(hit.keyword());
		float[] ts = hit.timestamps();
		if (target == null || ts == null || ts.length == 0) {
			LOG.debug("keyword spotter: a hit on an unknown keyword");
			return;
		}
		long first = frame(ts[0]);
		long last = frame(ts[ts.length - 1]);
		long offset = frameOffset(resetAt, decodes, last, model.chunkFrames(), model.trailingBlanks());
		long startSample = Math.max(0, (offset + first) * Spotter.FRAME_SAMPLES - START_LEAD_MILLIS * 16);
		long endSample = (offset + last + 1) * Spotter.FRAME_SAMPLES - END_LAG_MILLIS * 16;
		long startedAt = clock.instantOf(startSample);
		long endedAt = Math.max(startedAt, clock.instantOf(endSample));
		lastHitEnd = endedAt;
		casts.accept(new Cast(target.phrase(), target.heard(), startedAt, endedAt,
			clock.instantOf(clock.totalSamples() - 1)));
	}

	/** A token time in seconds as an output frame of 40 ms. */
	static long frame(float seconds) {
		return Math.round(seconds * 25.0);
	}

	/**
	 * The stream's frame its hit's token frames count from: the chunk of its last reset, when
	 * {@code resetAt} chunks had been decoded. A hit fires {@code trailing} + 1 frames after its last
	 * token, in the chunk just decoded (the {@code decodes}th); when that puts it two chunks or more
	 * before that chunk, sherpa-onnx reset the stream itself since (after 1.5 s of blanks, between
	 * chunks), and the frame is found from the chunk it fired in instead.
	 */
	static long frameOffset(long resetAt, long decodes, long lastFrame, int chunk, int trailing) {
		long fireAfter = lastFrame + trailing + 1;
		long chunkStart = (decodes - 1) * chunk;
		long offset = resetAt * chunk;
		if (offset + fireAfter < chunkStart - 2L * chunk) {
			offset = chunkStart - chunk * Math.floorDiv(fireAfter, chunk);
		}
		return offset;
	}

	private void closeStream() {
		if (stream != null) {
			stream.close();
			stream = null;
		}
		quiet = 0;
	}

	private void release() {
		closeStream();
		KeywordModel pending = offered.getAndSet(null);
		if (pending != null) {
			pending.close();
		}
		if (model != null) {
			model.close();
			model = null;
		}
	}
}
