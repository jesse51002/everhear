package com.voiceforgaming.vfg.testing;

import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.Locale;

/**
 * Kokoro TTS takes, kept on disk the first time in {@code speech-test-tts/} under the test assets,
 * named as the speech test names its own: a take costs a second or more to make, and every later
 * run hears the same audio.
 */
public final class KokoroTakes implements AutoCloseable {
	public static final int VOICES = 11;
	private Tts tts;

	/** {@code text} in {@code voice} (from 0) at normal speed. */
	public Wav.Audio say(String text, int voice) throws Exception {
		return say(text, voice, 1.0f);
	}

	/** {@code text} in {@code voice} at {@code speed} (1 is normal). */
	public Wav.Audio say(String text, int voice, float speed) throws Exception {
		String name = "kokoro-v" + voice + (speed == 1.0f ? "" : String.format(Locale.ROOT, "-x%.2f", speed)) + "-"
			+ TestAssets.normalize(text).replace(' ', '-');
		Path file = TestAssets.root().resolve("speech-test-tts").resolve(name + ".wav");
		if (!Files.exists(file)) {
			if (tts == null) {
				tts = new Tts(Tts.Model.KOKORO, 2);
			}
			Wav.Audio a = tts.say(text, voice, speed);
			Files.createDirectories(file.getParent());
			Path part = file.resolveSibling(file.getFileName() + ".part");
			Wav.write(part, a.samples(), a.rate());
			Files.move(part, file, StandardCopyOption.REPLACE_EXISTING);
		}
		return Wav.read(file);
	}

	@Override
	public void close() {
		if (tts != null) {
			tts.close();
			tts = null;
		}
	}
}
