package com.voiceforgaming.vfg.kws;

import java.util.ArrayDeque;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;

/**
 * One spoken phrase is one cast, whichever engine hears it first. The keyword spotter
 * ({@link SpotterWorker}, on {@code vfg-kws}) and Vosk ({@link VoskWorker}, on {@code vfg-vosk})
 * each claim a cast here before posting it; a claim for a saying the other engine claimed already
 * is refused, and its engine posts nothing.
 *
 * <p><b>The same saying</b> is a claim by the other engine of the same incantation whose window on
 * the audio clock overlaps by at least half of the shorter window (Vosk's widened by
 * {@link #VOSK_SLACK_MILLIS} each side, as its times are estimates), and which no claim of this
 * engine's was taken for already: each saying is heard at most once by each engine, so one claim
 * stands for at most one of the other's. That keeps "fireball fireball" said fast two sayings when
 * the spotter heard the first and Vosk both, though Vosk's windows, estimated, are wide enough to
 * overlap both. An engine's own claims are never compared: each keeps its own sayings apart (the
 * spotter by never reaching back past its last cast, Vosk by counting each phrase in an utterance).
 * Another incantation over the same audio is a cast of its own, as it is for the bridge: "hot
 * fireball" heard by Vosk where the spotter heard "fireball" (the spotter drops a held hit inside
 * a longer phrase Vosk cast itself). But one whose words are inside a longer one the other engine
 * cast over the same audio is not: "fireball", heard by the spotter in the tail of the "hot
 * fireball" Vosk cast, is that saying again.
 *
 * <p>{@link #reachBack()} is what the spotter's reach-back rule reads: neither of its ways hears audio
 * from before the end of the player's last cast, whichever engine cast it. A Vosk cast's end is an
 * estimate, late by up to 0.27 s one time in ten (a partial shows a one-word phrase up to 0.42 s
 * after it ends, at the 90th percentile; the cast ends 0.15 s before the newest sample), which would
 * run into a phrase said right after and hide it from the spotter. So for a Vosk cast the reach-back
 * stops {@link #VOSK_REACH_MARGIN_MILLIS} before its end; the spotter may then hear the tail of
 * Vosk's saying again, which this gate refuses as the same saying.
 *
 * <p>Thread-safe: both engines' threads call it.
 */
public final class CastGate {
	/** Claims whose end is this far behind the newest one's are forgotten. */
	static final long KEEP_MILLIS = 30_000;
	/**
	 * How far Vosk's window is widened each side when its claim is checked against the spotter's: its
	 * times are estimates, a final result's too (0.3 s, the bridge's own slack for duplicates).
	 */
	static final long VOSK_SLACK_MILLIS = 300;
	/** How far before a Vosk cast's end the spotter's reach-back stops: 0.3 s. */
	public static final long VOSK_REACH_MARGIN_MILLIS = 300;

	/** A cast claimed: its phrase (normalised words), its window on the audio clock, and which way heard it. */
	public static final class Claim {
		private final String phrase;
		private final long startedAt;
		private final long endedAt;
		private final SpotterWorker.Via via;
		private boolean taken;

		Claim(String phrase, long startedAt, long endedAt, SpotterWorker.Via via) {
			this.phrase = phrase;
			this.startedAt = startedAt;
			this.endedAt = endedAt;
			this.via = via;
		}

		public String phrase() {
			return phrase;
		}

		public long startedAt() {
			return startedAt;
		}

		public long endedAt() {
			return endedAt;
		}

		public SpotterWorker.Via via() {
			return via;
		}

		@Override
		public String toString() {
			return "Claim[" + phrase + " " + startedAt + "-" + endedAt + " by " + via.words() + (taken ? ", taken" : "")
				+ "]";
		}
	}

	private final ArrayDeque<Claim> claims = new ArrayDeque<>();
	private long lastEnd = Long.MIN_VALUE;
	private long reach = Long.MIN_VALUE;
	private long reachMargin = VOSK_REACH_MARGIN_MILLIS;

	/**
	 * Claims a cast of {@code phrase} said over {@code startedAt} to {@code endedAt}, heard by
	 * {@code via}; {@code false}, and nothing recorded, when the other engine claimed that saying.
	 */
	public synchronized boolean claim(String phrase, long startedAt, long endedAt, SpotterWorker.Via via) {
		boolean mineVosk = vosk(via);
		long mine = mineVosk ? VOSK_SLACK_MILLIS : 0;
		long theirs = mineVosk ? 0 : VOSK_SLACK_MILLIS;
		for (Claim c : claims) {
			if (vosk(c.via) == mineVosk || !overlapping(c.startedAt - theirs, c.endedAt + theirs, startedAt - mine,
					endedAt + mine)) {
				continue;
			}
			if (!c.taken && c.phrase.equals(phrase)) {
				c.taken = true;
				return false;
			}
			if (!c.phrase.equals(phrase) && inside(phrase, c.phrase)) {
				return false; // its words were cast already, in the longer phrase
			}
		}
		claims.addLast(new Claim(phrase, startedAt, endedAt, via));
		lastEnd = Math.max(lastEnd, endedAt);
		reach = Math.max(reach, vosk(via) ? Math.max(startedAt, endedAt - reachMargin) : endedAt);
		while (!claims.isEmpty() && claims.peekFirst().endedAt < lastEnd - KEEP_MILLIS) {
			claims.removeFirst();
		}
		return true;
	}

	/** The end of the newest cast claimed, on the audio clock; {@link Long#MIN_VALUE} before the first. */
	public synchronized long lastEnd() {
		return lastEnd;
	}

	/**
	 * Where the spotter's reach-back starts: the end of the newest cast, less
	 * {@link #VOSK_REACH_MARGIN_MILLIS} for a Vosk cast; {@link Long#MIN_VALUE} before the first.
	 */
	public synchronized long reachBack() {
		return reach;
	}

	/** For measuring: the margin a Vosk cast's reach-back leaves, in place of {@link #VOSK_REACH_MARGIN_MILLIS}. */
	synchronized void reachMargin(long millis) {
		reachMargin = millis;
	}

	/** The claims kept, oldest first. */
	public synchronized List<Claim> claims() {
		return List.copyOf(claims);
	}

	private static boolean vosk(SpotterWorker.Via via) {
		return via == SpotterWorker.Via.VOSK;
	}

	/** Whether two windows overlap by at least half the shorter one (a window may be a single instant). */
	static boolean overlapping(long s1, long e1, long s2, long e2) {
		long overlap = Math.min(e1, e2) - Math.max(s1, s2);
		long shorter = Math.min(e1 - s1, e2 - s2);
		return overlap >= 0 && 2 * overlap >= shorter;
	}

	/** The same incantation, or one whose words hold the other's as a run of whole words ("fire" in "fire ball"). */
	public static boolean related(String a, String b) {
		return inside(a, b) || inside(b, a);
	}

	/** Whether {@code shorter}'s words are a run of whole words of {@code longer}'s (or the same). */
	static boolean inside(String shorter, String longer) {
		return Collections.indexOfSubList(Arrays.asList(longer.split(" ")), Arrays.asList(shorter.split(" "))) >= 0;
	}
}
