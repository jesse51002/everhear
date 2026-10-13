package com.voiceforgaming.vfg.setup;

import java.io.BufferedInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.DirectoryStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Duration;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;
import org.apache.commons.compress.archivers.tar.TarArchiveEntry;
import org.apache.commons.compress.archivers.tar.TarArchiveInputStream;
import org.apache.commons.compress.compressors.bzip2.BZip2CompressorInputStream;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Downloads, verifies and unpacks the pinned natives, VAD, model, turn model, keyword model, and
 * Vosk's model and natives into {@code <gameDir>/vfg/}.
 *
 * <p>For each one: if its folder's {@code .ok} holds the source's SHA-256 it is skipped;
 * else a file of that name in {@code downloads/} with the right hash is used (a player
 * behind a firewall can put the files there by hand); else it is downloaded there through
 * {@code <file>.part}, hashed as it streams, three tries waiting 2 then 4 s. A {@code .part}
 * left by a try that broke off, or by an earlier start, is carried on from where it stopped
 * (an HTTP range request; a server that answers with the whole file starts it again, and one that
 * answers the range unusably, a 206 for another range or a 4xx, has the part deleted, so the next
 * try starts clean; a part already the whole size is hashed and used if right), so a
 * dropped connection late in the 482 MB speech model costs only what was not yet fetched; a
 * finished file with the wrong hash is deleted. Then it is unpacked, {@code .ok} is written
 * last, and the download is deleted.
 */
public final class AssetInstaller {
	private static final Logger LOG = LoggerFactory.getLogger("vfg");
	public static final int TRIES = 3;
	public static final long[] WAITS_MILLIS = {2000, 4000, 8000};
	public static final Duration CONNECT_TIMEOUT = Duration.ofSeconds(15);
	private static final Duration HEADERS_TIMEOUT = Duration.ofSeconds(30);
	private static final long STALL_MILLIS = 30_000;
	public static final String OK_FILE = ".ok";

	/** Overall progress across {@link #installAll}: bytes done of bytes in total. */
	@FunctionalInterface
	public interface Progress {
		void update(long doneBytes, long totalBytes);
	}

	@FunctionalInterface
	public interface Sleeper {
		void sleep(long millis) throws InterruptedException;
	}

	/** Setup could not finish; the message is shown to the player after "setup failed: ". */
	public static final class SetupException extends Exception {
		public SetupException(String message) {
			super(message);
		}
	}

	private final String baseUrl;
	private final Path root;
	private final Sleeper sleeper;
	private final Progress progress;
	private final HttpClient http;
	private List<String> extraModelFiles = List.of();
	private long progressBase;
	private long progressTotal;

	public AssetInstaller(String baseUrl, Path vfgRoot, Sleeper sleeper, Progress progress) {
		this.baseUrl = baseUrl.endsWith("/") ? baseUrl : baseUrl + "/";
		this.root = vfgRoot;
		this.sleeper = sleeper;
		this.progress = progress == null ? (d, t) -> {} : progress;
		this.http = HttpClient.newBuilder()
			.connectTimeout(CONNECT_TIMEOUT)
			.followRedirects(HttpClient.Redirect.NORMAL)
			.build();
	}

	public AssetInstaller(Path vfgRoot, Progress progress) {
		this(Assets.BASE_URL, vfgRoot, Thread::sleep, progress);
	}

	/** Also unpack these files from a model archive when it has them (the tests add its test clips). */
	public AssetInstaller withExtraModelFiles(List<String> files) {
		this.extraModelFiles = List.copyOf(files);
		return this;
	}

	public Path root() {
		return root;
	}

	/** Installs each asset in order, publishing overall progress. */
	public void installAll(List<Assets.Asset> assets) throws SetupException, InterruptedException {
		progressTotal = assets.stream().mapToLong(Assets.Asset::bytes).sum();
		progressBase = 0;
		progress.update(0, progressTotal);
		for (Assets.Asset a : assets) {
			install(a);
			progressBase += a.bytes();
			progress.update(progressBase, progressTotal);
		}
	}

	/** Whether {@code folder}'s {@code .ok} holds {@code sha256}. */
	public static boolean isInstalled(Path folder, String sha256) {
		try {
			Path ok = folder.resolve(OK_FILE);
			return Files.isRegularFile(ok) && Files.readString(ok, StandardCharsets.UTF_8).trim().equals(sha256);
		} catch (IOException e) {
			return false;
		}
	}

	/** Installs one asset into its folder and returns the folder. */
	public Path install(Assets.Asset a) throws SetupException, InterruptedException {
		Path folder = Assets.folder(root, a);
		if (isInstalled(folder, a.sha256())) {
			return folder;
		}
		Path file = fetch(a);
		try {
			prepareFolder(folder);
			switch (a.kind()) {
				case NATIVES -> unpackNatives(file, folder, a.variant());
				case MODEL, KWS -> unpackModel(file, folder, a);
				case VOSK -> unpackZipModel(file, folder, a);
				case VOSK_NATIVES -> unpackVoskNatives(file, folder, a);
				case VAD -> move(file, folder.resolve(Assets.VAD_FILE));
				case TURN -> move(file, folder.resolve(a.fileName()));
			}
			Files.writeString(folder.resolve(OK_FILE), a.sha256() + "\n", StandardCharsets.UTF_8);
			Files.deleteIfExists(file);
		} catch (IOException e) {
			throw new SetupException(a.fileName() + ": " + e.getMessage());
		}
		return folder;
	}

	/**
	 * Makes sure {@code downloads/<file name>} holds the asset with the right hash: one put
	 * there by hand is used as it is, otherwise it is downloaded. Returns its path.
	 */
	public Path fetch(Assets.Asset a) throws SetupException, InterruptedException {
		Path downloads = Assets.downloads(root);
		Path file = downloads.resolve(a.fileName());
		try {
			Files.createDirectories(downloads);
			if (Files.isRegularFile(file) && sha256(file).equals(a.sha256())) {
				LOG.info("using {} from {}", a.fileName(), downloads);
				return file;
			}
		} catch (IOException e) {
			throw new SetupException(a.fileName() + ": " + e.getMessage());
		}
		download(a, file);
		return file;
	}

	private void download(Assets.Asset a, Path file) throws SetupException, InterruptedException {
		String url = a.url(baseUrl);
		Exception last = null;
		for (int attempt = 1; attempt <= TRIES; attempt++) {
			try {
				LOG.info("downloading {} (try {} of {})", url, attempt, TRIES);
				downloadOnce(url, a, file);
				return;
			} catch (IOException | SetupException e) {
				last = e;
				LOG.warn("download of {} failed: {}", a.fileName(), e.getMessage());
				if (attempt < TRIES) {
					sleeper.sleep(WAITS_MILLIS[attempt - 1]);
				}
			}
		}
		throw new SetupException("could not download " + a.fileName() + " (" + (last == null ? "?" : last.getMessage()) + ")");
	}

	private void downloadOnce(String url, Assets.Asset a, Path file) throws IOException, SetupException,
			InterruptedException {
		Path part = file.resolveSibling(file.getFileName() + ".part");
		long have = Files.isRegularFile(part) ? Files.size(part) : 0;
		if (have == a.bytes()) {
			// the whole of it: a download that finished but was stopped before its hash check and move
			if (sha256(part).equals(a.sha256())) {
				LOG.info("{}: the download was already whole", a.fileName());
				move(part, file);
				return;
			}
			Files.deleteIfExists(part);
			have = 0;
		} else if (have > a.bytes()) { // longer than the whole: no download of this file
			Files.deleteIfExists(part);
			have = 0;
		}
		MessageDigest md = newSha256();
		if (have > 0) {
			hashInto(md, part);
		}
		HttpRequest.Builder request = HttpRequest.newBuilder(URI.create(url)).timeout(HEADERS_TIMEOUT).GET();
		if (have > 0) {
			request.header("Range", "bytes=" + have + "-");
		}
		HttpResponse<InputStream> response = http.send(request.build(), HttpResponse.BodyHandlers.ofInputStream());
		long done;
		try (InputStream in = response.body()) {
			int status = response.statusCode();
			boolean resumed = have > 0 && status == 206 && rangeStartsAt(response, have);
			if (!resumed && status != 200) {
				if (have > 0 && status >= 200 && status < 500) {
					// An answer to the range that cannot carry the part on (a 206 for another range, a 416):
					// the next try starts clean instead of asking the same again.
					Files.deleteIfExists(part);
				}
				throw new IOException("HTTP " + status);
			}
			if (!resumed && have > 0) {
				LOG.info("{}: the server sent the whole file; starting it again", a.fileName());
				md = newSha256();
				have = 0;
			} else if (resumed) {
				LOG.info("{}: carrying on from {} MB", a.fileName(), have / 1_000_000);
			}
			done = have;
			StallWatch watch = new StallWatch(in);
			try (OutputStream out = resumed ? Files.newOutputStream(part, StandardOpenOption.APPEND)
				: Files.newOutputStream(part)) {
				byte[] buf = new byte[1 << 16];
				long lastReport = done;
				int n;
				while ((n = in.read(buf)) > 0) {
					watch.tick();
					md.update(buf, 0, n);
					out.write(buf, 0, n);
					done += n;
					if (done - lastReport >= (1 << 18)) {
						lastReport = done;
						progress.update(progressBase + Math.min(done, a.bytes()), Math.max(progressTotal, a.bytes()));
					}
					if (Thread.currentThread().isInterrupted()) { // the part stays, for the next setup to carry on
						throw new InterruptedException("download of " + a.fileName() + " stopped");
					}
				}
			} finally {
				watch.stop();
			}
			if (watch.stalled()) {
				throw new IOException("download stalled");
			}
		}
		String actual = HexFormat.of().formatHex(md.digest());
		if (!actual.equals(a.sha256())) {
			Files.deleteIfExists(part);
			throw new SetupException("wrong SHA-256 for " + a.fileName());
		}
		move(part, file);
	}

	/** Whether a {@code 206}'s {@code Content-Range} starts at {@code from}: {@code bytes <from>-<to>/<size>}. */
	static boolean rangeStartsAt(HttpResponse<?> response, long from) {
		String range = response.headers().firstValue("Content-Range").orElse("").trim();
		return range.startsWith("bytes " + from + "-");
	}

	private static void hashInto(MessageDigest md, Path file) throws IOException {
		try (InputStream in = Files.newInputStream(file)) {
			byte[] buf = new byte[1 << 16];
			int n;
			while ((n = in.read(buf)) > 0) {
				md.update(buf, 0, n);
			}
		}
	}

	private static void prepareFolder(Path folder) throws IOException {
		if (Files.isDirectory(folder)) {
			deleteContents(folder);
		}
		Files.createDirectories(folder);
	}

	private static void deleteContents(Path dir) throws IOException {
		try (DirectoryStream<Path> entries = Files.newDirectoryStream(dir)) {
			for (Path p : entries) {
				if (Files.isDirectory(p) && !Files.isSymbolicLink(p)) {
					deleteContents(p);
				}
				Files.delete(p);
			}
		}
	}

	/** From a natives jar, only {@code sherpa-onnx/native/<os-arch>/*}, flat. */
	private static void unpackNatives(Path jar, Path folder, String platform) throws IOException, SetupException {
		String prefix = "sherpa-onnx/native/" + platform + "/";
		int count = 0;
		try (ZipInputStream zip = new ZipInputStream(new BufferedInputStream(Files.newInputStream(jar)))) {
			ZipEntry e;
			while ((e = zip.getNextEntry()) != null) {
				String name = e.getName();
				if (e.isDirectory() || !name.startsWith(prefix)) {
					continue;
				}
				String file = name.substring(prefix.length());
				if (file.isEmpty() || file.contains("/") || file.contains("\\") || file.equals("..")) {
					continue;
				}
				Files.copy(zip, folder.resolve(file), StandardCopyOption.REPLACE_EXISTING);
				count++;
			}
		}
		if (count == 0) {
			throw new SetupException("the natives jar has nothing for " + platform);
		}
	}

	/** From a model archive, only its wanted files (paths below the archive's top folder). */
	private void unpackModel(Path archive, Path folder, Assets.Asset a) throws IOException, SetupException {
		Set<String> missing = new LinkedHashSet<>(Assets.modelFiles(a));
		Set<String> wanted = new LinkedHashSet<>(missing);
		wanted.addAll(extraModelFiles);
		try (TarArchiveInputStream tar = new TarArchiveInputStream(new BZip2CompressorInputStream(
				new BufferedInputStream(Files.newInputStream(archive), 1 << 16)))) {
			TarArchiveEntry e;
			while ((e = tar.getNextEntry()) != null) {
				if (!e.isFile()) {
					continue;
				}
				String name = e.getName();
				while (name.startsWith("./")) { // some archives were made with "tar ... ."
					name = name.substring(2);
				}
				int slash = name.indexOf('/');
				String rel = slash >= 0 ? name.substring(slash + 1) : name;
				if (!wanted.contains(rel)) {
					continue;
				}
				Path target = folder.resolve(rel).normalize();
				if (!target.startsWith(folder)) {
					continue;
				}
				Files.createDirectories(target.getParent());
				Files.copy(tar, target, StandardCopyOption.REPLACE_EXISTING);
				missing.remove(rel);
			}
		}
		if (!missing.isEmpty()) {
			throw new SetupException("the model archive is missing " + String.join(", ", new ArrayList<>(missing)));
		}
	}

	/** From a Vosk natives zip (or jar), only this platform's library and what it loads, flat. */
	private static void unpackVoskNatives(Path zipFile, Path folder, Assets.Asset a) throws IOException, SetupException {
		Set<String> missing = new LinkedHashSet<>(Assets.voskNativeFiles(a));
		try (ZipInputStream zip = new ZipInputStream(new BufferedInputStream(Files.newInputStream(zipFile), 1 << 16))) {
			ZipEntry e;
			while ((e = zip.getNextEntry()) != null) {
				if (e.isDirectory() || !missing.remove(e.getName())) {
					continue;
				}
				String name = e.getName().substring(e.getName().lastIndexOf('/') + 1);
				Files.copy(zip, folder.resolve(name), StandardCopyOption.REPLACE_EXISTING);
			}
		}
		if (!missing.isEmpty()) {
			throw new SetupException("the Vosk natives archive is missing " + String.join(", ", missing));
		}
	}

	/** From a model zip, only its wanted files (paths below the zip's top folder). */
	private static void unpackZipModel(Path zipFile, Path folder, Assets.Asset a) throws IOException, SetupException {
		Set<String> missing = new LinkedHashSet<>(Assets.modelFiles(a));
		Set<String> wanted = new LinkedHashSet<>(missing);
		try (ZipInputStream zip = new ZipInputStream(new BufferedInputStream(Files.newInputStream(zipFile), 1 << 16))) {
			ZipEntry e;
			while ((e = zip.getNextEntry()) != null) {
				if (e.isDirectory()) {
					continue;
				}
				String name = e.getName();
				int slash = name.indexOf('/');
				String rel = slash >= 0 ? name.substring(slash + 1) : name;
				if (!wanted.contains(rel)) {
					continue;
				}
				Path target = folder.resolve(rel).normalize();
				if (!target.startsWith(folder)) {
					continue;
				}
				Files.createDirectories(target.getParent());
				Files.copy(zip, target, StandardCopyOption.REPLACE_EXISTING);
				missing.remove(rel);
			}
		}
		if (!missing.isEmpty()) {
			throw new SetupException("the model zip is missing " + String.join(", ", new ArrayList<>(missing)));
		}
	}

	private static void move(Path from, Path to) throws IOException {
		try {
			Files.move(from, to, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
		} catch (AtomicMoveNotSupportedException e) {
			Files.move(from, to, StandardCopyOption.REPLACE_EXISTING);
		}
	}

	public static String sha256(Path file) throws IOException {
		MessageDigest md = newSha256();
		try (InputStream in = Files.newInputStream(file)) {
			byte[] buf = new byte[1 << 16];
			int n;
			while ((n = in.read(buf)) > 0) {
				md.update(buf, 0, n);
			}
		}
		return HexFormat.of().formatHex(md.digest());
	}

	private static MessageDigest newSha256() {
		try {
			return MessageDigest.getInstance("SHA-256");
		} catch (NoSuchAlgorithmException e) {
			throw new IllegalStateException(e);
		}
	}

	/** Closes a download's stream when no bytes have arrived for 30 s, so a stalled download fails and is retried. */
	private static final class StallWatch {
		private final InputStream in;
		private final Thread thread;
		private volatile long lastByte = System.currentTimeMillis();
		private volatile boolean done;
		private volatile boolean stalled;

		StallWatch(InputStream in) {
			this.in = in;
			this.thread = new Thread(this::watch, "vfg-setup-watch");
			this.thread.setDaemon(true);
			this.thread.start();
		}

		void tick() {
			lastByte = System.currentTimeMillis();
		}

		boolean stalled() {
			return stalled;
		}

		void stop() {
			done = true;
			thread.interrupt();
		}

		private void watch() {
			while (!done) {
				try {
					Thread.sleep(1000);
				} catch (InterruptedException e) {
					return;
				}
				if (!done && System.currentTimeMillis() - lastByte > STALL_MILLIS) {
					stalled = true;
					try {
						in.close();
					} catch (IOException ignored) {
						// the reader sees the closed stream
					}
					return;
				}
			}
		}
	}
}
