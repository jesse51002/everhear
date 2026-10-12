package com.voiceforgaming.vfg.kws;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.google.gson.JsonParser;
import com.voiceforgaming.vfg.audio.WindowListener.StopReason;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Comparator;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.stream.IntStream;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/**
 * The Vosk thread's logic against a stand-in recognizer, driven window by window on the test's
 * thread. Window n's samples are all n / 1000, so the recognizer knows which windows it was fed
 * (0 is the zeros pushed when speech ends); a test says which windows a phrase's words are in, and
 * the stand-in's partial shows them once it has had them all, as Vosk's does about when a phrase
 * ends; its final result gives each its times. A stand-in VAD hears speech in the windows a test
 * lists.
 */
class VoskWorkerTest {
	private static final long T0 = 1_760_000_000_000L;
	private static final Incantation ICE_BEAM = new Incantation("ice beam", List.of("nice beam"), 3);
	private static final Incantation FIREBALL = new Incantation("fireball", List.of("fire ball"), 3);

	/**
	 * Words said in windows {@code first} to {@code last}; with {@code finalOnly}, shown in a final
	 * result only; with {@code gone} above 0, shown in partials only until that window is fed, as a
	 * partial's flicker.
	 */
	record Said(String words, int first, int last, boolean finalOnly, int gone) {
		Said(String words, int first, int last) {
			this(words, first, last, false, 0);
		}

		Said(String words, int first, int last, boolean finalOnly) {
			this(words, first, last, finalOnly, 0);
		}
	}

	static final class FakeRecognizer implements VoskModel.Recognizer {
		final FakeVosk vosk;
		final String grammar;
		/** The window of each piece fed, in order, 0 for zeros, and the sample it started at. */
		final List<int[]> fed = new ArrayList<>();
		int utteranceStart;
		long samples;
		int partials;
		int resets;
		boolean closed;

		FakeRecognizer(FakeVosk vosk, String grammar) {
			this.vosk = vosk;
			this.grammar = grammar;
		}

		@Override
		public boolean accept(short[] s) {
			int window = Math.round(s[0] / 32.767f);
			fed.add(new int[] {window, (int) samples, s.length});
			samples += s.length;
			return vosk.endpointAt.contains(window) && s.length == 512 || window == 0 && vosk.endpointAtPush;
		}

		private List<int[]> utterance() {
			return fed.subList(utteranceStart, fed.size());
		}

		private List<Said> heard(boolean all) {
			Set<Integer> had = new HashSet<>();
			for (int[] f : utterance()) {
				had.add(f[0]);
			}
			return vosk.said.stream().filter(p -> all || !p.finalOnly())
				.filter(p -> all || p.gone() == 0 || !had.contains(p.gone()))
				.filter(p -> IntStream.rangeClosed(p.first(), p.last()).allMatch(had::contains))
				.sorted(Comparator.comparingInt(Said::first)).toList();
		}

		@Override
		public String partial() {
			partials++;
			return String.join(" ", heard(false).stream().map(Said::words).toList());
		}

		@Override
		public List<VoskModel.Word> result() {
			List<VoskModel.Word> out = new ArrayList<>();
			for (Said p : heard(true)) {
				int[] first = utterance().stream().filter(f -> f[0] == p.first()).findFirst().orElseThrow();
				int[] last = utterance().stream().filter(f -> f[0] == p.last()).findFirst().orElseThrow();
				for (String w : p.words().split(" ")) {
					out.add(new VoskModel.Word(w, first[1] / 16000.0, (last[1] + last[2]) / 16000.0));
				}
			}
			utteranceStart = fed.size();
			return out;
		}

		@Override
		public void reset() {
			resets++;
			utteranceStart = fed.size();
		}

		@Override
		public void close() {
			closed = true;
		}

		/** The windows fed, in order, 0 for zeros. */
		List<Integer> windows() {
			return fed.stream().map(f -> f[0]).toList();
		}
	}

	static final class FakeVosk implements VoskModel {
		final List<FakeRecognizer> recognizers = new ArrayList<>();
		final List<Said> said = new ArrayList<>();
		final Set<Integer> endpointAt = new HashSet<>();
		boolean endpointAtPush;
		boolean closed;

		@Override
		public Recognizer recognizer(String grammarJson) {
			FakeRecognizer r = new FakeRecognizer(this, grammarJson);
			recognizers.add(r);
			return r;
		}

		@Override
		public boolean knows(String word) {
			return !word.equals("galick");
		}

		@Override
		public void close() {
			closed = true;
		}

		FakeRecognizer last() {
			return recognizers.getLast();
		}
	}

	/** Its VAD, standing in: speech in the windows a test lists. */
	static final class FakeVad implements SpotterVad {
		final Set<Integer> speech = new HashSet<>();
		boolean closed;

		@Override
		public boolean speaking(float[] window) {
			return speech.contains(Math.round(window[0] * 1000));
		}

		@Override
		public void reset() {}

		@Override
		public void close() {
			closed = true;
		}
	}

	private FakeVosk vosk;
	private FakeVad vad;
	private CastGate gate;
	private List<SpotterWorker.Cast> casts;
	private VoskWorker worker;
	private int windows;

	@BeforeEach
	void setUp() {
		vosk = new FakeVosk();
		vad = new FakeVad();
		gate = new CastGate();
		casts = new ArrayList<>();
		worker = new VoskWorker(true, gate, casts::add);
		worker.setModel(vosk, vad, List.of("the", "ice", "watch", "this", "fire"));
		worker.setIncantations(List.of(ICE_BEAM, FIREBALL));
		worker.onStarted(true);
		windows = 0;
	}

	/** Speech, as its VAD hears it, in windows {@code first} to {@code last}. */
	private void speech(int first, int last) {
		for (int i = first; i <= last; i++) {
			vad.speech.add(i);
		}
	}

	private void window() {
		windows++;
		float[] w = new float[512];
		Arrays.fill(w, windows / 1000f);
		worker.onWindow(w, T0 + windows * 32L, false);
		worker.drainNow();
	}

	private void windows(int n) {
		for (int i = 0; i < n; i++) {
			window();
		}
	}

	/** The instant of window {@code n}'s first sample (window n is read at T0 + 32 n ms). */
	private static long startOf(int n) {
		return T0 + n * 32L - 31;
	}

	private static long endOf(int n) {
		return T0 + n * 32L;
	}

	@Test
	void itIsFedSpeechWithThePreRollAndZerosWhenSpeechEnds() {
		speech(31, 40);
		windows(30);
		assertEquals(List.of(), vosk.last().windows(), "nothing in silence");
		window();
		assertEquals(IntStream.rangeClosed(1, 31).boxed().toList(), vosk.last().windows(), "up to 1 s before speech first");
		windows(9);
		window(); // 41: speech has ended
		List<Integer> fed = vosk.last().windows();
		assertEquals(41, fed.get(fed.size() - 2), "the window that ended speech");
		assertEquals(0, fed.getLast(), "then the zeros");
		assertEquals(VoskWorker.PUSH_SAMPLES, vosk.last().fed.getLast()[2]);
		windows(5);
		speech(47, 50);
		windows(4);
		assertEquals(List.of(42, 43, 44, 45, 46, 47), vosk.last().windows().subList(fed.size(), fed.size() + 6),
			"the silence after speech, then the next speech");
		assertEquals(1, vosk.recognizers.size(), "one recognizer across the pause");
		window(); // 51: speech has ended
		int before = vosk.last().fed.size();
		windows(60);
		List<Integer> after = vosk.last().windows().subList(before, vosk.last().fed.size());
		assertEquals(IntStream.rangeClosed(52, 51 + VoskWorker.TAIL_WINDOWS).boxed().toList(), after,
			"1 s of the silence after speech, and no more");
		speech(130, 135);
		windows(30);
		assertEquals(IntStream.rangeClosed(99, 133).boxed().toList(),
			vosk.last().windows().subList(before + after.size(), before + after.size() + 35), "then 1 s of pre-roll");
	}

	@Test
	void aPhraseCastsWhenTheNextPartialStillShowsItWithItsWindowFromWhenItShowed() {
		worker.confirmSayings(true); // vosk_confirm
		speech(31, 50);
		vosk.said.add(new Said("watch this", 31, 35));
		vosk.said.add(new Said("ice beam", 37, 42));
		vosk.said.add(new Said("now", 44, 45));
		windows(42);
		assertTrue(casts.isEmpty(), "shown once: it waits to hold");
		windows(2);
		assertTrue(casts.isEmpty(), "the same partial again is no news");
		window(); // 45: "watch this ice beam now"
		assertEquals(1, casts.size());
		SpotterWorker.Cast c = casts.getFirst();
		assertEquals("ice beam", c.phrase());
		assertEquals("ice beam", c.heard(), "the phrase itself: Vosk listens for no alias");
		assertEquals(SpotterWorker.Via.VOSK, c.via());
		assertEquals(endOf(42) - VoskWorker.END_SAMPLES / 16, c.endedAtMillis(), "ends 0.15 s before the sample it showed at");
		assertEquals(endOf(45), c.heardAtMillis());
		assertEquals(endOf(42) - VoskWorker.LEAD_SAMPLES / 16 + 1, c.startedAtMillis(), "0.8 s of its audio before");
		windows(10);
		assertEquals(1, casts.size(), "shown again and again, cast once");
	}

	@Test
	void aPhraseThatEndsSpeechCastsWhenSpeechEnds() {
		worker.confirmSayings(true); // vosk_confirm
		speech(11, 20);
		vosk.said.add(new Said("fireball", 15, 20));
		windows(20);
		assertTrue(casts.isEmpty());
		window(); // 21: speech has ended, the zeros are fed, and the partial still shows it
		assertEquals(1, casts.size());
		assertEquals(endOf(21), casts.getFirst().heardAtMillis());
		assertEquals(endOf(20) - VoskWorker.END_SAMPLES / 16, casts.getFirst().endedAtMillis());
	}

	@Test
	void aPhraseShownForAMomentIsNotCastAndIsNewIfItShowsAgain() {
		worker.confirmSayings(true); // vosk_confirm
		speech(11, 40);
		vosk.said.add(new Said("fireball", 13, 16, false, 17)); // "throw the ball into the fire"
		windows(17);
		assertTrue(casts.isEmpty(), "taken back by the next partial");
		windows(25);
		assertTrue(casts.isEmpty(), "nor at the end of speech");
		vosk.said.add(new Said("fireball", 45, 48));
		speech(44, 60);
		windows(20);
		assertEquals(1, casts.size(), "said for real later: a saying of its own");
	}

	@Test
	void byDefaultASayingCastsAsItShowsAndTheFlickerCasts() {
		speech(11, 40);
		vosk.said.add(new Said("fireball", 13, 16, false, 17));
		windows(16);
		assertEquals(1, casts.size(), "cast as it showed");
		assertEquals(endOf(16), casts.getFirst().heardAtMillis());
	}

	@Test
	void theWindowNeverStartsBeforeTheAudioItWasFed() {
		speech(5, 20);
		vosk.said.add(new Said("fireball", 5, 8));
		windows(21);
		assertEquals(1, casts.size());
		assertEquals(startOf(1), casts.getFirst().startedAtMillis(), "from the first sample it was fed");
	}

	@Test
	void aPhraseSaidTwiceCastsTwice() {
		speech(11, 30);
		vosk.said.add(new Said("fireball", 12, 16));
		vosk.said.add(new Said("fireball", 17, 21));
		windows(31);
		assertEquals(2, casts.size(), casts.toString());
		assertTrue(casts.get(1).startedAtMillis() < casts.getFirst().endedAtMillis(),
			"the windows overlap at the edge, and still two sayings");
	}

	@Test
	void aSayingTheSpotterCastAlreadyIsNotPostedAgain() {
		speech(11, 25);
		vosk.said.add(new Said("ice beam", 15, 20));
		windows(19);
		// the spotter's re-check, which ends its window a little before the phrase's sound does
		assertTrue(gate.claim("ice beam", startOf(15), endOf(20) - 180, SpotterWorker.Via.RECHECK));
		windows(10);
		assertTrue(casts.isEmpty(), casts.toString());
		assertEquals(1, gate.claims().size());
		vosk.said.add(new Said("ice beam", 40, 45));
		speech(38, 50);
		windows(25);
		assertEquals(1, casts.size(), "the next saying is Vosk's");
		assertFalse(gate.claim("ice beam", startOf(40), endOf(45) - 180, SpotterWorker.Via.STREAM),
			"and the spotter's is refused");
	}

	@Test
	void anotherIncantationOverTheSameAudioStillCasts() {
		worker.setIncantations(List.of(ICE_BEAM, FIREBALL, new Incantation("speed dash", List.of(), 3)));
		speech(11, 25);
		vosk.said.add(new Said("speed dash", 15, 20));
		gate.claim("ice beam", startOf(15), endOf(20), SpotterWorker.Via.RECHECK);
		windows(30);
		assertEquals(List.of("speed dash"), casts.stream().map(SpotterWorker.Cast::phrase).toList());
	}

	@Test
	void theGrammarIsThePhrasesAmongTheFillersAndANewListRebuildsIt() {
		window();
		assertEquals(List.of("ice beam", "fireball", "the", "watch", "this", "fire", "[unk]"),
			entries(vosk.last().grammar), "no filler that is a phrase's word");
		speech(5, 40);
		windows(20);
		worker.setIncantations(List.of(FIREBALL, new Incantation("juicy steak", List.of(), 3)));
		window();
		assertEquals(2, vosk.recognizers.size());
		assertTrue(vosk.recognizers.getFirst().closed);
		assertEquals(List.of("fireball", "juicy steak", "the", "ice", "watch", "this", "fire", "[unk]"),
			entries(vosk.last().grammar));
		assertEquals(IntStream.rangeClosed(1, 22).boxed().toList(), vosk.last().windows(),
			"speech is going on: the new one has the last 0.64 s and the window");
		worker.setIncantations(List.of(FIREBALL, new Incantation("juicy steak", List.of("juice steak"), 3)));
		window();
		assertEquals(2, vosk.recognizers.size(), "a new alias changes nothing for Vosk");
	}

	@Test
	void aPhraseVoskDoesNotKnowIsLeftOutAndWithNoneLeftItListensForNothing() {
		worker.setIncantations(List.of(new Incantation("galick gun", List.of("garlic gun"), 3), FIREBALL));
		window();
		assertEquals(List.of("fireball", "the", "ice", "watch", "this", "fire", "[unk]"), entries(vosk.last().grammar));
		worker.setIncantations(List.of(new Incantation("galick gun", List.of(), 3)));
		speech(3, 10);
		windows(10);
		assertTrue(vosk.last().closed, "nothing to listen for");
		assertEquals(1, vosk.recognizers.size());
	}

	@Test
	void aSayingOnlyInAFinalCastsWithItsOwnTimesIfItIsNotLate() {
		speech(11, 20);
		vosk.said.add(new Said("fireball", 13, 17, true));
		vosk.endpointAtPush = true;
		windows(21); // 21 ends speech: the zeros, and Vosk ends the utterance
		assertEquals(1, casts.size());
		SpotterWorker.Cast c = casts.getFirst();
		assertEquals(startOf(13), c.startedAtMillis(), "the words' own times");
		assertEquals(endOf(17), c.endedAtMillis());
		assertEquals(endOf(21), c.heardAtMillis());
	}

	@Test
	void aSayingFoundTooLateIsNotCast() {
		speech(11, 20);
		speech(80, 90);
		vosk.said.add(new Said("fireball", 13, 17, true));
		vosk.endpointAt.add(70); // Vosk ends the utterance only in the next speech's pre-roll
		windows(90);
		assertTrue(casts.isEmpty(), "1.7 s after the phrase: " + casts);
	}

	@Test
	void nothingIsQueuedForTheMicTestWithoutIncantationsOrWithVoskOff() {
		assertTrue(worker.listening());
		worker.onStarted(false);
		assertFalse(worker.listening(), "the settings screen's mic test");
		worker.onStarted(true);
		worker.setIncantations(List.of());
		assertFalse(worker.listening(), "no incantations");
		worker.setIncantations(List.of(FIREBALL));
		worker.configure(false);
		assertFalse(worker.listening(), "vosk: false");
		worker.configure(true);
		worker.onStopped(StopReason.LEAVE);
		assertFalse(worker.listening(), "left the server");
	}

	@Test
	void theCaptureThreadNeverBlocksAndTheWindowIsCopied() {
		long t0 = System.nanoTime();
		for (int i = 0; i < 10_000; i++) {
			worker.onWindow(new float[512], T0 + i * 32L, true);
		}
		assertTrue((System.nanoTime() - t0) / 1_000_000 < 500);
		assertEquals(10_000 - VoskWorker.QUEUE_WINDOWS, worker.dropped());
	}

	@Test
	void droppedWindowsResetTheRecognizerAndItsTimesStillCount() {
		speech(1, 200);
		windows(5);
		for (int i = 0; i < VoskWorker.QUEUE_WINDOWS + 6; i++) {
			windows++;
			float[] w = new float[512];
			Arrays.fill(w, windows / 1000f);
			worker.onWindow(w, T0 + windows * 32L, false); // nobody takes them: the last six are dropped
		}
		worker.drainNow();
		int before = vosk.last().fed.size();
		window();
		assertEquals(1, vosk.recognizers.size(), "reset, not rebuilt");
		assertEquals(1, vosk.last().resets, "the audio is not continuous across the gap");
		assertEquals(List.of(windows), vosk.last().windows().subList(before, vosk.last().fed.size()),
			"and nothing from before the gap is replayed");
		// a final's times count every sample the recognizer was fed, the reset's too
		int saidAt = windows + 2;
		vosk.said.add(new Said("fireball", saidAt, saidAt + 3, true));
		vosk.endpointAt.add(saidAt + 5);
		windows(7);
		assertEquals(1, casts.size());
		assertEquals(startOf(saidAt), casts.getFirst().startedAtMillis());
		assertEquals(endOf(saidAt + 3), casts.getFirst().endedAtMillis());
	}

	@Test
	void shutdownReleasesTheRecognizerTheModelAndTheVad() {
		speech(1, 5);
		windows(5);
		worker.shutdown(0);
		assertTrue(vosk.last().closed);
		assertTrue(vosk.closed);
		assertTrue(vad.closed);
	}

	@Test
	void pcmIsSixteenBitClipped() {
		assertEquals(List.of((short) 0, (short) 16383, (short) 32767, (short) -32768, (short) 32767),
			toList(VoskWorker.pcm(new float[] {0f, 0.5f, 1f, -1.5f, 2f}, 0)));
		assertEquals(List.of((short) 32767), toList(VoskWorker.pcm(new float[] {0f, 1f}, 1)));
	}

	private static List<Short> toList(short[] s) {
		List<Short> out = new ArrayList<>();
		for (short x : s) {
			out.add(x);
		}
		return out;
	}

	private static List<String> entries(String json) {
		return JsonParser.parseString(json).getAsJsonArray().asList().stream().map(e -> e.getAsString()).toList();
	}
}
