package com.voiceforgaming.vfg.testing;

import com.voiceforgaming.vfg.setup.AssetInstaller;
import com.voiceforgaming.vfg.setup.Assets;
import com.voiceforgaming.vfg.speech.SpeechEngine;
import com.voiceforgaming.vfg.turn.TurnDetector;
import com.voiceforgaming.vfg.turn.TurnDetectors;
import java.io.BufferedInputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.List;
import java.util.Locale;
import org.apache.commons.compress.archivers.tar.TarArchiveEntry;
import org.apache.commons.compress.archivers.tar.TarArchiveInputStream;
import org.apache.commons.compress.compressors.bzip2.BZip2CompressorInputStream;

/**
 * The real engine for the engine test, the speech test and the mic check: the pinned
 * natives, VAD and model installed once into {@code build/vfg-test-assets/} through the
 * mod's own {@link AssetInstaller}, then loaded through the mod's own {@link SpeechEngine}.
 */
public final class TestAssets {
	/** The Kitten TTS voices the speech test speaks with (Apache-2.0), pinned like the mod's own downloads. */
	public static final Assets.Asset KITTEN_TTS = new Assets.Asset(Assets.Kind.MODEL, "kitten-nano-en-v0_2-fp16",
		"tts-models/kitten-nano-en-v0_2-fp16.tar.bz2", 26586708L,
		"0345a8a2f4a710cb8f7912c9a731ded8b3e1e69b33a871efa95c2e64651518fe");

	/** sherpa-onnx's 60 s English VAD test recording: real speech with real pauses. */
	public static final Assets.Asset SILERO_TEST_WAV = new Assets.Asset(Assets.Kind.VAD, "test",
		"asr-models/test_silero_vad.wav", 1920044L,
		"89f17d9c94c4b31eb320f424628bcbc920abaddbee6e2760fd868bfb1d9a2e47");

	private TestAssets() {}

	public static Path root() {
		return Path.of(System.getProperty("vfg.testAssets", "build/vfg-test-assets")).toAbsolutePath();
	}

	public static AssetInstaller installer() {
		long[] lastTenth = {-1};
		return new AssetInstaller(root(), (done, total) -> {
			long tenth = total == 0 ? 0 : done * 10 / total;
			if (tenth != lastTenth[0]) {
				lastTenth[0] = tenth;
				if (done > 0 && done < total) {
					System.out.println("installing the engine and model: " + tenth * 10 + "%");
				}
			}
		}).withExtraModelFiles(List.of(Assets.MODEL_TEST_WAV, "test_wavs/en-english.wav"));
	}

	/** The Kennedy clip ("ask not what your country can do for you…", 24 kHz), pinned. */
	public static final Assets.Asset KENNEDY_WAV = new Assets.Asset(Assets.Kind.VAD, "test", "asr-models/en.wav",
		184608L, "148b936b43ce7c546a866e64da059f0458aee2d65e617f16e9d94f06e8d99ed6");

	public static Wav.Audio kennedy() throws Exception {
		return Wav.read(fetch(KENNEDY_WAV));
	}

	/** A config {@code model} value's folder: {@code parakeet-110m-en}, {@code moonshine-base-en} or {@code moonshine-tiny-en}. */
	public static Path modelDir(String model) {
		return Assets.folder(root(), Assets.model(model));
	}

	/** Installs (once) and loads the engine exactly as the mod does, for a config {@code model} value. */
	public static SpeechEngine engine(String model, int sttThreads, float vadThreshold) throws Exception {
		String platform = Assets.currentPlatform();
		if (platform == null) {
			throw new IllegalStateException("unsupported platform: " + System.getProperty("os.name") + " "
				+ System.getProperty("os.arch"));
		}
		Assets.Asset natives = Assets.natives(platform);
		Assets.Asset vad = Assets.vad();
		Assets.Asset modelAsset = Assets.model(model);
		installer().installAll(List.of(natives, vad, modelAsset));
		return SpeechEngine.load(Assets.folder(root(), natives), Assets.isParakeet(modelAsset),
			Assets.folder(root(), modelAsset), Assets.folder(root(), vad).resolve(Assets.VAD_FILE), sttThreads,
			vadThreshold);
	}

	/** This PC's natives folder, where {@link #engine} installs them. */
	public static Path nativesDir() {
		return Assets.folder(root(), Assets.natives(Assets.currentPlatform()));
	}

	/** Installs (once) the file for a config {@code turn_model} value through the mod's installer, and returns it. */
	public static Path turnModel(String turnModel) throws Exception {
		Assets.Asset asset = Assets.turn(turnModel);
		installer().install(asset);
		return Assets.turnFile(root(), asset);
	}

	/**
	 * Installs (once) and loads a config {@code turn_model}'s detector exactly as the mod does, on
	 * the ONNX Runtime of the natives {@link #engine} loaded; load the engine first, as the mod does.
	 */
	public static TurnDetector turnDetector(String turnModel) throws Exception {
		return TurnDetectors.load(turnModel, nativesDir(), turnModel(turnModel));
	}

	/** A pinned extra file, fetched once into {@code downloads/} through the mod's installer. */
	public static Path fetch(Assets.Asset asset) throws Exception {
		return installer().fetch(asset);
	}

	/** A pinned {@code .tar.bz2}, fetched and unpacked whole (once) under {@code root/<variant>/}. */
	public static Path unpacked(Assets.Asset archive) throws Exception {
		Path dir = root().resolve(archive.variant());
		if (AssetInstaller.isInstalled(dir, archive.sha256())) {
			return dir;
		}
		Path file = fetch(archive);
		Files.createDirectories(dir);
		try (TarArchiveInputStream tar = new TarArchiveInputStream(new BZip2CompressorInputStream(
				new BufferedInputStream(Files.newInputStream(file), 1 << 16)))) {
			TarArchiveEntry e;
			while ((e = tar.getNextEntry()) != null) {
				String name = e.getName();
				while (name.startsWith("./")) {
					name = name.substring(2);
				}
				int slash = name.indexOf('/');
				String rel = slash >= 0 ? name.substring(slash + 1) : name;
				Path target = dir.resolve(rel).normalize();
				if (rel.isEmpty() || !target.startsWith(dir)) {
					continue;
				}
				if (e.isDirectory()) {
					Files.createDirectories(target);
				} else if (e.isFile()) {
					Files.createDirectories(target.getParent());
					Files.copy(tar, target, StandardCopyOption.REPLACE_EXISTING);
				}
			}
		}
		Files.writeString(dir.resolve(AssetInstaller.OK_FILE), archive.sha256() + "\n", StandardCharsets.UTF_8);
		Files.deleteIfExists(file);
		return dir;
	}

	public static String seconds(double s) {
		return String.format(Locale.ROOT, "%.2f s", s);
	}

	/** Lowercase letters, digits and single spaces, for comparing transcripts. */
	public static String normalize(String s) {
		return s.toLowerCase(Locale.ROOT).replace("'", "").replaceAll("[^a-z0-9 ]", " ").replaceAll("\\s+", " ").trim();
	}
}
