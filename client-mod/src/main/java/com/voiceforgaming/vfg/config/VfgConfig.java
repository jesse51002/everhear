package com.voiceforgaming.vfg.config;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonElement;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.voiceforgaming.vfg.session.ServerAddressMatch;
import com.voiceforgaming.vfg.setup.Assets;
import com.voiceforgaming.vfg.turn.TurnDetectors;
import java.io.IOException;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.attribute.PosixFilePermissions;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * {@code <gameDir>/config/vfg-client.json}: loaded, validated and saved here.
 *
 * <p>The client token (§11) lives in this file and nowhere else on the player's PC; it
 * is never logged and {@link #toString()} leaves it out. For development the environment
 * variables {@code VFG_SERVER_ADDRESS}, {@code VFG_BRIDGE_URL} and {@code VFG_CLIENT_TOKEN}
 * override the file when set, and are never written back: the fields hold the file's
 * values, and the {@code effective…()} methods apply the overrides.
 *
 * <p>{@code turn_threshold} is written as {@code null} while it is not set, so the file
 * shows the key without fixing a number: each turn model then uses its own threshold, and
 * switching models never carries one model's over to the other.
 */
public final class VfgConfig {
	private static final Logger LOG = LoggerFactory.getLogger("vfg");
	private static final Gson GSON = new GsonBuilder().setPrettyPrinting().disableHtmlEscaping().serializeNulls()
		.create();

	public static final String FILE_NAME = "vfg-client.json";
	/** The default: Parakeet TDT 110M. A saved Moonshine value is kept, never migrated. */
	public static final String MODEL_PARAKEET = Assets.PARAKEET_110M;
	public static final String MODEL_BASE = Assets.MOONSHINE_BASE;
	public static final String MODEL_TINY = Assets.MOONSHINE_TINY;
	public static final List<String> MODELS = List.of(MODEL_PARAKEET, MODEL_BASE, MODEL_TINY);
	public static final String DEFAULT_MODEL = MODEL_PARAKEET;
	public static final List<String> CORNERS = List.of("top_left", "top_right", "bottom_left", "bottom_right");
	public static final int DEFAULT_BRIDGE_PORT = 8787;

	/** The silence that ends a line the turn model keeps waiting; it was 5 s before there was one. */
	public static final double DEFAULT_SILENCE_SECONDS = 3;
	/** What every file saved before the turn model says, unless the player changed it. */
	public static final double OLD_SILENCE_SECONDS = 5;
	public static final double DEFAULT_VAD_THRESHOLD = 0.5;
	public static final int DEFAULT_STT_THREADS = 2;
	/** The keyword spotter's threshold: sherpa-onnx's default, which the spec starts from. */
	public static final double DEFAULT_INCANTATION_THRESHOLD = 0.25;
	/** The keyword spotter's per-token boost. */
	public static final double DEFAULT_INCANTATION_BOOST = 1.5;
	/** How far back the keyword spotter's re-check reaches, in seconds; 0 turns re-checks off. */
	public static final double DEFAULT_INCANTATION_RECHECK_SECONDS = 3;
	/** The re-check's reach other than 0: 1–10 s. */
	public static final double MIN_INCANTATION_RECHECK_SECONDS = 1;
	public static final double MAX_INCANTATION_RECHECK_SECONDS = 10;

	public String serverAddress = "";
	public String bridgeUrl = "";
	public String clientToken = "";
	public String micDevice = "";
	public String model = DEFAULT_MODEL;
	public String iconCorner = "top_left";
	public boolean muted = false;
	public String turnModel = TurnDetectors.DEFAULT;
	/** {@code null}: the turn model's own threshold. */
	public Double turnThreshold = null;
	public double silenceSeconds = DEFAULT_SILENCE_SECONDS;
	public double vadThreshold = DEFAULT_VAD_THRESHOLD;
	public int sttThreads = DEFAULT_STT_THREADS;
	public boolean logText = false;
	/** Whether the keyword spotter listens for the player's incantations. */
	public boolean incantations = true;
	public double incantationThreshold = DEFAULT_INCANTATION_THRESHOLD;
	public double incantationBoost = DEFAULT_INCANTATION_BOOST;
	public double incantationRecheckSeconds = DEFAULT_INCANTATION_RECHECK_SECONDS;

	private transient Map<String, String> env = System.getenv();

	public VfgConfig copy() {
		VfgConfig c = new VfgConfig();
		c.serverAddress = serverAddress;
		c.bridgeUrl = bridgeUrl;
		c.clientToken = clientToken;
		c.micDevice = micDevice;
		c.model = model;
		c.iconCorner = iconCorner;
		c.muted = muted;
		c.turnModel = turnModel;
		c.turnThreshold = turnThreshold;
		c.silenceSeconds = silenceSeconds;
		c.vadThreshold = vadThreshold;
		c.sttThreads = sttThreads;
		c.logText = logText;
		c.incantations = incantations;
		c.incantationThreshold = incantationThreshold;
		c.incantationBoost = incantationBoost;
		c.incantationRecheckSeconds = incantationRecheckSeconds;
		c.env = env;
		return c;
	}

	/** Use these environment variables instead of the process's (tests). */
	public VfgConfig withEnv(Map<String, String> env) {
		this.env = env;
		return this;
	}

	// --- effective values: the file, with the development overrides on top ---

	private String override(String name, String fileValue) {
		String v = env == null ? null : env.get(name);
		return v != null && !v.isBlank() ? v.trim() : fileValue;
	}

	public String effectiveServerAddress() {
		return override("VFG_SERVER_ADDRESS", serverAddress);
	}

	public String effectiveBridgeUrl() {
		return override("VFG_BRIDGE_URL", bridgeUrl);
	}

	public String effectiveClientToken() {
		return override("VFG_CLIENT_TOKEN", clientToken);
	}

	/** Whether any development override is set. */
	public boolean hasEnvOverrides() {
		return !override("VFG_SERVER_ADDRESS", "").isEmpty() || !override("VFG_BRIDGE_URL", "").isEmpty()
			|| !override("VFG_CLIENT_TOKEN", "").isEmpty();
	}

	public boolean hasToken() {
		String t = effectiveClientToken();
		return t != null && !t.isBlank();
	}

	/** The full {@code …/v1/lines} URL, or {@code null} when there is no bridge to post to. */
	public String linesUrl() {
		return linesUrl(effectiveBridgeUrl(), effectiveServerAddress());
	}

	/** The full {@code …/v1/casts} URL, the incantation invoke, or {@code null}. */
	public String castsUrl() {
		return endpoint(effectiveBridgeUrl(), effectiveServerAddress(), "/v1/casts");
	}

	/** The full {@code …/v1/incantations} URL, the player's list, or {@code null}. */
	public String incantationsUrl() {
		return endpoint(effectiveBridgeUrl(), effectiveServerAddress(), "/v1/incantations");
	}

	/** An empty bridge URL means {@code http://<host of server address>:8787}. */
	public static String linesUrl(String bridgeUrl, String serverAddress) {
		return endpoint(bridgeUrl, serverAddress, "/v1/lines");
	}

	/** {@code path} on the bridge; an empty bridge URL means {@code http://<host of server address>:8787}. */
	public static String endpoint(String bridgeUrl, String serverAddress, String path) {
		String base = bridgeUrl == null ? "" : bridgeUrl.trim();
		if (base.isEmpty()) {
			String host = ServerAddressMatch.host(serverAddress);
			if (host == null) {
				return null;
			}
			base = "http://" + host + ":" + DEFAULT_BRIDGE_PORT;
		}
		while (base.endsWith("/")) {
			base = base.substring(0, base.length() - 1);
		}
		return base + path;
	}

	/** Whether a non-empty bridge URL is http or https with a host. An empty one is valid. */
	public static boolean isValidBridgeUrl(String url) {
		if (url == null || url.isBlank()) {
			return true;
		}
		try {
			URI u = URI.create(url.trim());
			String scheme = u.getScheme() == null ? "" : u.getScheme().toLowerCase(Locale.ROOT);
			return (scheme.equals("http") || scheme.equals("https")) && u.getHost() != null && !u.getHost().isEmpty();
		} catch (IllegalArgumentException e) {
			return false;
		}
	}

	// --- load and save ---

	/**
	 * Loads the file; writes the defaults if it is missing; if it does not parse, renames
	 * it {@code vfg-client.json.bad}, uses the defaults and logs one line.
	 *
	 * <p>A file with no {@code turn_model} key was written before the turn model, when every
	 * save wrote {@code silence_seconds: 5}: its 5 becomes 3, and the file is written back
	 * once, with the new keys, so the move never happens again. Any other silence was the
	 * player's own and is kept.
	 */
	public static VfgConfig load(Path file) {
		if (!Files.exists(file)) {
			VfgConfig c = new VfgConfig();
			c.saveQuietly(file);
			return c;
		}
		try {
			String text = Files.readString(file, StandardCharsets.UTF_8);
			JsonElement root = JsonParser.parseString(text);
			if (!root.isJsonObject()) {
				throw new IllegalStateException("not a JSON object");
			}
			JsonObject o = root.getAsJsonObject();
			VfgConfig c = fromJson(o);
			if (!o.has("turn_model")) {
				if (c.silenceSeconds == OLD_SILENCE_SECONDS) {
					c.silenceSeconds = DEFAULT_SILENCE_SECONDS;
					LOG.info("config: silence_seconds 5 is now 3, the default with the turn model");
				}
				c.saveQuietly(file);
			}
			return c;
		} catch (IOException | RuntimeException e) {
			Path bad = file.resolveSibling(file.getFileName() + ".bad");
			LOG.warn("{} does not parse ({}); renamed it {} and using the defaults", file.getFileName(),
				e.getClass().getSimpleName(), bad.getFileName());
			try {
				Files.move(file, bad, StandardCopyOption.REPLACE_EXISTING);
			} catch (IOException moveError) {
				LOG.warn("could not rename {}: {}", file.getFileName(), moveError.getMessage());
			}
			VfgConfig c = new VfgConfig();
			c.saveQuietly(file);
			return c;
		}
	}

	static VfgConfig fromJson(JsonObject o) {
		VfgConfig c = new VfgConfig();
		c.serverAddress = string(o, "server_address", "");
		c.bridgeUrl = string(o, "bridge_url", "");
		c.clientToken = string(o, "client_token", "");
		c.micDevice = string(o, "mic_device", "");
		c.model = string(o, "model", DEFAULT_MODEL);
		if (c.model.equals("parakeet-110m")) {
			c.model = MODEL_PARAKEET; // the short name works too
		}
		if (!MODELS.contains(c.model)) {
			LOG.warn("config: model \"{}\" is not one of {}; using {}", c.model, MODELS, DEFAULT_MODEL);
			c.model = DEFAULT_MODEL;
		}
		c.iconCorner = string(o, "icon_corner", "top_left");
		if (!CORNERS.contains(c.iconCorner)) {
			LOG.warn("config: icon_corner \"{}\" is not one of {}; using top_left", c.iconCorner, CORNERS);
			c.iconCorner = "top_left";
		}
		c.muted = bool(o, "muted", false);
		c.turnModel = string(o, "turn_model", TurnDetectors.DEFAULT);
		if (!TurnDetectors.MODELS.contains(c.turnModel)) {
			LOG.warn("config: turn_model \"{}\" is not one of {}; using {}", c.turnModel, TurnDetectors.MODELS,
				TurnDetectors.DEFAULT);
			c.turnModel = TurnDetectors.DEFAULT;
		}
		c.turnThreshold = optionalNumber(o, "turn_threshold", 0.05, 0.95);
		c.silenceSeconds = number(o, "silence_seconds", DEFAULT_SILENCE_SECONDS, 2, 30);
		c.vadThreshold = number(o, "vad_threshold", DEFAULT_VAD_THRESHOLD, 0.1, 0.9);
		double threads = number(o, "stt_threads", DEFAULT_STT_THREADS, 1, 8);
		if (threads != Math.rint(threads)) {
			LOG.warn("config: stt_threads {} is not a whole number; using {}", threads, DEFAULT_STT_THREADS);
			threads = DEFAULT_STT_THREADS;
		}
		c.sttThreads = (int) threads;
		c.logText = bool(o, "log_text", false);
		c.incantations = bool(o, "incantations", true);
		c.incantationThreshold = number(o, "incantation_threshold", DEFAULT_INCANTATION_THRESHOLD, 0.05, 0.95);
		c.incantationBoost = number(o, "incantation_boost", DEFAULT_INCANTATION_BOOST, 0.5, 5);
		c.incantationRecheckSeconds = number(o, "incantation_recheck_seconds", DEFAULT_INCANTATION_RECHECK_SECONDS, 0,
			MAX_INCANTATION_RECHECK_SECONDS);
		if (c.incantationRecheckSeconds > 0 && c.incantationRecheckSeconds < MIN_INCANTATION_RECHECK_SECONDS) {
			LOG.warn("config: incantation_recheck_seconds = {} is outside 0 or {}–{}; using {}",
				c.incantationRecheckSeconds, MIN_INCANTATION_RECHECK_SECONDS, MAX_INCANTATION_RECHECK_SECONDS,
				DEFAULT_INCANTATION_RECHECK_SECONDS);
			c.incantationRecheckSeconds = DEFAULT_INCANTATION_RECHECK_SECONDS;
		}
		return c;
	}

	private static String string(JsonObject o, String key, String fallback) {
		JsonElement e = o.get(key);
		if (e == null || e.isJsonNull()) {
			return fallback;
		}
		if (!e.isJsonPrimitive() || !e.getAsJsonPrimitive().isString()) {
			LOG.warn("config: {} is not a string; using the default", key);
			return fallback;
		}
		return e.getAsString();
	}

	private static boolean bool(JsonObject o, String key, boolean fallback) {
		JsonElement e = o.get(key);
		if (e == null || e.isJsonNull()) {
			return fallback;
		}
		if (!e.isJsonPrimitive() || !e.getAsJsonPrimitive().isBoolean()) {
			LOG.warn("config: {} is not true or false; using {}", key, fallback);
			return fallback;
		}
		return e.getAsBoolean();
	}

	private static double number(JsonObject o, String key, double fallback, double min, double max) {
		JsonElement e = o.get(key);
		if (e == null || e.isJsonNull()) {
			return fallback;
		}
		if (!e.isJsonPrimitive() || !e.getAsJsonPrimitive().isNumber()) {
			LOG.warn("config: {} is not a number; using {}", key, fallback);
			return fallback;
		}
		double v = e.getAsDouble();
		if (!(v >= min && v <= max)) {
			LOG.warn("config: {} = {} is outside {}–{}; using {}", key, v, min, max, fallback);
			return fallback;
		}
		return v;
	}

	/** A number in range, or {@code null} when the key is missing, {@code null}, or anything else. */
	private static Double optionalNumber(JsonObject o, String key, double min, double max) {
		JsonElement e = o.get(key);
		if (e == null || e.isJsonNull()) {
			return null;
		}
		if (!e.isJsonPrimitive() || !e.getAsJsonPrimitive().isNumber()) {
			LOG.warn("config: {} is not a number; using the model's own", key);
			return null;
		}
		double v = e.getAsDouble();
		if (!(v >= min && v <= max)) {
			LOG.warn("config: {} = {} is outside {}–{}; using the model's own", key, v, min, max);
			return null;
		}
		return v;
	}

	public JsonObject toJson() {
		JsonObject o = new JsonObject();
		o.addProperty("server_address", serverAddress);
		o.addProperty("bridge_url", bridgeUrl);
		o.addProperty("client_token", clientToken);
		o.addProperty("mic_device", micDevice);
		o.addProperty("model", model);
		o.addProperty("icon_corner", iconCorner);
		o.addProperty("muted", muted);
		o.addProperty("turn_model", turnModel);
		if (turnThreshold == null) {
			o.add("turn_threshold", JsonNull.INSTANCE);
		} else {
			o.addProperty("turn_threshold", turnThreshold);
		}
		wholeOrNot(o, "silence_seconds", silenceSeconds);
		o.addProperty("vad_threshold", vadThreshold);
		o.addProperty("stt_threads", sttThreads);
		o.addProperty("log_text", logText);
		o.addProperty("incantations", incantations);
		o.addProperty("incantation_threshold", incantationThreshold);
		o.addProperty("incantation_boost", incantationBoost);
		wholeOrNot(o, "incantation_recheck_seconds", incantationRecheckSeconds);
		return o;
	}

	/** A number written as a whole number when it is one: {@code 3}, not {@code 3.0}. */
	private static void wholeOrNot(JsonObject o, String key, double v) {
		if (v == Math.rint(v)) {
			o.addProperty(key, (long) v);
		} else {
			o.addProperty(key, v);
		}
	}

	/** Writes the file values (never the environment overrides), through a temp file. */
	public void save(Path file) throws IOException {
		Files.createDirectories(file.getParent());
		Path tmp = file.resolveSibling(file.getFileName() + ".tmp");
		Files.writeString(tmp, GSON.toJson(toJson()) + "\n", StandardCharsets.UTF_8);
		try {
			// The token is in here: readable by the player only, where the file system allows it.
			Files.setPosixFilePermissions(tmp, PosixFilePermissions.fromString("rw-------"));
		} catch (UnsupportedOperationException | IOException ignored) {
			// Windows: the file inherits the user's profile permissions
		}
		Files.move(tmp, file, StandardCopyOption.REPLACE_EXISTING);
	}

	public void saveQuietly(Path file) {
		try {
			save(file);
		} catch (IOException e) {
			LOG.warn("could not write {}: {}", file.getFileName(), e.getMessage());
		}
	}

	@Override
	public String toString() {
		return "VfgConfig[server_address=" + serverAddress + ", bridge_url=" + bridgeUrl + ", client_token="
			+ (clientToken.isEmpty() ? "(empty)" : "(set)") + ", mic_device=" + micDevice + ", model=" + model
			+ ", icon_corner=" + iconCorner + ", muted=" + muted + ", turn_model=" + turnModel + ", turn_threshold="
			+ (turnThreshold == null ? "(the model's own)" : turnThreshold) + ", silence_seconds=" + silenceSeconds
			+ ", vad_threshold=" + vadThreshold + ", stt_threads=" + sttThreads + ", log_text=" + logText
			+ ", incantations=" + incantations + ", incantation_threshold=" + incantationThreshold
			+ ", incantation_boost=" + incantationBoost + ", incantation_recheck_seconds=" + incantationRecheckSeconds
			+ "]";
	}
}
