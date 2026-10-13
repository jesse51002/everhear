package com.voiceforgaming.vfg.testing;

import com.voiceforgaming.vfg.setup.Assets;
import com.voiceforgaming.vfg.speech.Hotwords;
import com.voiceforgaming.vfg.speech.ParakeetPieces;
import com.voiceforgaming.vfg.speech.SpeechEngine;
import java.nio.file.Path;
import java.util.List;

/**
 * The speech-to-text models {@code ./gradlew sttCompare} and {@code ./gradlew micCheck} run, each
 * behind the mod's own VAD and decoding code ({@link SpeechEngine}), installed by the mod's own
 * installer: Parakeet TDT 0.6B v2 (the mod's default), Parakeet TDT 110M (its fallback), and
 * Moonshine base and tiny.
 */
public final class SttModels {
	/** One model: its id, a label, and its pinned archive. */
	public record Model(String id, String label, Assets.Asset archive) {
		/** Moonshine (otherwise a Parakeet transducer). */
		public boolean moonshine() {
			return id.startsWith("moonshine");
		}
	}

	public static final Model MOONSHINE_BASE = new Model("moonshine-base", "Moonshine base",
		Assets.model(Assets.MOONSHINE_BASE));
	public static final Model MOONSHINE_TINY = new Model("moonshine-tiny", "Moonshine tiny",
		Assets.model(Assets.MOONSHINE_TINY));
	public static final Model PARAKEET_06B = new Model("parakeet-0.6b", "Parakeet TDT 0.6B v2 int8",
		Assets.model(Assets.PARAKEET_06B));
	public static final Model PARAKEET_110M = new Model("parakeet-110m", "Parakeet TDT 110M int8",
		Assets.model(Assets.PARAKEET_110M));

	public static final List<Model> ALL = List.of(MOONSHINE_BASE, MOONSHINE_TINY, PARAKEET_110M, PARAKEET_06B);

	private SttModels() {}

	public static Model byId(String id) {
		for (Model m : ALL) {
			if (m.id().equals(id)) {
				return m;
			}
		}
		throw new IllegalArgumentException("no model " + id + "; one of " + ALL.stream().map(Model::id).toList());
	}

	/** Installs the model (once) through the mod's installer and returns its folder. */
	public static Path install(Model m) throws Exception {
		TestAssets.installer().install(m.archive());
		return Assets.folder(TestAssets.root(), m.archive());
	}

	/**
	 * The recognizer as the mod makes it: greedy without {@code hotwords}; with them (Parakeet only),
	 * beam search with {@code paths} paths over the pieces' code names.
	 */
	public static SpeechEngine.Recognition recognition(Model m, Path dir, int threads, boolean hotwords, int paths)
			throws Exception {
		if (!hotwords || m.moonshine()) {
			return SpeechEngine.recognition(dir, !m.moonshine(), threads, false);
		}
		ParakeetPieces pieces = ParakeetPieces.read(dir.resolve("tokens.txt"));
		return new SpeechEngine.Recognition(SpeechEngine.parakeetBeam(dir, threads, pieces.writeCoded(dir), paths),
			pieces);
	}

	/** The engine (the mod's VAD plus this model), greedy, loaded exactly as the mod loads its own. */
	public static SpeechEngine engine(Model m, int threads) throws Exception {
		return engine(m, threads, null, SpeechEngine.MAX_ACTIVE_PATHS, 0.5f);
	}

	/** With {@code hotwords} ({@code null} for none, greedy) and the beam's {@code paths}. */
	public static SpeechEngine engine(Model m, int threads, Hotwords hotwords, int paths, float vadThreshold)
			throws Exception {
		String platform = Assets.currentPlatform();
		Assets.Asset natives = Assets.natives(platform);
		Assets.Asset vad = Assets.vad();
		TestAssets.installer().installAll(List.of(natives, vad));
		Path dir = install(m);
		return SpeechEngine.load(Assets.folder(TestAssets.root(), natives), () -> {
			try { // inside the supplier: after the natives' path is set, as the mod does
				return recognition(m, dir, threads, hotwords != null, paths);
			} catch (Exception e) {
				throw new IllegalStateException(e);
			}
		}, Assets.folder(TestAssets.root(), vad).resolve(Assets.VAD_FILE), vadThreshold, hotwords);
	}
}
