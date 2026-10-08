package com.voiceforgaming.vfg.testing;

import com.k2fsa.sherpa.onnx.GeneratedAudio;
import com.k2fsa.sherpa.onnx.OfflineTts;
import com.k2fsa.sherpa.onnx.OfflineTtsConfig;
import com.k2fsa.sherpa.onnx.OfflineTtsKittenModelConfig;
import com.k2fsa.sherpa.onnx.OfflineTtsModelConfig;
import com.voiceforgaming.vfg.setup.Assets;
import java.nio.file.Path;

/**
 * Kitten TTS (nano, 8 voices, 24 kHz) through sherpa-onnx, the same natives the mod
 * loads: it speaks the Minecraft-style phrases the speech test listens to.
 */
public final class Tts implements AutoCloseable {
	private final OfflineTts tts;

	public Tts() throws Exception {
		if (System.getProperty("sherpa_onnx.native.path") == null) {
			Assets.Asset natives = Assets.natives(Assets.currentPlatform());
			TestAssets.installer().install(natives);
			System.setProperty("sherpa_onnx.native.path", Assets.folder(TestAssets.root(), natives).toString());
		}
		Path dir = TestAssets.unpacked(TestAssets.KITTEN_TTS);
		var kitten = OfflineTtsKittenModelConfig.builder()
			.setModel(dir.resolve("model.fp16.onnx").toString())
			.setVoices(dir.resolve("voices.bin").toString())
			.setTokens(dir.resolve("tokens.txt").toString())
			.setDataDir(dir.resolve("espeak-ng-data").toString())
			.build();
		tts = new OfflineTts(OfflineTtsConfig.builder()
			.setModel(OfflineTtsModelConfig.builder().setKitten(kitten).setNumThreads(2).setDebug(false).build())
			.build());
	}

	public int voices() {
		return tts.getNumSpeakers();
	}

	/** {@code text} in voice {@code voice} (0–7) at {@code speed} (1 is normal). */
	public Wav.Audio say(String text, int voice, float speed) {
		GeneratedAudio g = tts.generate(text, voice, speed);
		return new Wav.Audio(g.getSamples(), g.getSampleRate());
	}

	@Override
	public void close() {
		tts.release();
	}
}
