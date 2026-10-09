package com.voiceforgaming.vfg.testing;

import com.k2fsa.sherpa.onnx.GeneratedAudio;
import com.k2fsa.sherpa.onnx.OfflineTts;
import com.k2fsa.sherpa.onnx.OfflineTtsConfig;
import com.k2fsa.sherpa.onnx.OfflineTtsKittenModelConfig;
import com.k2fsa.sherpa.onnx.OfflineTtsKokoroModelConfig;
import com.k2fsa.sherpa.onnx.OfflineTtsModelConfig;
import com.voiceforgaming.vfg.setup.Assets;
import java.nio.file.Path;

/**
 * Text to speech through sherpa-onnx, the same natives the mod loads. Kitten (nano, 8 voices,
 * 24 kHz) speaks the Minecraft-style phrases the speech test listens to. Kokoro (int8, English,
 * 11 voices, 24 kHz) is slower and sounds more like a person finishing a sentence: Smart Turn
 * scores most of Kitten's finished sentences as unfinished, and most of Kokoro's as finished.
 */
public final class Tts implements AutoCloseable {
	/** Which model speaks. */
	public enum Model { KITTEN, KOKORO }

	private final OfflineTts tts;

	/** Kitten. */
	public Tts() throws Exception {
		this(Model.KITTEN, 2);
	}

	/** {@code model}, on {@code threads} threads. */
	public Tts(Model model, int threads) throws Exception {
		if (System.getProperty("sherpa_onnx.native.path") == null) {
			Assets.Asset natives = Assets.natives(Assets.currentPlatform());
			TestAssets.installer().install(natives);
			System.setProperty("sherpa_onnx.native.path", Assets.folder(TestAssets.root(), natives).toString());
		}
		var config = OfflineTtsModelConfig.builder().setNumThreads(threads).setDebug(false);
		if (model == Model.KITTEN) {
			Path dir = TestAssets.unpacked(TestAssets.KITTEN_TTS);
			config.setKitten(OfflineTtsKittenModelConfig.builder()
				.setModel(dir.resolve("model.fp16.onnx").toString())
				.setVoices(dir.resolve("voices.bin").toString())
				.setTokens(dir.resolve("tokens.txt").toString())
				.setDataDir(dir.resolve("espeak-ng-data").toString())
				.build());
		} else {
			Path dir = TestAssets.unpacked(TestAssets.KOKORO_TTS);
			config.setKokoro(OfflineTtsKokoroModelConfig.builder()
				.setModel(dir.resolve("model.int8.onnx").toString())
				.setVoices(dir.resolve("voices.bin").toString())
				.setTokens(dir.resolve("tokens.txt").toString())
				.setDataDir(dir.resolve("espeak-ng-data").toString())
				.build());
		}
		tts = new OfflineTts(OfflineTtsConfig.builder().setModel(config.build()).build());
	}

	public int voices() {
		return tts.getNumSpeakers();
	}

	/** {@code text} in voice {@code voice} (from 0) at {@code speed} (1 is normal). */
	public Wav.Audio say(String text, int voice, float speed) {
		GeneratedAudio g = tts.generate(text, voice, speed);
		return new Wav.Audio(g.getSamples(), g.getSampleRate());
	}

	@Override
	public void close() {
		tts.release();
	}
}
