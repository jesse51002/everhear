package com.voiceforgaming.vfg.kws;

import com.voiceforgaming.vfg.VoicePipeline;
import com.voiceforgaming.vfg.audio.WindowListener.StopReason;
import com.voiceforgaming.vfg.speech.SampleClock;
import com.voiceforgaming.vfg.speech.SpeechEngine;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Consumer;
import java.util.function.LongConsumer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * The {@code vfg-kws} thread: the keyword spotter, a pipeline of its own beside the sentence
 * pipeline. The capture thread hands it a copy of every 32 ms window, with whether the capture's
 * VAD hears speech, by a non-blocking offer into a queue of 2 s; a full queue drops the window, so
 * neither the capture thread nor speech-to-text ever waits on it. It listens only while capturing
 * for a line, with the spotter on, the model loaded, and at least one incantation.
 *
 * <p><b>Speech, as the spotter hears it,</b> is its own VAD's ({@link SpotterVad}), which ends it
 * after 0.15 s of silence where the capture's waits 0.35 s; with none loaded it is the capture's
 * flag. Nothing is decoded while nobody speaks.
 *
 * <p><b>Two ways hear a phrase, and whichever hears it first casts it.</b>
 * <ul>
 * <li><b>The re-check.</b> While the player speaks, and until 0.15 s of silence after, the worker
 *     handles every window waiting in the queue and then checks the newest audio again, back to back:
 *     a fresh, throwaway stream with the current keywords is fed the player's last 3 s
 *     ({@code incantation_recheck_seconds}), then 0.5 s of zeros, and decoded to its first hit, which
 *     casts. The zeros stand for "the player stops right now", so the model decodes the end of what
 *     was said without waiting for audio not yet spoken, and a phrase casts as it ends. On Jesse's
 *     laptop one takes a median 12 ms on one thread, 40 ms at the 90th percentile (a whole 3 s of
 *     speech about 45 ms; the silence it leaves out makes most cheaper), so one runs after nearly
 *     every window and keeps about half a core (51–56%) busy while the player talks; on a slower PC
 *     they space themselves out instead of falling behind. A re-check never starts with more than
 *     {@link #LEAD_WINDOWS} of silence before speech, nor holds a silence of {@link #GAP_WINDOWS} or
 *     more: a stream resets itself after 1.5 s of blanks and loses what it had heard (sherpa-onnx
 *     issue #3990), which would drop the start of a phrase said just then.
 * <li><b>The live stream,</b> the backup: it never skips audio when the PC is too busy to re-check
 *     often, and with {@code incantation_recheck_seconds} at 0 it is the only way. A stream opens when
 *     speech starts, fed first the last 0.64 s of audio, so the start of the speech the VAD needed to
 *     hear is in it. When speech ends it is pushed 0.5 s of zeros, so its last 320 ms chunk decodes at
 *     once, and closed, so the zeros never shift its clock; the next speech opens a new one. No stream
 *     sits in the 1.5 s of silence after which sherpa-onnx resets it (issue #3990 again).
 * </ul>
 *
 * <p><b>A hit</b> maps its tag back to the incantation's phrase ({@link Keywords}), and its
 * tokens' frames to instants on the same audio clock a line uses: a {@link SampleClock} that
 * records the same reads, at the same wall times, as the capture's, so a sample has the same
 * instant in both. A token's frame counts from its stream's first sample or its last reset; the
 * reset after each hit is the spotter's own, and one sherpa-onnx makes in silence is found from the
 * chunk the keyword fired in. A token comes out a little after its sound, so {@code started_at} is
 * the first token less {@link #START_LEAD_MILLIS} and {@code ended_at} the end of the last token's
 * frame less {@link #END_LAG_MILLIS} (on Kokoro speech both then land within about 0.05 s of where
 * the phrase's sound starts and ends), neither before the stream's first sample nor after the newest
 * sample heard; a re-check that casts before the phrase's last sound is over ends it at the newest.
 *
 * <p><b>A keyword whose tokens start another's</b> ("fire" beside "fire ball" or "fireball") would
 * end the longer one: a re-check's zeros, or the gap between the words, finish it before the rest is
 * said, and a stream that hits it has lost the start of the longer one. So the re-checks while speech
 * goes on leave it out; one more re-check with every keyword runs when speech ends. And a hit on it,
 * from either way, is held: a cast whose sound starts before its end, within {@link #HOLD_MILLIS},
 * was the longer phrase and drops it; if none comes, it casts when the time is up or speech ends,
 * whichever is first. With re-checks off, the live stream alone hears the shorter one first.
 *
 * <p><b>One saying casts once.</b> Neither way hears audio from before the end of the last cast: a
 * re-check never reaches back past it, a re-check's cast closes the live stream, which the next
 * window reopens with its pre-roll from after that end, and the live stream resets itself after its
 * own hits. So whichever way hears a phrase first casts it, and the other never hears that saying.
 *
 * <p><b>A new list</b> rebuilds the keywords; an open stream is replaced at once by one with the
 * new keywords, fed the last 0.64 s again (from after the last cast), so a phrase being said is
 * not lost. The next re-check uses them.
 */
public final class SpotterWorker implements VoicePipeline.Tap {
	private static final Logger LOG = LoggerFactory.getLogger("vfg");

	/** The queue from the capture thread: 2 s of windows. */
	public static final int QUEUE_WINDOWS = 64;
	/** The audio a new live stream is fed first: 0.64 s. */
	public static final int PRE_ROLL_WINDOWS = 20;
	/** The zeros after a re-check's audio, and pushed into the live stream when speech ends: 0.5 s. */
	public static final int PUSH_SAMPLES = SampleClock.SAMPLE_RATE / 2;
	/** The most silence a re-check keeps before the speech it checks: 0.32 s. */
	public static final int LEAD_WINDOWS = 10;
	/** A silence inside a re-check's reach this long or longer, 1 s, ends what it reaches back to. */
	public static final int GAP_WINDOWS = 31;
	/** How much earlier than its first token a keyword's sound starts. */
	public static final long START_LEAD_MILLIS = 240;
	/** How much earlier than the end of its last token's frame a keyword's sound ends. */
	public static final long END_LAG_MILLIS = 100;
	/**
	 * How long a hit on a keyword that starts another ("fire", beside "fire ball") is held for the
	 * longer one, at most: Microsoft's speech engine waits 0.5 s after such a phrase. Speech ending
	 * sooner casts it sooner.
	 */
	public static final long HOLD_MILLIS = 500;
	private static final int SAMPLES_PER_MS = SampleClock.SAMPLE_RATE / 1000;
	private static final float[] ZEROS = new float[PUSH_SAMPLES];
	private static final long POLL_MILLIS = 250;

	/** Which way a phrase was heard. */
	public enum Via {
		RECHECK("the re-check"), STREAM("the stream");

		private final String words;

		Via(String words) {
			this.words = words;
		}

		/** As the cast's log line says it. */
		public String words() {
			return words;
		}
	}

	/**
	 * A keyword heard: the incantation's phrase, the keyword the spotter matched, when it was said
	 * (on the audio clock), the instant of the newest sample the spotter had when it fired, and
	 * which way heard it.
	 */
	public record Cast(String phrase, String heard, long startedAtMillis, long endedAtMillis, long heardAtMillis,
			Via via) {}

	/** One window from the capture thread; {@code session} counts captures, {@code seq} windows within one. */
	public record Window(float[] samples, long wallMillis, boolean speaking, int session, long seq) {}

	/** A window kept: its first sample on the worker's clock, its read's time, and whether the spotter hears speech. */
	private record Slot(float[] samples, long first, long wallMillis, boolean speaking) {
		long end() {
			return first + samples.length;
		}
	}

	/** A hit held because its keyword starts another, with when it was said and which way heard it. */
	private record Held(Keywords.Target target, long startedAt, long endedAt, Via via) {}

	private record Settings(boolean on, double threshold, double boost, double recheckSeconds,
			List<Incantation> incantations) {}

	private record Loaded(KeywordModel model, SpotterVad vad) {
		void close() {
			model.close();
			if (vad != null) {
				vad.close();
			}
		}
	}

	private final ArrayBlockingQueue<Window> queue = new ArrayBlockingQueue<>(QUEUE_WINDOWS);
	private final Consumer<Cast> casts;
	private final AtomicLong dropped = new AtomicLong();
	private final AtomicReference<Loaded> offered = new AtomicReference<>();
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
	private SpotterVad vad;
	private Settings built;
	private Keywords keywords = Keywords.NONE;
	private int recheckSamples;
	private int ringWindows = PRE_ROLL_WINDOWS;
	private int leadWindows = LEAD_WINDOWS;
	private LongConsumer recheckTimes;
	private final SampleClock clock = new SampleClock();
	private final List<Slot> ring = new ArrayList<>();
	private int at = -1;
	private long nextSeq;
	private boolean speaking;
	private long rechecked = -1;
	private KeywordModel.Stream stream;
	private long streamStart;
	private long decodes;
	private long resetAt;
	private long lastCastEnd = Long.MIN_VALUE;
	private Held held;
	private boolean speechEnded;
	private boolean warnedDropping;

	/**
	 * {@code recheckSeconds}: how far a re-check reaches back, 0 for none. {@code casts} is called on
	 * {@code vfg-kws} and must return at once.
	 */
	public SpotterWorker(boolean on, double threshold, double boost, double recheckSeconds, Consumer<Cast> casts) {
		this.casts = casts;
		this.settings = new Settings(on, threshold, boost, recheckSeconds, List.of());
	}

	// --- from any thread ---

	/** The player's incantations, from the bridge; an empty list stops the spotter. */
	public void setIncantations(List<Incantation> incantations) {
		Settings s = settings;
		settings = new Settings(s.on(), s.threshold(), s.boost(), s.recheckSeconds(), List.copyOf(incantations));
	}

	/**
	 * The config's {@code incantations}, {@code incantation_threshold}, {@code incantation_boost} and
	 * {@code incantation_recheck_seconds}.
	 */
	public void configure(boolean on, double threshold, double boost, double recheckSeconds) {
		Settings s = settings;
		settings = new Settings(on, threshold, boost, recheckSeconds, s.incantations());
	}

	/** The loaded model, with no VAD of the spotter's own; the worker owns it from now on. */
	public void setModel(KeywordModel loaded) {
		setModel(loaded, null);
	}

	/**
	 * The loaded model and the spotter's own VAD ({@code null}: the capture's flag); the worker owns
	 * both from now on, and closes those they replace.
	 */
	public void setModel(KeywordModel loaded, SpotterVad loadedVad) {
		Loaded old = offered.getAndSet(loaded == null ? null : new Loaded(loaded, loadedVad));
		if (old != null) {
			old.close(); // offered and never picked up
		}
		if (loaded == null && loadedVad != null) {
			loadedVad.close();
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

	/** Stops {@code vfg-kws}, waiting up to {@code joinMillis}, and releases the stream, the model and the VAD. */
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
				drainNow();
			}
		} catch (InterruptedException e) {
			// stopping
		} catch (RuntimeException e) {
			LOG.warn("keyword spotter stopped: {}", e.toString());
		} finally {
			release();
		}
	}

	/**
	 * Handles every window queued now, without waiting, then re-checks the newest audio once if the
	 * player is speaking: one turn of {@code vfg-kws}, and how tests and tools that run none drive it.
	 */
	public void drainNow() {
		Window w;
		while ((w = queue.poll()) != null) {
			step(w);
		}
		recheck(false);
		if (speechEnded && keywords.anyStartsAnother()) {
			recheck(true);
		}
		speechEnded = false;
		releaseHeld(!speaking);
	}

	/** Handles one window, or {@code null} after a wait with none; on {@code vfg-kws}, or a test's thread. */
	public void step(Window w) {
		refresh();
		if (w == null) {
			if (!forLine) {
				endAudio();
			}
			return;
		}
		if (w.session() != at || w.seq() != nextSeq) {
			endAudio(); // a new capture, or windows dropped: the audio is not continuous
			at = w.session();
			if (w.seq() != nextSeq && dropped.get() > 0 && !warnedDropping) {
				warnedDropping = true;
				LOG.warn("keyword spotter fell behind: {} windows dropped", dropped.get());
			}
		}
		nextSeq = w.seq() + 1;
		if (model == null || keywords.isEmpty()) {
			endAudio();
			return;
		}
		try {
			boolean was = speaking;
			speaking = vad != null ? vad.speaking(w.samples()) : w.speaking();
			speechEnded |= was && !speaking;
			Slot slot = new Slot(w.samples(), clock.recordRead(w.samples().length, w.wallMillis()), w.wallMillis(),
				speaking);
			ring.add(slot);
			trimRing();
			if (stream == null) {
				if (speaking) {
					open();
				}
				return;
			}
			feed(slot, slot.first());
			if (!speaking && stream != null) {
				push();
			}
			if (!speaking) {
				closeStream();
			}
		} catch (IllegalStateException e) {
			closeStream(); // the model was released under us
		}
	}

	/** Picks up a new model and a new list or settings, rebuilding the keywords and an open stream. */
	private void refresh() {
		Loaded next = offered.getAndSet(null);
		if (next != null) {
			endAudio();
			if (model != null) {
				model.close();
			}
			if (vad != null) {
				vad.close();
			}
			model = next.model();
			vad = next.vad();
			built = null;
		}
		Settings s = settings;
		if (s == built || model == null) {
			return;
		}
		built = s;
		recheckSamples = (int) Math.round(Math.max(0, s.recheckSeconds()) * SampleClock.SAMPLE_RATE);
		ringWindows = Math.max(PRE_ROLL_WINDOWS, (recheckSamples + SpeechEngine.WINDOW - 1) / SpeechEngine.WINDOW);
		trimRing();
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

	private void trimRing() {
		while (ring.size() > ringWindows) {
			ring.removeFirst();
		}
	}

	// --- the live stream ---

	/** Opens the live stream with the keywords and feeds it the pre-roll, from just after the last cast's end. */
	private void open() {
		stream = openStream(keywords.spec());
		if (stream == null) {
			return;
		}
		streamStart = -1;
		decodes = 0;
		resetAt = 0;
		long from = Math.max(ring.get(Math.max(0, ring.size() - PRE_ROLL_WINDOWS)).first(), afterLastCast());
		for (Slot r : ring) {
			if (r.end() > from) {
				feed(r, from);
			}
		}
	}

	/** A stream for {@code spec}, or {@code null} when the model refuses it (then it listens for none). */
	private KeywordModel.Stream openStream(String spec) {
		try {
			return model.open(spec);
		} catch (IllegalArgumentException e) {
			LOG.warn("keyword spotter: {}; listening for none", e.getMessage());
			keywords = Keywords.NONE;
			return null;
		}
	}

	/** Feeds the window's samples from sample {@code from} on. */
	private void feed(Slot r, long from) {
		if (stream == null) {
			return; // closed for a held hit cast at last; the next speech reopens it
		}
		if (streamStart < 0) {
			streamStart = Math.max(r.first(), from);
		}
		stream.accept(from(r, from));
		decodeStream();
	}

	/** A window's samples from sample {@code from} on: all of them, or a copy of its end. */
	private static float[] from(Slot r, long from) {
		return r.first() >= from ? r.samples()
			: Arrays.copyOfRange(r.samples(), (int) (from - r.first()), r.samples().length);
	}

	/** The end of speech: zeros, so the chunk holding its last sound decodes now rather than with the next speech. */
	private void push() {
		stream.accept(ZEROS);
		decodeStream();
	}

	private void decodeStream() {
		while (stream != null && stream.ready()) {
			stream.decode();
			decodes++;
			KeywordModel.Hit hit = stream.hit();
			if (hit != null) {
				Keywords.Target target = target(hit);
				if (target != null) {
					long first = frame(hit.timestamps()[0]);
					long last = frame(hit.timestamps()[hit.timestamps().length - 1]);
					long offset = frameOffset(resetAt, decodes, last, model.chunkFrames(), model.trailingBlanks());
					cast(target, streamStart + (offset + first) * Spotter.FRAME_SAMPLES,
						streamStart + (offset + last + 1) * Spotter.FRAME_SAMPLES, streamStart, Via.STREAM,
						keywords.startsAnother(hit.keyword()));
					if (stream == null) {
						return; // closed for a held hit cast at last
					}
				}
				stream.reset();
				resetAt = decodes;
			}
		}
	}

	private void closeStream() {
		if (stream != null) {
			stream.close();
			stream = null;
		}
	}

	// --- the re-check ---

	/** For measuring: how many windows of silence a re-check keeps before speech; -1 keeps them all. */
	void leadWindows(int windows) {
		leadWindows = windows;
	}

	/** For measuring: hears how long each re-check took, in nanoseconds. */
	void recheckTimes(LongConsumer times) {
		recheckTimes = times;
	}

	/**
	 * Re-checks the newest audio, when the player is speaking and audio has come since the last:
	 * a throwaway stream fed the last {@code incantation_recheck_seconds} from after the last cast,
	 * then 0.5 s of zeros, decoded to its first hit. It listens for the keywords that start no other,
	 * so "fire" cannot end "fire ball" in it; once speech has ended, one more listens for {@code every}
	 * keyword, so "fire" said alone is heard as soon as it can be.
	 */
	private void recheck(boolean every) {
		if (recheckSamples == 0 || !(speaking || every) || model == null || keywords.isEmpty() || ring.isEmpty()) {
			return;
		}
		Slot newest = ring.getLast();
		if (newest.end() == rechecked) {
			return;
		}
		rechecked = newest.end();
		long from = Math.max(Math.max(newest.end() - recheckSamples, ring.getFirst().first()), afterLastCast());
		if (from >= newest.end()) {
			return;
		}
		from = lessSilence(from);
		long t0 = System.nanoTime();
		try {
			KeywordModel.Stream s = openStream(every ? keywords.spec() : keywords.specStartingNone());
			if (s == null) {
				return;
			}
			try (s) {
				for (Slot r : ring) {
					if (r.end() > from) {
						s.accept(from(r, from));
					}
				}
				s.accept(ZEROS);
				long decoded = 0;
				long reset = 0;
				while (s.ready()) {
					s.decode();
					decoded++;
					KeywordModel.Hit hit = s.hit();
					if (hit == null) {
						continue;
					}
					Keywords.Target target = target(hit);
					if (target != null) {
						long first = frame(hit.timestamps()[0]);
						long last = frame(hit.timestamps()[hit.timestamps().length - 1]);
						long offset = frameOffset(reset, decoded, last, model.chunkFrames(), model.trailingBlanks());
						cast(target, from + (offset + first) * Spotter.FRAME_SAMPLES,
							from + (offset + last + 1) * Spotter.FRAME_SAMPLES, from, Via.RECHECK,
							keywords.startsAnother(hit.keyword()));
					}
					if (target != null && keywords.startsAnother(hit.keyword())) {
						s.reset(); // held: listen on, for another
						reset = decoded;
						continue;
					}
					break;
				}
			}
		} catch (IllegalStateException e) {
			// the model was released under us
		} finally {
			if (recheckTimes != null) {
				recheckTimes.accept(System.nanoTime() - t0);
			}
		}
	}

	/** The first sample kept after the end of the last cast: the reach-back rule. */
	private long afterLastCast() {
		if (lastCastEnd == Long.MIN_VALUE) {
			return 0;
		}
		for (Slot r : ring) {
			if (r.wallMillis() > lastCastEnd) {
				// a sample's instant is its read's time less the samples after it in the read
				long after = (r.wallMillis() - lastCastEnd) * SAMPLES_PER_MS;
				return r.end() - Math.min(r.samples().length, after);
			}
		}
		return ring.getLast().end();
	}

	/**
	 * Where a re-check reaching back to {@code from} starts, so that it neither starts with more than
	 * {@link #LEAD_WINDOWS} of silence before speech nor holds a silence of {@link #GAP_WINDOWS} or
	 * more: {@link #LEAD_WINDOWS} before the speech after the last such silence.
	 */
	private long lessSilence(long from) {
		if (leadWindows < 0) {
			return from;
		}
		int start = 0;
		while (ring.get(start).end() <= from) {
			start++;
		}
		int begin = start;
		int i = start;
		while (i < ring.size()) {
			if (ring.get(i).speaking()) {
				i++;
				continue;
			}
			int silent = i;
			while (i < ring.size() && !ring.get(i).speaking()) {
				i++;
			}
			int run = i - silent;
			if (i < ring.size() && (run >= GAP_WINDOWS || (silent == start && run > leadWindows))) {
				begin = i - leadWindows;
			}
		}
		return begin > start ? ring.get(begin).first() : from;
	}

	// --- a hit ---

	private Keywords.Target target(KeywordModel.Hit hit) {
		Keywords.Target target = keywords.target(hit.keyword());
		if (target == null || hit.timestamps() == null || hit.timestamps().length == 0) {
			LOG.debug("keyword spotter: a hit on an unknown keyword");
			return null;
		}
		return target;
	}

	/**
	 * Casts a hit whose first token is at sample {@code firstToken} and whose last token's frame ends
	 * at {@code lastFrameEnd}, heard by a stream whose audio starts at sample {@code streamFirst}; or,
	 * when its keyword {@code startsAnother}, holds it ({@link #HOLD_MILLIS}). A cast whose sound
	 * starts before the held one's ends drops it: the longer phrase was said.
	 */
	private void cast(Keywords.Target target, long firstToken, long lastFrameEnd, long streamFirst, Via via,
			boolean startsAnother) {
		long newest = clock.totalSamples() - 1;
		long startedAt = clock.instantOf(Math.min(newest, Math.max(streamFirst, firstToken - START_LEAD_MILLIS
			* SAMPLES_PER_MS)));
		long endedAt = Math.max(startedAt, clock.instantOf(Math.min(newest, lastFrameEnd - END_LAG_MILLIS
			* SAMPLES_PER_MS)));
		if (startedAt < lastCastEnd) {
			return; // a held hit cast late: this one's sound is in it
		}
		if (held != null && startedAt < held.endedAt()) {
			if (startsAnother) {
				return; // the same short saying again
			}
			held = null; // the longer phrase, or another, has its sound
		}
		if (startsAnother) {
			if (held != null) {
				castHeld();
			}
			held = new Held(target, startedAt, endedAt, via);
			return;
		}
		send(target, startedAt, endedAt, via);
	}

	/**
	 * Sends a cast. A re-check's closes the live stream, which has that saying in it too, to reopen
	 * from after its end; the live stream resets itself after its own hits.
	 */
	private void send(Keywords.Target target, long startedAt, long endedAt, Via via) {
		lastCastEnd = Math.max(lastCastEnd, endedAt);
		if (via == Via.RECHECK) {
			closeStream();
		}
		casts.accept(new Cast(target.phrase(), target.heard(), startedAt, endedAt,
			clock.instantOf(clock.totalSamples() - 1), via));
	}

	/** Casts the held hit when speech has ended or {@link #HOLD_MILLIS} have passed with no longer phrase. */
	private void releaseHeld(boolean speechEnded) {
		if (held != null && (speechEnded || clock.totalSamples() > 0
				&& clock.instantOf(clock.totalSamples() - 1) - held.endedAt() >= HOLD_MILLIS)) {
			castHeld();
		}
	}

	private void castHeld() {
		Held h = held;
		held = null;
		closeStream(); // a re-check's held hit is in the live stream's audio too
		send(h.target(), h.startedAt(), h.endedAt(), h.via());
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

	/**
	 * Audio that does not follow on: the stream closed, and the ring, the clock and the VAD start
	 * again; nothing to do when nothing has been heard since the last time.
	 */
	private void endAudio() {
		if (ring.isEmpty() && stream == null) {
			return; // every window heard goes to the ring, so the VAD has had none since it was reset
		}
		if (held != null) {
			castHeld();
		}
		closeStream();
		ring.clear();
		clock.reset();
		speaking = false;
		speechEnded = false;
		rechecked = -1;
		if (vad != null) {
			try {
				vad.reset();
			} catch (IllegalStateException e) {
				// released
			}
		}
	}

	private void release() {
		closeStream();
		Loaded pending = offered.getAndSet(null);
		if (pending != null) {
			pending.close();
		}
		if (model != null) {
			model.close();
			model = null;
		}
		if (vad != null) {
			vad.close();
			vad = null;
		}
	}
}
