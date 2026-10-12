package com.voiceforgaming.vfg.kws;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.voiceforgaming.vfg.audio.WindowListener.StopReason;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.IntStream;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/**
 * The spotter thread's logic against a stand-in model whose streams decode a chunk per 320 ms of
 * audio (ten windows) and hit where a test says, driven window by window on the test's thread. Each
 * window's samples are its number, so a stream knows which windows it was fed: a test either
 * scripts a hit on a stream's nth chunk, or says which windows a phrase is in, and a stream hears it
 * once it has had all of them and one 40 ms frame more, as the real model needs a blank frame after
 * a keyword. The tests of the live stream on its own run with re-checks off, as
 * {@code incantation_recheck_seconds: 0} does; the rest with the default 3 s.
 */
class SpotterWorkerTest {
	private static final long T0 = 1_760_000_000_000L;
	private static final Incantation FIRE_BALL = new Incantation("fire ball", List.of("fireball"), 3);

	/**
	 * A phrase said in windows {@code first} to {@code last}, which a stream hears as {@code keyword};
	 * with {@code blankAfter}, not when it was fed the window after {@code last} and that window is in
	 * another phrase, as the model needs a blank frame after a keyword ("fire" is not heard in "fire
	 * ball").
	 */
	record Said(String keyword, int first, int last, boolean blankAfter) {
		Said(String keyword, int first, int last) {
			this(keyword, first, last, false);
		}
	}

	/** What a stream was fed in one call: the window number in it (0 for zeros), where it starts, and its length. */
	record Fed(int window, long at, int length) {}

	/** A stream that is ready for a chunk per 5120 samples and hits on the decodes a test scripts or on its phrases. */
	static final class FakeStream implements KeywordModel.Stream {
		final Map<Long, KeywordModel.Hit> script;
		final List<Said> said;
		final Set<String> tags = new HashSet<>();
		final Set<Said> heard = new HashSet<>();
		final List<Fed> fed = new ArrayList<>();
		final List<Float> firstSamples = new ArrayList<>();
		long samples;
		long decoded;
		long resetSample;
		int resets;
		int hits;
		boolean closed;
		private KeywordModel.Hit pending;

		FakeStream(Map<Long, KeywordModel.Hit> script, List<Said> said, String keywords) {
			this.script = script;
			this.said = said;
			for (String k : keywords.split("/")) {
				tags.add(k.substring(k.lastIndexOf('@') + 1));
			}
		}

		@Override
		public void accept(float[] s) {
			fed.add(new Fed(Math.round(s[0]), samples, s.length));
			firstSamples.add(s[0]);
			samples += s.length;
		}

		@Override
		public boolean ready() {
			return samples / 5120 > decoded;
		}

		@Override
		public void decode() {
			decoded++;
			pending = script.get(decoded);
			long chunkEnd = decoded * 5120;
			for (Said p : said) {
				Fed first = whole(p.first());
				Fed last = whole(p.last());
				int next = p.last() + 1;
				boolean blank = !p.blankAfter() || fed.stream().noneMatch(f -> f.window() == next)
					|| said.stream().noneMatch(o -> o.first() <= next && next <= o.last());
				if (pending == null && tags.contains(p.keyword()) && !heard.contains(p) && first != null && last != null && first.at() >= resetSample
						&& chunkEnd >= last.at() + last.length() + 640 && blank) {
					heard.add(p);
					// tokens a little after the sound, as the worker's lead and lag expect
					pending = new KeywordModel.Hit(p.keyword(), new float[] {(first.at() - resetSample) / 16000f + 0.24f,
						(last.at() + last.length() - resetSample) / 16000f + 0.06f});
				}
			}
			if (pending != null) {
				hits++;
			}
		}

		private Fed whole(int window) {
			return fed.stream().filter(f -> f.window() == window && f.length() == 512).findFirst().orElse(null);
		}

		@Override
		public KeywordModel.Hit hit() {
			KeywordModel.Hit h = pending;
			pending = null;
			return h;
		}

		@Override
		public void reset() {
			resets++;
			resetSample = decoded * 5120;
		}

		@Override
		public void close() {
			closed = true;
		}

		/** The windows fed, in order, without the zeros. */
		List<Integer> windows() {
			return fed.stream().filter(f -> f.window() != 0).map(Fed::window).toList();
		}

		boolean pushed() {
			return !fed.isEmpty() && fed.getLast().window() == 0 && fed.getLast().length() == SpotterWorker.PUSH_SAMPLES;
		}
	}

	static final class FakeModel implements KeywordModel {
		final List<String> opened = new ArrayList<>();
		final List<FakeStream> streams = new ArrayList<>();
		final Map<Long, Hit> script = new HashMap<>();
		final List<Said> said = new ArrayList<>();
		boolean closed;

		@Override
		public Stream open(String keywords) {
			opened.add(keywords);
			FakeStream s = new FakeStream(script, said, keywords);
			streams.add(s);
			return s;
		}

		@Override
		public SentencePiece pieces() {
			try {
				return SentencePieceTest.model();
			} catch (Exception e) {
				throw new IllegalStateException(e);
			}
		}

		@Override
		public Set<String> tokens() {
			return Set.of("▁FI", "RE", "▁BA", "LL", "B", "AL", "L", "▁I", "CE", "▁WA");
		}

		@Override
		public int chunkFrames() {
			return 8;
		}

		@Override
		public int trailingBlanks() {
			return 1;
		}

		@Override
		public void close() {
			closed = true;
		}

		FakeStream last() {
			return streams.getLast();
		}

		int hits() {
			return streams.stream().mapToInt(s -> s.hits).sum();
		}
	}

	/** The spotter's own VAD, standing in: speech in the windows a test lists. */
	static final class FakeVad implements SpotterVad {
		final Set<Integer> speech = new HashSet<>();
		int resets;
		boolean closed;

		@Override
		public boolean speaking(float[] window) {
			return speech.contains(Math.round(window[0]));
		}

		@Override
		public void reset() {
			resets++;
		}

		@Override
		public void close() {
			closed = true;
		}
	}

	private FakeModel model;
	private List<SpotterWorker.Cast> casts;
	private SpotterWorker worker;
	private int windows;

	@BeforeEach
	void setUp() {
		start(0);
	}

	/** A fresh worker and model, re-checking over {@code recheckSeconds} (0: the live stream alone). */
	private void start(double recheckSeconds) {
		start(recheckSeconds, null);
	}

	private void start(double recheckSeconds, SpotterVad vad) {
		model = new FakeModel();
		casts = new ArrayList<>();
		worker = new SpotterWorker(true, 0.25, 1.5, recheckSeconds, casts::add);
		worker.setModel(model, vad);
		worker.setIncantations(List.of(FIRE_BALL));
		worker.onStarted(true);
		windows = 0;
	}

	/** The next window, read at T0 + 32 ms times its number, on the test's thread; then the worker has it. */
	private void window(boolean speaking) {
		queue(speaking);
		worker.drainNow();
	}

	/** The next window, queued and not yet handled. */
	private void queue(boolean speaking) {
		windows++;
		float[] w = new float[512];
		Arrays.fill(w, windows);
		worker.onWindow(w, T0 + windows * 32L, speaking);
	}

	private void windows(int n, boolean speaking) {
		for (int i = 0; i < n; i++) {
			window(speaking);
		}
	}

	/** The instant of window {@code n}'s first sample. */
	private static long startOf(int n) {
		return T0 + n * 32L - 31;
	}

	/** The instant of the first sample a stream was fed: a window, or its end from some sample on. */
	private static long firstInstant(FakeStream s) {
		Fed f = s.fed.getFirst();
		return T0 + f.window() * 32L - (f.length() - 1) / 16;
	}

	// --- the live stream ---

	@Test
	void theCaptureThreadNeverBlocksWhenTheQueueIsFull() {
		long t0 = System.nanoTime();
		for (int i = 0; i < 10_000; i++) {
			worker.onWindow(new float[512], T0 + i * 32L, true); // nobody takes them
		}
		long millis = (System.nanoTime() - t0) / 1_000_000;
		assertTrue(millis < 500, "10,000 offers took " + millis + " ms");
		assertEquals(10_000 - SpotterWorker.QUEUE_WINDOWS, worker.dropped());
	}

	@Test
	void theWindowIsCopiedBeforeItIsQueued() {
		float[] w = new float[512];
		w[0] = 0.5f;
		worker.onWindow(w, T0, true);
		w[0] = -1f; // the capture thread reuses or changes its array
		worker.drainNow();
		assertEquals(List.of(0.5f), model.last().firstSamples);
	}

	@Test
	void nothingIsQueuedForTheMicTestWithoutIncantationsOrWithTheSpotterOff() {
		assertTrue(worker.listening());
		worker.onStarted(false);
		assertFalse(worker.listening(), "the settings screen's mic test");
		worker.onStarted(true);
		worker.setIncantations(List.of());
		assertFalse(worker.listening(), "no incantations");
		worker.setIncantations(List.of(FIRE_BALL));
		worker.configure(false, 0.25, 1.5, 3);
		assertFalse(worker.listening(), "incantations: false");
		worker.configure(true, 0.25, 1.5, 3);
		worker.onStopped(StopReason.LEAVE);
		assertFalse(worker.listening(), "left the server");
	}

	@Test
	void aStreamOpensOnSpeechWithThePreRollAndIsPushedAndClosedWhenSpeechEnds() {
		windows(30, false);
		assertTrue(model.streams.isEmpty(), "silence opens no stream");
		window(true);
		assertEquals(1, model.streams.size());
		assertEquals(SpotterWorker.PRE_ROLL_WINDOWS * 512L, model.last().samples, "fed the last 0.64 s first");
		windows(9, true);
		assertFalse(model.last().closed);
		window(false);
		FakeStream s = model.last();
		assertTrue(s.closed, "closed when speech ends");
		assertTrue(s.pushed(), "after 0.5 s of zeros");
		assertEquals(30 * 512L + SpotterWorker.PUSH_SAMPLES, s.samples, "the window that ended speech, then the zeros");
		windows(20, false);
		assertEquals(1, model.streams.size());
		window(true);
		assertEquals(2, model.streams.size(), "the next speech opens a new one");
		assertEquals(SpotterWorker.PRE_ROLL_WINDOWS * 512L, model.last().samples);
	}

	@Test
	void thePushDecodesTheLastChunkAtOnceAndItsHitCasts() {
		model.said.add(new Said("fireball", 11, 20));
		windows(10, false);
		windows(10, true);
		assertTrue(casts.isEmpty(), "the chunk with the phrase's end has not filled");
		window(false);
		assertEquals(1, casts.size(), "the zeros filled it");
		SpotterWorker.Cast c = casts.getFirst();
		assertEquals(SpotterWorker.Via.STREAM, c.via());
		assertEquals(T0 + 21 * 32, c.heardAtMillis());
		assertEquals(1, model.streams.size(), "no re-checks with incantation_recheck_seconds 0");
	}

	@Test
	void aHitOnAnAliasCastsThePhraseWithItsTimesOnTheAudioClock() {
		// on the third chunk: tokens at 0.40 to 0.64 s of the stream, which starts with window 1
		model.script.put(3L, new KeywordModel.Hit("fireball", new float[] {0.40f, 0.48f, 0.56f, 0.60f, 0.64f}));
		windows(30, true);
		assertEquals(1, casts.size());
		SpotterWorker.Cast c = casts.getFirst();
		assertEquals("fire ball", c.phrase());
		assertEquals("fireball", c.heard());
		// sample 0 was read with window 1, at T0 + 32 ms; a sample's instant is its read's time less the samples after it
		assertEquals(T0 + 400 - SpotterWorker.START_LEAD_MILLIS + 1, c.startedAtMillis(), "first token less the lead");
		assertEquals(T0 + 680 - SpotterWorker.END_LAG_MILLIS + 1, c.endedAtMillis(),
			"the end of the last token's 40 ms frame less the lag");
		assertEquals(T0 + 30 * 32, c.heardAtMillis(), "the newest sample when it fired");
		assertEquals(1, model.last().resets, "reset after the hit");
	}

	@Test
	void aSecondHitCountsItsFramesFromTheResetAfterTheFirst() {
		model.script.put(3L, new KeywordModel.Hit("fire_ball", new float[] {0.40f, 0.64f}));
		model.script.put(6L, new KeywordModel.Hit("fire_ball", new float[] {0.32f, 0.40f}));
		windows(60, true);
		assertEquals(2, casts.size());
		SpotterWorker.Cast second = casts.get(1);
		// reset after 3 chunks (24 frames, 0.96 s): the tokens are at 0.96 + 0.32 and 0.96 + 0.40 s
		assertEquals(T0 + 1280 - SpotterWorker.START_LEAD_MILLIS + 1, second.startedAtMillis());
		assertEquals(T0 + 1400 - SpotterWorker.END_LAG_MILLIS + 1, second.endedAtMillis());
	}

	@Test
	void aResetTheModelMadeInSilenceIsFoundFromTheChunkTheHitFiredIn() {
		// no reset since the start: frames count from 0, and the hit fires in the chunk just decoded
		assertEquals(0, SpotterWorker.frameOffset(0, 3, 16, 8, 1));
		assertEquals(24, SpotterWorker.frameOffset(3, 6, 10, 8, 1));
		// 20 chunks in, a hit whose last token is at frame 5: it fired at 7, so counted from a reset at chunk 19
		assertEquals(152, SpotterWorker.frameOffset(0, 20, 5, 8, 1));
		// a hit that fired late by up to two chunks is still counted from the reset the worker made
		assertEquals(0, SpotterWorker.frameOffset(0, 5, 16, 8, 1));
	}

	@Test
	void aNewListReopensTheOpenStreamWithItsKeywordsAndTheRecentAudio() {
		windows(25, true);
		assertEquals(1, model.opened.size());
		worker.setIncantations(List.of(FIRE_BALL, new Incantation("ice wall", List.of(), 3)));
		window(true);
		assertEquals(2, model.opened.size());
		assertTrue(model.streams.getFirst().closed);
		assertTrue(model.opened.get(1).endsWith("@ice_wall"), model.opened.get(1));
		assertEquals(21 * 512L, model.last().samples, "the last 0.64 s again, and the new window");
	}

	@Test
	void windowsDroppedFromAFullQueueStartAFreshStream() {
		for (int i = 0; i < SpotterWorker.QUEUE_WINDOWS + 6; i++) {
			worker.onWindow(new float[512], T0 + i * 32L, true); // the last six are dropped
		}
		worker.drainNow();
		assertEquals(1, model.streams.size());
		worker.onWindow(new float[512], T0 + 70 * 32L, true);
		worker.drainNow();
		assertEquals(2, model.streams.size(), "the audio is not continuous across the gap");
		assertTrue(model.streams.getFirst().closed);
		assertEquals(512, model.last().samples, "and the pre-roll from before the gap is not replayed");
	}

	@Test
	void leavingClosesTheStreamAtTheNextWait() {
		windows(5, true);
		worker.onStopped(StopReason.LEAVE);
		worker.step(null);
		assertTrue(model.last().closed);
	}

	@Test
	void shutdownReleasesTheModelAndTheVadItOwns() {
		FakeVad vad = new FakeVad();
		vad.speech.addAll(List.of(1, 2, 3, 4, 5));
		start(3, vad);
		windows(5, true);
		worker.shutdown(0);
		assertTrue(model.streams.getFirst().closed);
		assertTrue(model.closed);
		assertTrue(vad.closed);
	}

	// --- the re-check ---

	@Test
	void aRecheckCastsAPhraseAsItEndsFromTheLastSecondsAndZeros() {
		start(3);
		model.said.add(new Said("fireball", 11, 20));
		windows(10, false);
		windows(9, true);
		assertTrue(casts.isEmpty(), "the phrase is not over");
		window(true);
		assertEquals(1, casts.size(), "heard with the window it ends in");
		SpotterWorker.Cast c = casts.getFirst();
		assertEquals(SpotterWorker.Via.RECHECK, c.via());
		assertEquals("fire ball", c.phrase());
		assertEquals(T0 + 20 * 32, c.heardAtMillis(), "the newest sample it had");
		assertTrue(Math.abs(c.startedAtMillis() - startOf(11)) <= 40, "started " + (c.startedAtMillis() - startOf(11)));
		assertTrue(Math.abs(c.endedAtMillis() - (T0 + 20 * 32)) <= 40, "ended " + (c.endedAtMillis() - T0 - 640));
		assertTrue(c.endedAtMillis() <= c.heardAtMillis(), "never after the newest sample");
		FakeStream recheck = model.last();
		assertTrue(recheck.closed, "a re-check's stream is thrown away");
		assertTrue(recheck.pushed(), "after 0.5 s of zeros");
		assertEquals(20, recheck.windows().getLast(), "up to the newest window");
	}

	@Test
	void aRecheckReachesBackItsSecondsAndNoFurther() {
		start(1);
		windows(40, true);
		FakeStream recheck = model.last();
		assertEquals(9, recheck.windows().getFirst(), "1 s back: from part of window 9 to window 40");
		assertEquals(16000 + SpotterWorker.PUSH_SAMPLES, recheck.samples);
	}

	@Test
	void noSayingIsCastTwiceByTheRecheckAndTheLiveStream() {
		start(3);
		model.said.add(new Said("fireball", 11, 20));
		windows(10, false);
		windows(20, true);
		windows(10, false);
		assertEquals(1, casts.size(), casts.toString());
		assertEquals(SpotterWorker.Via.RECHECK, casts.getFirst().via());
		FakeStream live = model.streams.getFirst();
		assertTrue(live.closed, "the live stream, which had the saying in it, closed at the cast");
		assertEquals(20, live.windows().getLast());
		FakeStream reopened = model.streams.stream().filter(st -> st.windows().contains(25)).findFirst().orElseThrow();
		assertTrue(firstInstant(reopened) > casts.getFirst().endedAtMillis(), "reopened from after the cast's end");
		assertTrue(model.streams.stream().noneMatch(st -> st != model.last() && !st.closed));
	}

	@Test
	void aRechecksCastBeforeThePhraseIsOverIsNotCastAgainByTheStream() {
		// a re-check that hears the phrase while its last window is still being said, as with "heal"
		start(3);
		model.said.add(new Said("fireball", 11, 19));
		windows(10, false);
		windows(9, true);
		assertEquals(1, casts.size());
		model.said.add(new Said("fireball", 11, 21)); // the stream would hear the whole saying, a little later
		windows(11, true);
		window(false);
		assertEquals(1, casts.size(), casts.toString());
	}

	@Test
	void aRecheckNeverReachesBackPastTheEndOfTheLastCast() {
		start(3);
		model.said.add(new Said("fireball", 11, 20));
		windows(10, false);
		windows(10, true);
		assertEquals(1, casts.size());
		int before = model.streams.size();
		windows(15, true);
		List<FakeStream> after = model.streams.subList(before, model.streams.size());
		assertEquals(15, after.stream().filter(st -> st.pushed() && st.closed).count(), "one re-check a window");
		assertEquals(1, after.stream().filter(st -> !st.closed).count(), "and the live stream, reopened");
		long end = casts.getFirst().endedAtMillis();
		for (FakeStream st : after) {
			assertTrue(firstInstant(st) > end && firstInstant(st) <= end + 1, "from just after the cast's end");
			assertFalse(st.windows().contains(11));
		}
		assertEquals(1, casts.size());
	}

	@Test
	void theSamePhraseSaidAgainAfterTheCastIsCastAgain() {
		start(3);
		model.said.add(new Said("fireball", 11, 20));
		model.said.add(new Said("fireball", 26, 35));
		windows(10, false);
		windows(35, true);
		windows(10, false);
		assertEquals(2, casts.size(), casts.toString());
		assertTrue(casts.stream().allMatch(c -> c.via() == SpotterWorker.Via.RECHECK));
		assertTrue(Math.abs(casts.get(1).startedAtMillis() - startOf(26)) <= 40);
		assertEquals(T0 + 35 * 32, casts.get(1).heardAtMillis());
	}

	@Test
	void aSayingTheLiveStreamCastFirstIsNotHeardAgainByTheRecheck() {
		// a PC too busy to re-check: forty windows wait, and the stream hears the phrase among them
		start(3);
		model.said.add(new Said("fireball", 11, 20));
		for (int i = 0; i < 40; i++) {
			queue(true);
		}
		worker.drainNow();
		assertEquals(1, casts.size(), casts.toString());
		assertEquals(SpotterWorker.Via.STREAM, casts.getFirst().via());
		assertEquals(2, model.streams.size());
		assertTrue(firstInstant(model.last()) > casts.getFirst().endedAtMillis(), "the re-check starts after the cast's end");
		windows(10, true);
		assertEquals(1, casts.size());
	}

	@Test
	void rechecksRunOnlyWhileTheSpotterHearsSpeech() {
		start(3);
		windows(30, false);
		assertTrue(model.streams.isEmpty(), "nothing in silence");
		windows(5, true);
		assertEquals(6, model.streams.size(), "the live stream and a re-check a window");
		window(false);
		assertEquals(6, model.streams.size(), "speech ended: no re-check");
		windows(20, false);
		assertEquals(6, model.streams.size());
	}

	@Test
	void aRecheckComesAfterEveryWindowWaitingIsHandled() {
		start(3);
		for (int i = 0; i < 5; i++) {
			queue(true);
		}
		worker.drainNow();
		assertEquals(2, model.streams.size(), "the live stream, and one re-check for the five windows");
		assertEquals(List.of(1, 2, 3, 4, 5), model.streams.getFirst().windows());
		assertEquals(List.of(1, 2, 3, 4, 5), model.last().windows(), "the re-check has the newest window");
		worker.drainNow();
		assertEquals(2, model.streams.size(), "nothing new: no re-check");
	}

	@Test
	void aRecheckKeepsOnlyALittleOfTheSilenceBeforeSpeech() {
		start(3);
		windows(60, false);
		window(true);
		assertEquals(61 - SpotterWorker.LEAD_WINDOWS, model.last().windows().getFirst());
		windows(10, true);
		windows(SpotterWorker.GAP_WINDOWS + 4, false);
		window(true);
		int speech = windows;
		assertEquals(speech - SpotterWorker.LEAD_WINDOWS, model.last().windows().getFirst(),
			"a long silence inside its reach ends it");
		windows(5, false);
		windows(2, true);
		assertEquals(speech - SpotterWorker.LEAD_WINDOWS, model.last().windows().getFirst(), "a short one does not");
	}

	@Test
	void withRechecksOffTheLiveStreamIsAlone() {
		start(0);
		model.said.add(new Said("fireball", 11, 20));
		windows(10, false);
		windows(20, true);
		window(false);
		assertEquals(1, model.streams.size());
		assertEquals(1, casts.size());
		assertEquals(SpotterWorker.Via.STREAM, casts.getFirst().via());
	}

	@Test
	void rechecksFollowTheConfig() {
		start(0);
		windows(5, true);
		assertEquals(1, model.streams.size());
		worker.configure(true, 0.25, 1.5, 3);
		window(true);
		assertEquals(2, model.streams.size(), "on from the next window");
		worker.configure(true, 0.25, 1.5, 0);
		windows(3, true);
		assertEquals(2, model.streams.size(), "and off again");
	}

	@Test
	void theSpottersOwnVadSaysWhenSpeechEnds() {
		FakeVad vad = new FakeVad();
		for (int i = 11; i <= 20; i++) {
			vad.speech.add(i);
		}
		start(3, vad);
		windows(30, true); // the capture's VAD hears speech throughout
		FakeStream live = model.streams.getFirst();
		assertEquals(IntStream.rangeClosed(1, 21).boxed().toList(), live.windows(),
			"opened at window 11 with the pre-roll, and fed to window 21");
		assertTrue(live.closed && live.pushed(), "pushed and closed at window 21, when the spotter's VAD says so");
		assertEquals(1 + 10, model.streams.size(), "re-checks only while its VAD hears speech");
		int resets = vad.resets;
		worker.onStarted(true);
		window(false);
		assertEquals(resets + 1, vad.resets, "a new capture starts its VAD again");
	}

	@Test
	void aKeywordThatStartsALongerOneIsHeldAndTheLongerOneCasts() {
		start(3);
		worker.setIncantations(List.of(new Incantation("fire", List.of(), 3), FIRE_BALL));
		model.said.add(new Said("fire", 11, 15, true));
		model.said.add(new Said("fire_ball", 11, 20));
		windows(10, false);
		windows(5, true);
		assertTrue(model.streams.subList(1, model.streams.size()).stream().noneMatch(st -> st.tags.contains("fire")),
			"the re-checks while speech goes on leave \"fire\" out");
		windows(5, true);
		window(false);
		assertEquals(1, casts.size(), casts.toString());
		assertEquals("fire ball", casts.getFirst().phrase());
		assertEquals(SpotterWorker.Via.RECHECK, casts.getFirst().via());
	}

	@Test
	void aHeldKeywordSaidAloneCastsWhenSpeechEnds() {
		start(3);
		worker.setIncantations(List.of(new Incantation("fire", List.of(), 3), FIRE_BALL));
		model.said.add(new Said("fire", 11, 15, true));
		windows(10, false);
		windows(5, true);
		assertTrue(casts.isEmpty());
		window(false);
		assertTrue(model.last().tags.contains("fire") && model.last().pushed(), "one last re-check, with every keyword");
		assertEquals(1, casts.size(), "when speech ends");
		assertEquals("fire", casts.getFirst().phrase());
		assertEquals(T0 + 16 * 32, casts.getFirst().heardAtMillis());
		windows(20, false);
		assertEquals(1, casts.size());
	}

	@Test
	void theLastRecheckRunsOnlyWhenAKeywordStartsAnother() {
		start(3);
		windows(5, true);
		int before = model.streams.size();
		window(false);
		assertEquals(before, model.streams.size());
	}

	@Test
	void aHeldKeywordFollowedByOtherWordsCastsAfterTheHold() {
		start(3);
		worker.setIncantations(List.of(new Incantation("fire", List.of(), 3), FIRE_BALL));
		model.said.add(new Said("fire", 11, 15, true));
		windows(10, false);
		windows(30, true); // "fire", then words that are not "ball"
		assertEquals(1, casts.size(), casts.toString());
		SpotterWorker.Cast c = casts.getFirst();
		assertEquals("fire", c.phrase());
		long held = c.heardAtMillis() - c.endedAtMillis();
		assertTrue(held >= SpotterWorker.HOLD_MILLIS && held < SpotterWorker.HOLD_MILLIS + 40, "held " + held + " ms");
	}

	@Test
	void withRechecksOffAHeldKeywordStillWaitsForTheLongerOne() {
		start(0);
		worker.setIncantations(List.of(new Incantation("fire", List.of(), 3), FIRE_BALL));
		model.said.add(new Said("fire", 11, 15)); // the stream hears it at a gap between words
		windows(10, false);
		windows(30, true);
		window(false);
		assertEquals(1, casts.size(), casts.toString());
		assertEquals("fire", casts.getFirst().phrase());
	}

	@Test
	void withNothingToListenForTheAudioIsDroppedOnceNotEveryWindow() {
		FakeVad vad = new FakeVad();
		vad.speech.addAll(List.of(1, 2, 3, 4, 5));
		start(3, vad);
		windows(5, true);
		int resets = vad.resets;
		worker.setIncantations(List.of(new Incantation("level 3", List.of(), 3))); // no tokens for it here
		windows(40, true);
		assertEquals(resets + 1, vad.resets, "reset once, on the way in");
		assertTrue(model.streams.getFirst().closed);
	}

	@Test
	void aOneWordPhraseIsListenedForAndCast() {
		start(3);
		worker.setIncantations(List.of(new Incantation("Fireball", List.of(), 3)));
		model.said.add(new Said("fireball", 11, 20));
		windows(10, false);
		windows(10, true);
		assertTrue(model.opened.getFirst().endsWith("@fireball"), model.opened.getFirst());
		assertEquals(1, casts.size());
		assertEquals("fireball", casts.getFirst().phrase());
		assertEquals("fireball", casts.getFirst().heard());
	}
}
