package com.voiceforgaming.vfg.kws;

import com.voiceforgaming.vfg.VoicePipeline;
import com.voiceforgaming.vfg.audio.WindowListener.StopReason;
import com.voiceforgaming.vfg.speech.SampleClock;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Consumer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * The {@code vfg-vosk} thread: Vosk listening for the player's incantations beside the keyword
 * spotter, a speech recognizer whose grammar is their phrases among about 5,000 common English
 * words ({@link VoskGrammar}). It hears fast speech the spotter misses: on Kokoro TTS at 1.6 and
 * 2 times its speed, the two together heard 93 and 78% of the phrases, the spotter alone 84 and 58%.
 *
 * <p><b>Its audio</b> is what the spotter hears while someone speaks. The capture thread hands it a
 * copy of every window, as it does the spotter, by a non-blocking offer into a queue of 2 s, with
 * whether someone speaks as the spotter's VAD hears it ({@link SpeechTap}: speech ends after 0.15 s of
 * silence); given a VAD of its own, it asks that instead. When speech starts it is fed the last second it has not had yet, then every
 * window while speech goes on; when speech ends, the window that ended it and 0.3 s of zeros, which
 * let it decode the last words at once rather than with the next speech, and then the real audio for
 * {@link #TAIL_WINDOWS} more: Vosk's decoder runs about half a second behind, and fast speech's last
 * word often shows only then (with the zeros alone, the two engines heard 76% of the phrases said at
 * twice Kokoro's speed, with the tail 78%); it also lets Vosk's own endpointing end the utterance at
 * the pause, as it does when fed everything. Nothing more is fed in a longer silence.
 *
 * <p><b>A cast</b> comes from Vosk's partial text, read after every window: a phrase there as whole
 * words is a saying, each saying cast once however the text is revised, and a phrase said twice
 * casts twice ({@link PhraseMatcher}), as soon as a partial shows it. With {@code vosk_confirm} a
 * saying casts only once it holds: when the next partial that differs still shows it, or when speech
 * ends with it shown, or in the utterance's final result. A partial can show a phrase's words
 * together for a moment and take them back ("throw the ball into the fire" flickered "fire ball");
 * one that does not hold is then dropped, and is new if it shows again. It is off by default: on the
 * research's takes it took a median 0.26 s more for a cast Vosk heard first, and 3 points of the
 * phrases said at twice Kokoro's speed (Vosk takes back real sayings too), to save 2 of 4 of Vosk's
 * false casts in 39 min of real speech. Its window on the audio clock is estimated, as a partial has
 * no times: a partial shows a phrase a median 0.63 s after it starts (0.78 s at the 90th
 * percentile) and a median 0.02 s after it ends (0.21 s for two words, 0.42 s for one, at the 90th),
 * so the window starts {@link #LEAD_SAMPLES} of Vosk's audio before the newest sample it had, and
 * ends {@link #END_SAMPLES} before it: about where the phrase ends, a little early more often than
 * late, as a re-check's cast does. Ending at the newest sample instead, its end ran into a phrase
 * said right after, which the spotter then could not hear, as it never reaches back past the end of
 * the last cast. A saying
 * that shows only in an utterance's final result has its words' own times; one found more than
 * {@link #LATE_MILLIS} after its end (a final Vosk gives only with the next speech) is not cast: a
 * late spell is worse than none.
 *
 * <p><b>One saying casts once,</b> across both engines: each cast is claimed in the {@link CastGate}
 * it shares with the spotter first, and one the spotter claimed already (the same incantation, over
 * the same stretch of audio) is not posted. The spotter takes up Vosk's casts in turn: its reach-back
 * starts after them ({@link SpotterWorker}).
 *
 * <p><b>A new list</b> rebuilds the grammar and the recognizer, which are fed the last second again
 * if speech is going on. A phrase with a word Vosk does not know is left out, with a log line once
 * per phrase, and left to the spotter. Audio that does not follow on (a new capture, windows
 * dropped from a full queue) resets the recognizer, which goes on counting its times over every
 * sample it was ever fed, as the worker does.
 */
public final class VoskWorker implements VoicePipeline.Tap {
	private static final Logger LOG = LoggerFactory.getLogger("vfg");

	/** The queue from the capture thread: 2 s of windows. */
	public static final int QUEUE_WINDOWS = 64;
	/**
	 * The most audio from before speech started that Vosk is fed: 1 s. Vosk adapts to the voice and the
	 * room as it goes, and hears the first words better with more before them: at twice Kokoro's speed
	 * it heard 79% of the phrases with 1 s, 75% with 0.64 s, 72% with 0.32 s.
	 */
	public static final int PRE_ROLL_WINDOWS = 31;
	/** The zeros fed when speech ends: 0.3 s. */
	public static final int PUSH_SAMPLES = SampleClock.SAMPLE_RATE * 3 / 10;
	/** The real audio fed after speech ends, its zeros first: 1 s. */
	public static final int TAIL_WINDOWS = 31;
	/**
	 * How much of Vosk's audio before the newest sample a cast's window starts: 0.8 s. A partial shows
	 * a phrase 0.34 to 1.03 s after its first word starts, a median 0.63 s and 0.78 s at the 90th
	 * percentile (Kokoro, 330 phrases at 1 to 2 times its speed).
	 */
	public static final int LEAD_SAMPLES = SampleClock.SAMPLE_RATE * 8 / 10;
	/** How much of Vosk's audio before the newest sample a cast's window ends: 0.15 s. */
	public static final int END_SAMPLES = SampleClock.SAMPLE_RATE * 15 / 100;
	/** A phrase found this long after it ended is not cast: 1 s. */
	public static final long LATE_MILLIS = 1000;
	/** How much of what Vosk was fed is kept, to put its final results' times on the audio clock: 10 s. */
	private static final int KEEP_SAMPLES = SampleClock.SAMPLE_RATE * 10;
	private static final short[] ZEROS = new short[PUSH_SAMPLES];
	private static final long POLL_MILLIS = 250;

	/** One window from the capture thread; {@code session} counts captures, {@code seq} windows within one. */
	public record Window(float[] samples, long wallMillis, boolean speaking, int session, long seq) {}

	/** A window kept for the pre-roll: its first sample on the worker's clock, and whether its VAD hears speech. */
	private record Slot(float[] samples, long first, boolean speaking) {
		long end() {
			return first + samples.length;
		}
	}

	/**
	 * A stretch fed to the recognizer: from its sample {@code at}, {@code length} samples, those of the
	 * clock from {@code clockFirst}, or zeros ({@code clockFirst} -1).
	 */
	private record Fed(long at, long clockFirst, int length) {
		long end() {
			return at + length;
		}

		boolean real() {
			return clockFirst >= 0;
		}
	}

	private record Settings(boolean on, List<Incantation> incantations) {}

	/** A saying shown by a partial, waiting to hold, with the stretch of Vosk's audio its cast would cover. */
	private record Pending(PhraseMatcher.Saying saying, long from, long to) {}

	private record Loaded(VoskModel model, SpotterVad vad, List<String> fillers) {
		void close() {
			model.close();
			if (vad != null) {
				vad.close();
			}
		}
	}

	private final ArrayBlockingQueue<Window> queue = new ArrayBlockingQueue<>(QUEUE_WINDOWS);
	private final Consumer<SpotterWorker.Cast> casts;
	private final CastGate gate;
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

	// vfg-vosk
	private VoskModel model;
	private SpotterVad vad;
	private List<String> fillers = List.of();
	private Settings built;
	private VoskGrammar grammar = VoskGrammar.NONE;
	private VoskModel.Recognizer recognizer;
	private PhraseMatcher matcher;
	private final Set<String> warned = new HashSet<>();
	private final SampleClock clock = new SampleClock();
	private final List<Slot> ring = new ArrayList<>();
	private final ArrayDeque<Fed> fed = new ArrayDeque<>();
	private long fedSamples;
	private long fedUpTo;
	private String lastText = "";
	private final List<Pending> pending = new ArrayList<>();
	private volatile boolean confirm;
	private int at = -1;
	private long nextSeq;
	private boolean speaking;
	private int tail;
	private boolean warnedDropping;

	/** {@code casts} is called on {@code vfg-vosk} and must return at once; {@code gate} is the spotter's too. */
	public VoskWorker(boolean on, CastGate gate, Consumer<SpotterWorker.Cast> casts) {
		this.casts = casts;
		this.gate = gate;
		this.settings = new Settings(on, List.of());
	}

	// --- from any thread ---

	/** The player's incantations, from the bridge; an empty list stops it. */
	public void setIncantations(List<Incantation> incantations) {
		settings = new Settings(settings.on(), List.copyOf(incantations));
	}

	/** The config's {@code vosk}, with {@code incantations}: whether it listens. */
	public void configure(boolean on) {
		settings = new Settings(on, settings.incantations());
	}

	/**
	 * The loaded model, the VAD that says when someone speaks ({@code null}: the capture's flag), and
	 * the common words for its grammar; the worker owns the model and the VAD from now on, and closes
	 * those they replace.
	 */
	public void setModel(VoskModel loaded, SpotterVad loadedVad, List<String> fillerWords) {
		Loaded old = offered.getAndSet(loaded == null ? null : new Loaded(loaded, loadedVad, List.copyOf(fillerWords)));
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
			thread = new Thread(this::run, "vfg-vosk");
			thread.setDaemon(true);
			thread.start();
		}
	}

	/** Stops {@code vfg-vosk}, waiting up to {@code joinMillis}, and releases the recognizer, the model and the VAD. */
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

	// --- vfg-vosk ---

	private void run() {
		try {
			while (!stopped) {
				step(queue.poll(POLL_MILLIS, TimeUnit.MILLISECONDS));
				drainNow();
			}
		} catch (InterruptedException e) {
			// stopping
		} catch (RuntimeException e) {
			LOG.warn("vosk stopped: {}", e.toString());
		} finally {
			release();
		}
	}

	/** Handles every window queued now, without waiting: how tests and tools that run no thread drive it. */
	public void drainNow() {
		Window w;
		while ((w = queue.poll()) != null) {
			step(w);
		}
	}

	/** Handles one window, or {@code null} after a wait with none; on {@code vfg-vosk}, or a test's thread. */
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
				LOG.warn("vosk fell behind: {} windows dropped", dropped.get());
			}
		}
		nextSeq = w.seq() + 1;
		if (recognizer == null) {
			endAudio();
			return;
		}
		try {
			boolean was = speaking;
			speaking = vad != null ? vad.speaking(w.samples()) : w.speaking();
			Slot slot = new Slot(w.samples(), clock.recordRead(w.samples().length, w.wallMillis()), speaking);
			ring.add(slot);
			while (ring.size() > PRE_ROLL_WINDOWS + 1) {
				ring.removeFirst();
			}
			if (speaking) {
				for (Slot r : ring) {
					feed(r); // when speech starts, the pre-roll it has not had; then just this window
				}
				tail = TAIL_WINDOWS;
			} else if (was) {
				feed(slot);
				push();
				castPending(); // speech ended with them shown
			} else if (tail > 0) {
				tail--;
				feed(slot);
			}
		} catch (IllegalStateException e) {
			closeRecognizer(); // the model was released under us
		}
	}

	/** Picks up a new model and a new list or settings, rebuilding the grammar and the recognizer. */
	private void refresh() {
		Loaded next = offered.getAndSet(null);
		if (next != null) {
			closeRecognizer();
			endAudio();
			if (model != null) {
				model.close();
			}
			if (vad != null) {
				vad.close();
			}
			model = next.model();
			vad = next.vad();
			fillers = next.fillers();
			built = null;
			grammar = VoskGrammar.NONE;
		}
		Settings s = settings;
		if (s == built || model == null) {
			return;
		}
		built = s;
		VoskGrammar g = s.on() ? VoskGrammar.build(s.incantations(), model::knows, fillers) : VoskGrammar.NONE;
		for (VoskGrammar.LeftOut l : g.leftOut()) {
			if (warned.add(l.phrase())) {
				LOG.info("vosk: \"{}\" left out, as Vosk does not know {}; the keyword spotter alone listens for it",
					l.phrase(), String.join(", ", l.unknown()));
			}
		}
		if (g.json().equals(grammar.json())) {
			grammar = g;
			return;
		}
		grammar = g;
		closeRecognizer();
		if (g.isEmpty()) {
			endAudio();
			return;
		}
		if (!newRecognizer()) {
			return;
		}
		fedUpTo = 0;
		LOG.info("vosk: listening for {} of {} incantations among {} common words", g.phrases().size(),
			s.incantations().size(), g.fillerCount());
		if (speaking) {
			for (Slot r : ring) {
				feed(r); // a phrase being said is not lost
			}
		}
	}

	// --- feeding ---

	/** Feeds the window's samples it has not had yet, then reads what Vosk heard. */
	private void feed(Slot s) {
		if (recognizer == null || s.end() <= fedUpTo) {
			return;
		}
		int from = (int) Math.max(0, fedUpTo - s.first());
		short[] pcm = pcm(s.samples(), from);
		fed.addLast(new Fed(fedSamples, s.first() + from, pcm.length));
		fedSamples += pcm.length;
		fedUpTo = s.end();
		heard(recognizer.accept(pcm));
	}

	/** The end of speech: zeros, so the last words decode now rather than with the next speech. */
	private void push() {
		if (recognizer == null) {
			return;
		}
		fed.addLast(new Fed(fedSamples, -1, PUSH_SAMPLES));
		fedSamples += PUSH_SAMPLES;
		heard(recognizer.accept(ZEROS));
	}

	/** 16-bit samples from float ones, from sample {@code from} on. */
	static short[] pcm(float[] samples, int from) {
		short[] out = new short[samples.length - from];
		for (int i = 0; i < out.length; i++) {
			float v = samples[from + i] * 32767f;
			out[i] = (short) Math.max(-32768f, Math.min(32767f, v));
		}
		return out;
	}

	/** Reads Vosk's words after a piece: its partial, or the utterance's final when it ended one. */
	private void heard(boolean ended) {
		if (ended) {
			List<VoskModel.Word> words = recognizer.result();
			List<String> texts = words.stream().map(VoskModel.Word::word).toList();
			for (Pending p : pending) {
				PhraseMatcher.Saying s = matcher.find(texts, p.saying().phrase(), p.saying().nth());
				if (s != null) {
					castFinal(s, words);
				}
			}
			pending.clear();
			for (PhraseMatcher.Saying s : matcher.finish(texts)) {
				castFinal(s, words);
			}
			lastText = "";
		} else {
			String text = recognizer.partial();
			if (!text.equals(lastText)) {
				List<String> words = text.isBlank() ? List.of() : Arrays.asList(text.strip().split("\\s+"));
				settle(words);
				for (PhraseMatcher.Saying s : matcher.update(words)) {
					// a partial has no times: from LEAD_SAMPLES before the newest sample to END_SAMPLES before it.
					// Not from where Vosk said its utterance began: it says so as its decoder, half a second
					// behind, gets there, after the next words have started.
					Pending p = new Pending(s, fedSamples - LEAD_SAMPLES, fedSamples - END_SAMPLES);
					if (confirm) {
						pending.add(p);
					} else {
						cast(s, p.from(), p.to());
					}
				}
			}
			lastText = text;
		}
		while (fed.size() > 1 && fed.peekFirst().end() <= fedSamples - KEEP_SAMPLES) {
			fed.removeFirst();
		}
	}

	/** Settles the sayings waiting against a partial that differs: those it still shows cast, the rest are dropped. */
	private void settle(List<String> words) {
		for (Pending p : pending) {
			if (matcher.find(words, p.saying().phrase(), p.saying().nth()) != null) {
				cast(p.saying(), p.from(), p.to());
			} else {
				matcher.uncount(p.saying().phrase(), p.saying().nth());
			}
		}
		pending.clear();
	}

	/** Casts the sayings waiting: speech ended, and the newest partial shows them. */
	private void castPending() {
		for (Pending p : pending) {
			cast(p.saying(), p.from(), p.to());
		}
		pending.clear();
	}

	/** Casts a saying of a final result, with its words' own times, counted over all the recognizer was fed. */
	private void castFinal(PhraseMatcher.Saying s, List<VoskModel.Word> words) {
		cast(s, Math.round(words.get(s.first()).start() * SampleClock.SAMPLE_RATE),
			Math.round(words.get(s.end() - 1).end() * SampleClock.SAMPLE_RATE));
	}

	/** The config's {@code vosk_confirm}: whether a saying waits to hold before it casts. */
	public void confirmSayings(boolean on) {
		confirm = on;
	}

	/**
	 * Casts a saying Vosk found over its samples {@code from} to {@code to} (exclusive), unless it is
	 * late or the spotter cast it already.
	 */
	private void cast(PhraseMatcher.Saying s, long from, long to) {
		long end = realAtOrBefore(Math.max(from, to - 1));
		if (end < 0) {
			return;
		}
		long start = realAtOrAfter(from);
		if (start < 0 || start > end) {
			start = end;
		}
		long startedAt = clock.instantOf(start);
		long endedAt = clock.instantOf(end);
		long heardAt = clock.instantOf(clock.totalSamples() - 1);
		if (heardAt - endedAt > LATE_MILLIS) {
			LOG.debug("vosk: \"{}\" heard {} ms after it was said: not cast", s.phrase(), heardAt - endedAt);
			return;
		}
		if (!gate.claim(s.phrase(), startedAt, endedAt, SpotterWorker.Via.VOSK)) {
			LOG.debug("vosk: \"{}\" heard, cast already by the keyword spotter", s.phrase());
			return;
		}
		casts.accept(new SpotterWorker.Cast(s.phrase(), s.phrase(), startedAt, endedAt, heardAt,
			SpotterWorker.Via.VOSK));
	}

	/** The clock's sample fed at the recognizer's sample {@code pos}, or the last real one before it; -1 if none. */
	private long realAtOrBefore(long pos) {
		long best = -1;
		for (Fed f : fed) {
			if (f.at() > pos) {
				break;
			}
			if (f.real()) {
				best = f.clockFirst() + Math.min(pos - f.at(), f.length() - 1);
			}
		}
		return best;
	}

	/** The clock's sample fed at the recognizer's sample {@code pos}, or the first real one after it; -1 if none. */
	private long realAtOrAfter(long pos) {
		for (Fed f : fed) {
			if (f.end() > pos && f.real()) {
				return f.clockFirst() + Math.max(0, pos - f.at());
			}
		}
		return -1;
	}

	/** A recognizer for the grammar, with the audio it was fed counted from 0, as Vosk counts its times. */
	private boolean newRecognizer() {
		try {
			recognizer = model.recognizer(grammar.json());
		} catch (RuntimeException e) {
			LOG.warn("vosk could not listen for {} ({}); the keyword spotter alone listens", grammar.phrases(),
				e.toString());
			recognizer = null;
			return false;
		}
		matcher = new PhraseMatcher(grammar.phrases());
		fed.clear();
		fedSamples = 0;
		lastText = "";
		pending.clear();
		return true;
	}

	private void closeRecognizer() {
		if (recognizer != null) {
			try {
				recognizer.close();
			} catch (IllegalStateException e) {
				// released with the model
			}
			recognizer = null;
		}
	}

	/**
	 * Audio that does not follow on: the utterance is forgotten, and the ring, the clock and the VAD
	 * start again; nothing to do when nothing has been heard since the last time.
	 */
	private void endAudio() {
		if (ring.isEmpty() && fed.isEmpty()) {
			return;
		}
		if (recognizer != null) {
			try {
				recognizer.reset(); // its times go on counting every sample fed, as fedSamples does
			} catch (IllegalStateException e) {
				closeRecognizer();
			}
		}
		if (matcher != null) {
			matcher.reset();
		}
		pending.clear();
		fed.clear();
		lastText = "";
		fedUpTo = 0;
		ring.clear();
		clock.reset();
		speaking = false;
		tail = 0;
		if (vad != null) {
			try {
				vad.reset();
			} catch (IllegalStateException e) {
				// released
			}
		}
	}

	private void release() {
		closeRecognizer();
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
