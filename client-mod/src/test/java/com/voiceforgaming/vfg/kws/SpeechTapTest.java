package com.voiceforgaming.vfg.kws;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.voiceforgaming.vfg.VoicePipeline;
import com.voiceforgaming.vfg.audio.WindowListener.StopReason;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.atomic.AtomicBoolean;
import org.junit.jupiter.api.Test;

/** One VAD on the capture thread says for the spotter and Vosk when someone speaks. */
class SpeechTapTest {
	/** Speech in the windows listed (a window's first sample is its number). */
	static final class Vad implements SpotterVad {
		final Set<Integer> speech = new HashSet<>();
		final List<Integer> heard = new ArrayList<>();
		int resets;
		boolean closed;

		@Override
		public boolean speaking(float[] window) {
			heard.add(Math.round(window[0]));
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

	static VoicePipeline.Tap recorder(List<String> out, String name) {
		return new VoicePipeline.Tap() {
			@Override
			public void onStarted(boolean forLine) {
				out.add(name + " started");
			}

			@Override
			public void onWindow(float[] window, long readWallMillis, boolean speaking) {
				out.add(name + " " + Math.round(window[0]) + (speaking ? " speech" : " -"));
			}

			@Override
			public void onStopped(StopReason reason) {
				out.add(name + " stopped");
			}
		};
	}

	@Test
	void theVadRunsOnceAWindowAndBothHearItsAnswer() {
		List<String> out = new ArrayList<>();
		AtomicBoolean listening = new AtomicBoolean(true);
		SpeechTap tap = new SpeechTap(listening::get, recorder(out, "kws"), recorder(out, "vosk"));
		tap.onStarted(true);
		tap.onWindow(new float[] {1}, 32, true); // no VAD yet: the capture's flag
		Vad vad = new Vad();
		vad.speech.add(3);
		tap.setVad(vad);
		tap.onWindow(new float[] {2}, 64, true);
		tap.onWindow(new float[] {3}, 96, false);
		listening.set(false);
		tap.onWindow(new float[] {4}, 128, true);
		listening.set(true);
		tap.onWindow(new float[] {5}, 160, false);
		tap.onStopped(StopReason.LEAVE);
		assertEquals(List.of("kws started", "vosk started", "kws 1 speech", "vosk 1 speech", "kws 2 -", "vosk 2 -",
			"kws 3 speech", "vosk 3 speech", "kws 4 speech", "vosk 4 speech", "kws 5 -", "vosk 5 -", "kws stopped",
			"vosk stopped"), out);
		assertEquals(List.of(2, 3, 5), vad.heard, "once a window, and not while nobody listens");
		assertEquals(2, vad.resets, "when it is first used, and again after a rest");
		tap.close();
		assertTrue(vad.closed);
	}

	@Test
	void aNewCaptureResetsTheVadAndANewVadClosesTheOld() {
		SpeechTap tap = new SpeechTap(() -> true, recorder(new ArrayList<>(), "kws"));
		Vad first = new Vad();
		tap.setVad(first);
		tap.onStarted(true);
		tap.onWindow(new float[] {1}, 32, false);
		tap.onStarted(true);
		tap.onWindow(new float[] {2}, 64, false);
		assertEquals(2, first.resets);
		Vad second = new Vad();
		tap.setVad(second);
		assertTrue(first.closed);
		tap.onWindow(new float[] {3}, 96, false);
		assertEquals(1, second.resets);
		assertEquals(List.of(3), second.heard);
	}
}
