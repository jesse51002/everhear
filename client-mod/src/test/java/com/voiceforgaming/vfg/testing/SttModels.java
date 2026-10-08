package com.voiceforgaming.vfg.testing;

import com.k2fsa.sherpa.onnx.OfflineModelConfig;
import com.k2fsa.sherpa.onnx.OfflineRecognizerConfig;
import com.k2fsa.sherpa.onnx.OfflineTransducerModelConfig;
import com.voiceforgaming.vfg.setup.Assets;
import com.voiceforgaming.vfg.speech.SpeechEngine;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

/**
 * The speech-to-text models {@code ./gradlew sttCompare} measures, each behind the mod's own
 * VAD and decoding code ({@link SpeechEngine}). Moonshine base is what the mod ships; the
 * others are candidates, pinned by SHA-256 like the mod's own downloads.
 */
public final class SttModels {
	/** One model: its id, a label, what a player would download, and how to build its recognizer. */
	public record Model(String id, String label, Assets.Asset archive) {
		/** Moonshine (otherwise a Parakeet transducer). */
		public boolean moonshine() {
			return id.startsWith("moonshine");
		}

		/** One the mod itself can run, installed by its own installer. */
		public boolean inMod() {
			return Assets.PINNED.contains(archive);
		}
	}

	public static final Model MOONSHINE_BASE = new Model("moonshine-base", "Moonshine base",
		Assets.model(Assets.MOONSHINE_BASE));
	public static final Model MOONSHINE_TINY = new Model("moonshine-tiny", "Moonshine tiny",
		Assets.model(Assets.MOONSHINE_TINY));
	public static final Model PARAKEET_06B = new Model("parakeet", "Parakeet TDT 0.6B v2 int8",
		new Assets.Asset(Assets.Kind.MODEL, "parakeet-tdt-0.6b-v2-int8",
			"asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8.tar.bz2", 482468385L,
			"157c157bc51155e03e37d2466522a3a737dd9c72bb25f36eb18912964161e1ad"));
	public static final Model PARAKEET_110M = new Model("parakeet-110m", "Parakeet TDT 110M int8 (the mod's default)",
		Assets.model(Assets.PARAKEET_110M));

	public static final List<Model> ALL = List.of(MOONSHINE_BASE, MOONSHINE_TINY, PARAKEET_06B, PARAKEET_110M);

	private SttModels() {}

	public static Model byId(String id) {
		for (Model m : ALL) {
			if (m.id().equals(id)) {
				return m;
			}
		}
		throw new IllegalArgumentException("no model " + id + "; one of "
			+ ALL.stream().map(Model::id).toList());
	}

	/** Installs the model (once) and returns its folder. */
	public static Path install(Model m) throws Exception {
		if (m.inMod()) {
			TestAssets.installer().install(m.archive());
			return Assets.folder(TestAssets.root(), m.archive());
		}
		return TestAssets.unpacked(m.archive());
	}

	/** The recognizer config, with hotwords when {@code hotwordsFile} is not null (transducers only). */
	public static OfflineRecognizerConfig recognizer(Model m, Path dir, int threads, Path hotwordsFile,
			float hotwordsScore) {
		if (m.moonshine()) {
			return SpeechEngine.moonshine(dir, threads);
		}
		if (hotwordsFile == null) {
			return SpeechEngine.parakeet(dir, threads); // the mod's own config; 0.6B has the same file names
		}
		String d = dir.toAbsolutePath().toString();
		var transducer = OfflineTransducerModelConfig.builder().setEncoder(d + "/encoder.int8.onnx")
			.setDecoder(d + "/decoder.int8.onnx").setJoiner(d + "/joiner.int8.onnx").build();
		var modelBuilder = OfflineModelConfig.builder().setTransducer(transducer).setTokens(d + "/tokens.txt")
			.setModelType("nemo_transducer").setNumThreads(threads).setDebug(false);
		if (hotwordsFile != null) {
			// Plain-text hotwords are cut into the model's own pieces through a BPE vocab.
			modelBuilder.setModelingUnit("bpe").setBpeVocab(bpeVocab(dir).toString());
		}
		var model = modelBuilder.build();
		var config = OfflineRecognizerConfig.builder().setOfflineModelConfig(model);
		if (hotwordsFile != null) {
			config.setDecodingMethod("modified_beam_search").setMaxActivePaths(4)
				.setHotwordsFile(hotwordsFile.toAbsolutePath().toString()).setHotwordsScore(hotwordsScore);
		}
		return config.build();
	}

	/**
	 * A SentencePiece {@code bpe.vocab} for the model, which its archive does not ship: the
	 * pieces of {@code tokens.txt} in id order, scored by minus their rank, which is how a BPE
	 * SentencePiece model scores its merges.
	 */
	public static Path bpeVocab(Path dir) {
		Path vocab = dir.resolve("bpe.vocab");
		try {
			if (!Files.exists(vocab)) {
				StringBuilder b = new StringBuilder();
				for (String line : Files.readAllLines(dir.resolve("tokens.txt"), StandardCharsets.UTF_8)) {
					int space = line.lastIndexOf(' ');
					String piece = line.substring(0, space);
					int id = Integer.parseInt(line.substring(space + 1).trim());
					b.append(piece).append('\t').append(-id).append('\n');
				}
				Files.writeString(vocab, b, StandardCharsets.UTF_8);
			}
		} catch (IOException e) {
			throw new UncheckedIOException(e);
		}
		return vocab;
	}

	/** The engine (the mod's VAD plus this model), loaded exactly as the mod loads its own. */
	public static SpeechEngine engine(Model m, int threads, Path hotwordsFile, float hotwordsScore) throws Exception {
		return engine(m, threads, hotwordsFile, hotwordsScore, 0.5f);
	}

	public static SpeechEngine engine(Model m, int threads, Path hotwordsFile, float hotwordsScore, float vadThreshold)
			throws Exception {
		String platform = Assets.currentPlatform();
		Assets.Asset natives = Assets.natives(platform);
		Assets.Asset vad = Assets.vad();
		TestAssets.installer().installAll(List.of(natives, vad));
		Path dir = install(m);
		return SpeechEngine.load(Assets.folder(TestAssets.root(), natives),
			() -> recognizer(m, dir, threads, hotwordsFile, hotwordsScore),
			Assets.folder(TestAssets.root(), vad).resolve(Assets.VAD_FILE), vadThreshold);
	}
}
