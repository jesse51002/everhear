package com.voiceforgaming.vfg.setup;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.sun.net.httpserver.HttpServer;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;
import org.apache.commons.compress.archivers.tar.TarArchiveEntry;
import org.apache.commons.compress.archivers.tar.TarArchiveOutputStream;
import org.apache.commons.compress.compressors.bzip2.BZip2CompressorOutputStream;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** The installer against a local HttpServer serving small stand-ins for the natives, the VAD and a model. */
class AssetInstallerTest {
	@TempDir
	Path dir;

	private HttpServer server;
	private String base;
	private final Map<String, byte[]> files = new ConcurrentHashMap<>();
	private final List<String> requests = Collections.synchronizedList(new ArrayList<>());
	private final List<Long> sleeps = Collections.synchronizedList(new ArrayList<>());

	@BeforeEach
	void start() throws IOException {
		server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
		server.createContext("/", exchange -> {
			String path = exchange.getRequestURI().getPath().substring(1);
			requests.add(path);
			if (path.startsWith("moved/")) { // a redirect, as GitHub answers
				exchange.getResponseHeaders().add("Location", "/" + path.substring("moved/".length()));
				exchange.sendResponseHeaders(302, -1);
				exchange.close();
				return;
			}
			byte[] body = files.get(path);
			if (body == null) {
				exchange.sendResponseHeaders(404, -1);
				exchange.close();
				return;
			}
			exchange.sendResponseHeaders(200, body.length);
			try (OutputStream out = exchange.getResponseBody()) {
				out.write(body);
			}
		});
		server.start();
		base = "http://127.0.0.1:" + server.getAddress().getPort() + "/";
	}

	@AfterEach
	void stop() {
		server.stop(0);
	}

	private AssetInstaller installer() {
		return new AssetInstaller(base, dir.resolve("vfg"), sleeps::add, null);
	}

	private static String sha256(byte[] data) throws Exception {
		return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(data));
	}

	private static byte[] nativesJar() throws IOException {
		ByteArrayOutputStream b = new ByteArrayOutputStream();
		try (ZipOutputStream zip = new ZipOutputStream(b)) {
			zip.putNextEntry(new ZipEntry("META-INF/MANIFEST.MF"));
			zip.write("Manifest-Version: 1.0\n".getBytes(StandardCharsets.UTF_8));
			for (String name : List.of("sherpa-onnx/native/linux-x64/libonnxruntime.so",
					"sherpa-onnx/native/linux-x64/libsherpa-onnx-jni.so", "sherpa-onnx/native/win-x64/onnxruntime.dll")) {
				zip.putNextEntry(new ZipEntry(name));
				zip.write(("binary of " + name).getBytes(StandardCharsets.UTF_8));
			}
		}
		return b.toByteArray();
	}

	private static byte[] modelArchive() throws IOException {
		ByteArrayOutputStream b = new ByteArrayOutputStream();
		try (TarArchiveOutputStream tar = new TarArchiveOutputStream(new BZip2CompressorOutputStream(b))) {
			String top = "sherpa-onnx-moonshine-base-en-quantized-2026-02-27/";
			for (String name : List.of("encoder_model.ort", "decoder_model_merged.ort", "tokens.txt", "LICENSE",
					"test_wavs/0.wav", "README.md")) {
				byte[] data = ("content of " + name).getBytes(StandardCharsets.UTF_8);
				TarArchiveEntry e = new TarArchiveEntry(top + name);
				e.setSize(data.length);
				tar.putArchiveEntry(e);
				tar.write(data);
				tar.closeArchiveEntry();
			}
		}
		return b.toByteArray();
	}

	private Assets.Asset serve(Assets.Kind kind, String variant, String path, byte[] data) throws Exception {
		files.put(path, data);
		return new Assets.Asset(kind, variant, path, data.length, sha256(data));
	}

	@Test
	void aGoodHashUnpacksAndWritesOk() throws Exception {
		Assets.Asset natives = serve(Assets.Kind.NATIVES, "linux-x64", "v1.13.8/natives-linux-x64.jar", nativesJar());
		Assets.Asset vad = serve(Assets.Kind.VAD, "silero", "asr-models/silero_vad.onnx", "vad model".getBytes());
		Assets.Asset model = serve(Assets.Kind.MODEL, "base", "asr-models/model-base.tar.bz2", modelArchive());
		List<long[]> progress = new ArrayList<>();
		AssetInstaller installer = new AssetInstaller(base, dir.resolve("vfg"), sleeps::add,
			(d, t) -> progress.add(new long[] {d, t}));
		installer.installAll(List.of(natives, vad, model));

		Path root = dir.resolve("vfg");
		Path nativesDir = root.resolve("natives/sherpa-onnx-1.13.8-linux-x64");
		assertTrue(Files.isRegularFile(nativesDir.resolve("libonnxruntime.so")));
		assertTrue(Files.isRegularFile(nativesDir.resolve("libsherpa-onnx-jni.so")));
		assertFalse(Files.exists(nativesDir.resolve("onnxruntime.dll")), "only this platform's natives");
		assertEquals(natives.sha256(), Files.readString(nativesDir.resolve(".ok")).trim());

		Path vadDir = root.resolve("models/silero-vad");
		assertArrayEquals("vad model".getBytes(), Files.readAllBytes(vadDir.resolve("silero_vad.onnx")));
		assertEquals(vad.sha256(), Files.readString(vadDir.resolve(".ok")).trim());

		Path modelDir = root.resolve("models/moonshine-base-en-2026-02-27");
		for (String f : Assets.MOONSHINE_FILES) {
			assertEquals("content of " + f, Files.readString(modelDir.resolve(f)));
		}
		assertFalse(Files.exists(modelDir.resolve("test_wavs/0.wav")), "only the four files");
		assertFalse(Files.exists(modelDir.resolve("README.md")));
		assertEquals(model.sha256(), Files.readString(modelDir.resolve(".ok")).trim());

		try (var left = Files.list(root.resolve("downloads"))) {
			assertEquals(0, left.count(), "downloads are deleted once unpacked");
		}
		long total = natives.bytes() + vad.bytes() + model.bytes();
		assertArrayEquals(new long[] {total, total}, progress.get(progress.size() - 1));
	}

	@Test
	void aBadHashWritesNothingAndFailsAfterThreeTries() throws Exception {
		Assets.Asset good = serve(Assets.Kind.VAD, "silero", "asr-models/silero_vad.onnx", "vad model".getBytes());
		Assets.Asset wrong = new Assets.Asset(good.kind(), good.variant(), good.path(), good.bytes(),
			"0".repeat(64));
		AssetInstaller.SetupException e = assertThrows(AssetInstaller.SetupException.class,
			() -> installer().install(wrong));
		assertTrue(e.getMessage().contains("silero_vad.onnx"), e.getMessage());
		assertEquals(3, requests.size());
		assertEquals(List.of(2000L, 4000L), sleeps);
		Path root = dir.resolve("vfg");
		assertFalse(Files.exists(root.resolve("models/silero-vad/.ok")));
		assertFalse(Files.exists(root.resolve("models/silero-vad/silero_vad.onnx")));
		try (var left = Files.list(root.resolve("downloads"))) {
			assertEquals(0, left.count(), "the bad download and its .part are deleted");
		}
	}

	@Test
	void aMissingFileFailsAfterThreeTries() {
		Assets.Asset missing = new Assets.Asset(Assets.Kind.VAD, "silero", "asr-models/nothing.onnx", 1, "0".repeat(64));
		assertThrows(AssetInstaller.SetupException.class, () -> installer().install(missing));
		assertEquals(3, requests.size());
	}

	@Test
	void anExistingOkMakesNoRequest() throws Exception {
		Assets.Asset vad = serve(Assets.Kind.VAD, "silero", "asr-models/silero_vad.onnx", "vad model".getBytes());
		Path folder = Assets.folder(dir.resolve("vfg"), vad);
		Files.createDirectories(folder);
		Files.writeString(folder.resolve(".ok"), vad.sha256() + "\n");
		installer().install(vad);
		assertTrue(requests.isEmpty());
	}

	@Test
	void aMatchingFileInDownloadsMakesNoRequest() throws Exception {
		byte[] jar = nativesJar();
		Assets.Asset natives = serve(Assets.Kind.NATIVES, "linux-x64", "v1.13.8/natives-linux-x64.jar", jar);
		Path downloads = dir.resolve("vfg/downloads");
		Files.createDirectories(downloads);
		Files.write(downloads.resolve(natives.fileName()), jar); // put there by hand
		installer().install(natives);
		assertTrue(requests.isEmpty());
		assertTrue(Files.isRegularFile(dir.resolve("vfg/natives/sherpa-onnx-1.13.8-linux-x64/libonnxruntime.so")));
		assertFalse(Files.exists(downloads.resolve(natives.fileName())));
	}

	@Test
	void aWrongFileInDownloadsIsDownloadedAgain() throws Exception {
		Assets.Asset vad = serve(Assets.Kind.VAD, "silero", "asr-models/silero_vad.onnx", "vad model".getBytes());
		Path downloads = dir.resolve("vfg/downloads");
		Files.createDirectories(downloads);
		Files.writeString(downloads.resolve("silero_vad.onnx"), "truncated");
		installer().install(vad);
		assertEquals(1, requests.size());
		assertTrue(AssetInstaller.isInstalled(Assets.folder(dir.resolve("vfg"), vad), vad.sha256()));
	}

	@Test
	void redirectsAreFollowed() throws Exception {
		byte[] data = "vad model".getBytes();
		files.put("asr-models/silero_vad.onnx", data);
		Assets.Asset vad = new Assets.Asset(Assets.Kind.VAD, "silero", "moved/asr-models/silero_vad.onnx", data.length,
			sha256(data));
		installer().install(vad);
		assertEquals(2, requests.size());
		assertTrue(Files.isRegularFile(dir.resolve("vfg/models/silero-vad/silero_vad.onnx")));
	}

	@Test
	void theEngineTestCanAskForTheTestClipToo() throws Exception {
		Assets.Asset model = serve(Assets.Kind.MODEL, "base", "asr-models/model-base.tar.bz2", modelArchive());
		installer().withExtraModelFiles(List.of(Assets.MODEL_TEST_WAV, "test_wavs/not-in-this-archive.wav")).install(model);
		assertEquals("content of test_wavs/0.wav",
			Files.readString(dir.resolve("vfg/models/moonshine-base-en-2026-02-27/test_wavs/0.wav")));
	}

	/** Parakeet's archive: its own four files, entries written as "./top/file" by tar. */
	private static byte[] parakeetArchive() throws IOException {
		ByteArrayOutputStream b = new ByteArrayOutputStream();
		try (TarArchiveOutputStream tar = new TarArchiveOutputStream(new BZip2CompressorOutputStream(b))) {
			String top = "./sherpa-onnx-nemo-parakeet_tdt_transducer_110m-en-36000-int8/";
			for (String name : List.of("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt",
					"test_wavs/0.wav")) {
				byte[] data = ("content of " + name).getBytes(StandardCharsets.UTF_8);
				TarArchiveEntry e = new TarArchiveEntry(top + name);
				e.setSize(data.length);
				tar.putArchiveEntry(e);
				tar.write(data);
				tar.closeArchiveEntry();
			}
		}
		return b.toByteArray();
	}

	@Test
	void aParakeetArchiveUnpacksItsOwnFiles() throws Exception {
		Assets.Asset model = serve(Assets.Kind.MODEL, "parakeet-110m", "asr-models/parakeet.tar.bz2", parakeetArchive());
		installer().install(model);
		Path folder = dir.resolve("vfg/models/parakeet-tdt-110m-en-36000-int8");
		for (String f : Assets.PARAKEET_FILES) {
			assertEquals("content of " + f, Files.readString(folder.resolve(f)));
		}
		assertFalse(Files.exists(folder.resolve("test_wavs/0.wav")), "only the model's files");
		assertEquals(model.sha256(), Files.readString(folder.resolve(".ok")).trim());
	}

	@Test
	void pinnedTableAndPlatforms() {
		assertEquals("win-x64", Assets.platform("Windows 11", "amd64"));
		assertEquals("linux-x64", Assets.platform("Linux", "amd64"));
		assertEquals("osx-aarch64", Assets.platform("Mac OS X", "aarch64"));
		assertEquals("osx-x64", Assets.platform("Mac OS X", "x86_64"));
		assertEquals(null, Assets.platform("Linux", "aarch64"));
		assertEquals(null, Assets.platform("Windows 11", "aarch64"));
		assertEquals(null, Assets.platform("FreeBSD", "amd64"));
		assertEquals("silero_vad.onnx", Assets.vad().fileName());
		assertEquals(111266225L, Assets.model("moonshine-base-en").bytes());
		assertEquals(29858559L, Assets.model("moonshine-tiny-en").bytes());
		Assets.Asset parakeet = Assets.model("parakeet-110m-en");
		assertEquals(108035095L, parakeet.bytes());
		assertEquals("f628312e9fdf8686374cb01a69425c41732529d540860311f16f37cbc32cfe9b", parakeet.sha256());
		assertEquals("asr-models/sherpa-onnx-nemo-parakeet_tdt_transducer_110m-en-36000-int8.tar.bz2", parakeet.path());
		assertTrue(Assets.isParakeet(parakeet));
		assertFalse(Assets.isParakeet(Assets.model("moonshine-base-en")));
		assertEquals(Path.of("r/models/parakeet-tdt-110m-en-36000-int8"), Assets.folder(Path.of("r"), parakeet));
		assertEquals("v1.13.8/sherpa-onnx-native-lib-win-x64-1.13.8.jar", Assets.natives("win-x64").path());
		assertEquals(Path.of("r/models/moonshine-tiny-en-2026-02-27"),
			Assets.folder(Path.of("r"), Assets.model("moonshine-tiny-en")));
		assertEquals(Path.of("r/natives/sherpa-onnx-1.13.8-win-x64"), Assets.folder(Path.of("r"), Assets.natives("win-x64")));
	}
}
