package com.voiceforgaming.vfg.testing;

import com.voiceforgaming.vfg.audio.Resampler;
import java.util.ArrayList;
import java.util.List;
import java.util.Random;

/**
 * Builds the "room" a test speaks into, at 48 kHz: speech clips trimmed to where the
 * speech is, exact gaps of silence between them, and a quiet noise floor under it all.
 * It remembers where each clip's speech starts and ends, so a test can check where the
 * mod split the lines and how long after the end of speech each one was ready.
 */
public final class SpeechTimeline {
	public static final int RATE = 48000;
	private static final float NOISE_RMS = 0.001f; // -60 dBFS, a quiet room through a mic
	private static final float SPEECH_THRESHOLD = 0.02f;

	/** One clip: what is said, the words a transcript must contain, and where its speech is (48 kHz samples). */
	public record Clip(String label, List<String> keywords, long start, long end) {}

	private final List<float[]> parts = new ArrayList<>();
	private final List<Clip> clips = new ArrayList<>();
	private long length;

	public SpeechTimeline silence(double seconds) {
		parts.add(new float[(int) Math.round(seconds * RATE)]);
		length += parts.getLast().length;
		return this;
	}

	/** Adds speech, trimmed to its first and last sample above -34 dBFS. */
	public SpeechTimeline speech(String label, List<String> keywords, Wav.Audio audio) {
		float[] at48k = to48k(audio);
		int from = 0;
		while (from < at48k.length && Math.abs(at48k[from]) < SPEECH_THRESHOLD) {
			from++;
		}
		int to = at48k.length;
		while (to > from && Math.abs(at48k[to - 1]) < SPEECH_THRESHOLD) {
			to--;
		}
		float[] speech = java.util.Arrays.copyOfRange(at48k, from, to);
		clips.add(new Clip(label, keywords, length, length + speech.length));
		parts.add(speech);
		length += speech.length;
		return this;
	}

	public List<Clip> clips() {
		return clips;
	}

	/** Milliseconds from the start of the timeline to a 48 kHz sample. */
	public static long millisOf(long sample48k) {
		return sample48k * 1000 / RATE;
	}

	/** The whole timeline at 48 kHz, with the noise floor added. */
	public float[] at48k() {
		float[] out = new float[(int) length];
		int off = 0;
		for (float[] p : parts) {
			System.arraycopy(p, 0, out, off, p.length);
			off += p.length;
		}
		Random random = new Random(7);
		for (int i = 0; i < out.length; i++) {
			out[i] += (float) (random.nextGaussian() * NOISE_RMS);
		}
		return out;
	}

	/** A second of the same noise floor, which the device loops after the timeline ends. */
	public float[] floor(int rate) {
		float[] f = new float[rate];
		Random r = new Random(11);
		for (int i = 0; i < f.length; i++) {
			f[i] = (float) (r.nextGaussian() * NOISE_RMS);
		}
		return f;
	}

	/** The timeline at 16 kHz, through the mod's own {@link Resampler}. */
	public float[] at16k() {
		return new Resampler().process(at48k());
	}

	/** Brings 16 or 24 kHz audio to 48 kHz by linear interpolation (test material, not the mod's path). */
	public static float[] to48k(Wav.Audio a) {
		if (a.rate() == RATE) {
			return a.samples().clone();
		}
		if (RATE % a.rate() != 0) {
			throw new IllegalArgumentException("can only bring 8, 16 or 24 kHz to 48 kHz, not " + a.rate());
		}
		int f = RATE / a.rate();
		float[] in = a.samples();
		float[] out = new float[in.length * f];
		for (int i = 0; i < in.length; i++) {
			float x = in[i];
			float y = i + 1 < in.length ? in[i + 1] : x;
			for (int k = 0; k < f; k++) {
				out[i * f + k] = x + (y - x) * k / f;
			}
		}
		return out;
	}
}
