package com.voiceforgaming.vfg.setup;

import com.voiceforgaming.vfg.kws.Spotter;
import com.voiceforgaming.vfg.turn.TurnDetectors;
import java.nio.file.Path;
import java.util.List;
import java.util.Locale;

/**
 * The pinned downloads (kind, platform or model, path, bytes, SHA-256), platform
 * detection, and where each one lives under {@code <gameDir>/vfg/}.
 */
public final class Assets {
	public static final String BASE_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/";
	public static final String SHERPA_VERSION = "1.13.8";
	public static final String MODEL_DATE = "2026-02-27";

	public enum Kind { NATIVES, VAD, MODEL, TURN, KWS }

	/**
	 * One pinned file: {@code path} is relative to {@link #BASE_URL}, or a whole URL when it
	 * has a scheme (the turn models are on huggingface.co, not in sherpa-onnx's release).
	 */
	public record Asset(Kind kind, String variant, String path, long bytes, String sha256) {
		/** The file name, the last segment of the path. */
		public String fileName() {
			return path.substring(path.lastIndexOf('/') + 1);
		}

		/** Where it is downloaded from: {@code path} itself when it is a whole URL, else below {@code baseUrl}. */
		public String url(String baseUrl) {
			return path.contains("://") ? path : baseUrl + path;
		}
	}

	/** The keyword spotter's model: icefall's English GigaSpeech zipformer, 3.3M parameters (Apache-2.0). */
	public static final String KWS_GIGASPEECH = "gigaspeech-3.3m";

	public static final List<Asset> PINNED = List.of(
		new Asset(Kind.NATIVES, "win-x64", "v1.13.8/sherpa-onnx-native-lib-win-x64-1.13.8.jar", 8277046L,
			"33fbdbd5410e9ba9bdda94aa164ec8f7825bb49246420d8ce9bdd88219d97039"),
		new Asset(Kind.NATIVES, "linux-x64", "v1.13.8/sherpa-onnx-native-lib-linux-x64-1.13.8.jar", 10515871L,
			"30c93b59381113f9c20aedbbf9fc1ad399158f6bc03dddc0f8934a6e28e069ba"),
		new Asset(Kind.NATIVES, "osx-aarch64", "v1.13.8/sherpa-onnx-native-lib-osx-aarch64-1.13.8.jar", 9460832L,
			"42e272180c8836127f024f3335d7afcdfb30fe0164b78330d5d449034e34ce34"),
		new Asset(Kind.NATIVES, "osx-x64", "v1.13.8/sherpa-onnx-native-lib-osx-x64-1.13.8.jar", 10782466L,
			"9190c28951d85efdbd376bae6b6dff12993311ad605945289e8459bf9c886a96"),
		new Asset(Kind.VAD, "silero", "asr-models/silero_vad.onnx", 643854L,
			"9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6"),
		new Asset(Kind.MODEL, "base", "asr-models/sherpa-onnx-moonshine-base-en-quantized-2026-02-27.tar.bz2", 111266225L,
			"43232c1d13013d37317163baec3135bd771a186a4356f28c889bab453bb0e891"),
		new Asset(Kind.MODEL, "tiny", "asr-models/sherpa-onnx-moonshine-tiny-en-quantized-2026-02-27.tar.bz2", 29858559L,
			"9ec31b342d8fa3240c3b81b8f82e1cf7e3ac467c93ca5a999b741d5887164f8d"),
		new Asset(Kind.MODEL, "parakeet-110m",
			"asr-models/sherpa-onnx-nemo-parakeet_tdt_transducer_110m-en-36000-int8.tar.bz2", 108035095L,
			"f628312e9fdf8686374cb01a69425c41732529d540860311f16f37cbc32cfe9b"),
		new Asset(Kind.TURN, TurnDetectors.SMART_TURN, "https://huggingface.co/soniqo/Smart-Turn-v3.2-ONNX/resolve/"
			+ "b48fdbe20772bcec1fef02f4a1a355236ef6359e/smart-turn-v3.2-int8.onnx", 11123370L,
			"00cd131551e8d1e9011f31345edb632a26116dd83e159782c4ddff610718ea31"),
		new Asset(Kind.TURN, TurnDetectors.TURN_1_MINI, "https://huggingface.co/p99lab/turn-1-mini/resolve/"
			+ "076c86d1744a231d3a4cce31f3bbbd1211b4c281/onnx/turn-1-mini.step.int8dyn.onnx", 7000932L,
			"a59a5b627190e7bf52a46dd25026ec8d4d0b01e1b0146643bb39161808c0619e"),
		new Asset(Kind.KWS, KWS_GIGASPEECH,
			"kws-models/sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01.tar.bz2", 17626723L,
			"f170013b4716e41b62b9bfd809687c207cef798ef9bc6534d524e17af9b6561a"));

	/** The config's {@code model} values, the default first. */
	public static final String PARAKEET_110M = "parakeet-110m-en";
	public static final String MOONSHINE_BASE = "moonshine-base-en";
	public static final String MOONSHINE_TINY = "moonshine-tiny-en";

	/** The files taken from a Moonshine archive. */
	public static final List<String> MOONSHINE_FILES =
		List.of("encoder_model.ort", "decoder_model_merged.ort", "tokens.txt", "LICENSE");

	/** The files taken from a Parakeet (NeMo TDT transducer) archive, which ships no LICENSE (CC-BY-4.0). */
	public static final List<String> PARAKEET_FILES =
		List.of("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt");

	/** A model's own test clip, unpacked only by the tests. */
	public static final String MODEL_TEST_WAV = "test_wavs/0.wav";

	public static final String VAD_FILE = "silero_vad.onnx";

	private Assets() {}

	/**
	 * sherpa-onnx's own name for this platform: {@code win-x64}, {@code linux-x64},
	 * {@code osx-aarch64} or {@code osx-x64}; {@code null} for any other.
	 */
	public static String platform(String osName, String osArch) {
		String os = osName == null ? "" : osName.toLowerCase(Locale.ROOT);
		String arch = osArch == null ? "" : osArch.toLowerCase(Locale.ROOT);
		boolean x64 = arch.equals("amd64") || arch.equals("x86_64") || arch.equals("x64");
		boolean arm64 = arch.equals("aarch64") || arch.equals("arm64");
		if (os.contains("win")) {
			return x64 ? "win-x64" : null;
		}
		if (os.contains("mac") || os.contains("darwin")) {
			return x64 ? "osx-x64" : arm64 ? "osx-aarch64" : null;
		}
		if (os.contains("linux")) {
			return x64 ? "linux-x64" : null;
		}
		return null;
	}

	public static String currentPlatform() {
		return platform(System.getProperty("os.name"), System.getProperty("os.arch"));
	}

	public static boolean isWindows() {
		return System.getProperty("os.name", "").toLowerCase(Locale.ROOT).contains("win");
	}

	public static Asset natives(String platform) {
		return find(Kind.NATIVES, platform);
	}

	public static Asset vad() {
		return find(Kind.VAD, "silero");
	}

	/** The archive for a config {@code model} value: {@code parakeet-110m-en}, {@code moonshine-base-en} or {@code moonshine-tiny-en}. */
	public static Asset model(String modelSetting) {
		return find(Kind.MODEL, switch (modelSetting) {
			case MOONSHINE_BASE -> "base";
			case MOONSHINE_TINY -> "tiny";
			default -> "parakeet-110m";
		});
	}

	/** Whether a model archive is a Parakeet transducer (otherwise Moonshine). */
	public static boolean isParakeet(Asset model) {
		return model.kind() == Kind.MODEL && model.variant().startsWith("parakeet");
	}

	/** The files to take from a model archive (or the keyword model's). */
	public static List<String> modelFiles(Asset model) {
		if (model.kind() == Kind.KWS) {
			return Spotter.FILES;
		}
		return isParakeet(model) ? PARAKEET_FILES : MOONSHINE_FILES;
	}

	/** The keyword spotter's model. */
	public static Asset kws() {
		return find(Kind.KWS, KWS_GIGASPEECH);
	}

	private static Asset find(Kind kind, String variant) {
		for (Asset a : PINNED) {
			if (a.kind() == kind && a.variant().equals(variant)) {
				return a;
			}
		}
		throw new IllegalArgumentException("no pinned " + kind + " for " + variant);
	}

	/**
	 * The pinned file for a config {@code turn_model} value ({@code smart-turn-v3.2} or
	 * {@code turn-1-mini}), or {@code null} for {@code off}.
	 */
	public static Asset turn(String turnModel) {
		return turnModel.equals(TurnDetectors.OFF) ? null : find(Kind.TURN, turnModel);
	}

	/** Where a turn model's {@code .onnx} file is once installed: in its folder, under its own name. */
	public static Path turnFile(Path vfgRoot, Asset turnModel) {
		return folder(vfgRoot, turnModel).resolve(turnModel.fileName());
	}

	// --- where things live under <gameDir>/vfg/ ---

	public static Path root(Path gameDir) {
		return gameDir.resolve("vfg");
	}

	public static Path downloads(Path vfgRoot) {
		return vfgRoot.resolve("downloads");
	}

	/** The folder an asset is unpacked into, relative to {@code <gameDir>/vfg/}. */
	public static Path folder(Path vfgRoot, Asset a) {
		return switch (a.kind()) {
			case NATIVES -> vfgRoot.resolve("natives").resolve("sherpa-onnx-" + SHERPA_VERSION + "-" + a.variant());
			case VAD -> vfgRoot.resolve("models").resolve("silero-vad");
			case MODEL -> vfgRoot.resolve("models").resolve(isParakeet(a)
				? "parakeet-tdt-110m-en-36000-int8"
				: "moonshine-" + a.variant() + "-en-" + MODEL_DATE);
			case TURN -> vfgRoot.resolve("models").resolve("turn").resolve(a.variant());
			case KWS -> vfgRoot.resolve("models").resolve("kws").resolve(kwsFolder(a));
		};
	}

	/** The keyword model's folder name: its archive's name without {@code .tar.bz2}. */
	private static String kwsFolder(Asset a) {
		String name = a.fileName();
		return name.endsWith(".tar.bz2") ? name.substring(0, name.length() - ".tar.bz2".length()) : name;
	}
}
