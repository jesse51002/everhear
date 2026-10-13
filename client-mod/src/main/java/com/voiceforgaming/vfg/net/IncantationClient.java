package com.voiceforgaming.vfg.net;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.voiceforgaming.vfg.kws.Incantation;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;
import java.util.function.Supplier;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * The {@code vfg-incantations} thread: the player's incantations from the bridge, by long poll
 * ({@code GET /v1/incantations?player=…&after=…&wait=25}), while the mod listens on the server it
 * is set up for. It asks once at {@link #start}, with no {@code after}, and the bridge answers at
 * once; then again after each answer, with the version it has, and the bridge holds the request
 * until the list changes or 25 s pass, answering {@code 304} for no change.
 *
 * <p>The answer also carries {@code server_phrases} (since 0.3.5's bridge): every incantation phrase
 * on the server, whoever's, deduplicated, phrases only. The mod's hotwords hold them; a bridge
 * that does not send the field gives the listener {@code null}, and the player's own phrases stand in.
 *
 * <ul>
 * <li>{@code 200}: the list, the server's phrases and the version, to the listener.
 * <li>{@code 304}: unchanged; asked again at once.
 * <li>No answer, {@code 5xx} or anything else: asked again after 1, 2, 5, 10, 30, 30… s.
 * <li>{@code 401}: the token is rejected; asked again after 60 s.
 * </ul>
 */
public final class IncantationClient {
	private static final Logger LOG = LoggerFactory.getLogger("vfg");
	/** How long the bridge holds a request with no change (it holds at most 30 s). */
	public static final int WAIT_SECONDS = 25;
	/** A request's timeout, beyond the wait. */
	public static final Duration TIMEOUT_BEYOND_WAIT = Duration.ofSeconds(10);
	private static final long[] BACKOFF_MILLIS = {1000, 2000, 5000, 10000, 30000};
	private static final long REJECTED_MILLIS = 60_000;

	/** Where the list is: the full {@code …/v1/incantations} URL and the client token. */
	public record Target(String incantationsUrl, String token) {
		@Override
		public String toString() {
			return "Target[" + incantationsUrl + "]"; // never the token
		}
	}

	@FunctionalInterface
	public interface Sleeper {
		void sleep(long millis) throws InterruptedException;
	}

	/** Called on {@code vfg-incantations}. */
	public interface Listener {
		/**
		 * A new list for the player, and its version; {@code serverPhrases} is every incantation phrase
		 * on the server, or {@code null} from a bridge that does not send them.
		 */
		void updated(long version, List<Incantation> incantations, List<String> serverPhrases);

		default void tokenRejected() {}
	}

	/** What one request came to. */
	public enum Poll { UPDATED, UNCHANGED, REJECTED, FAILED }

	private final Supplier<Target> target;
	private final int waitSeconds;
	private final Sleeper sleeper;
	private final Listener listener;
	private final HttpClient http;
	private volatile long version = -1;
	private Thread thread;
	private int session;

	/** {@code waitSeconds}: how long the bridge may hold a request; 25 in the mod. */
	public IncantationClient(Supplier<Target> target, int waitSeconds, Sleeper sleeper, Listener listener) {
		this.target = target;
		this.waitSeconds = waitSeconds;
		this.sleeper = sleeper;
		this.listener = listener;
		this.http = HttpClient.newBuilder()
			.connectTimeout(BridgeClient.CONNECT_TIMEOUT)
			.version(HttpClient.Version.HTTP_1_1)
			.followRedirects(HttpClient.Redirect.NEVER)
			.build();
	}

	public IncantationClient(Supplier<Target> target, Listener listener) {
		this(target, WAIT_SECONDS, Thread::sleep, listener);
	}

	/** The version of the last list, or -1 before the first. */
	public long version() {
		return version;
	}

	/** Starts asking for {@code player}'s list afresh, stopping any earlier polling. */
	public synchronized void start(String player) {
		stop();
		version = -1;
		int mine = ++session;
		thread = new Thread(() -> run(player, mine), "vfg-incantations");
		thread.setDaemon(true);
		thread.start();
	}

	/** Stops polling; a request in flight is abandoned. */
	public synchronized void stop() {
		session++;
		if (thread != null) {
			thread.interrupt();
			thread = null;
		}
	}

	private synchronized boolean current(int mine) {
		return session == mine;
	}

	private void run(String player, int mine) {
		int failures = 0;
		try {
			while (current(mine)) {
				switch (poll(player, mine)) {
					case UPDATED, UNCHANGED -> failures = 0;
					case REJECTED -> sleeper.sleep(REJECTED_MILLIS);
					case FAILED -> sleeper.sleep(BACKOFF_MILLIS[Math.min(failures++, BACKOFF_MILLIS.length - 1)]);
				}
			}
		} catch (InterruptedException e) {
			// stopped
		}
	}

	/** One request for {@code player}'s list, with {@code after} once there is a version; on the calling thread. */
	public Poll poll(String player) throws InterruptedException {
		int mine;
		synchronized (this) {
			mine = session;
		}
		return poll(player, mine);
	}

	private Poll poll(String player, int mine) throws InterruptedException {
		Target t = target.get();
		if (t == null || t.incantationsUrl() == null || t.incantationsUrl().isBlank()) {
			return Poll.FAILED;
		}
		long after = version;
		String url = t.incantationsUrl() + "?player=" + URLEncoder.encode(player, StandardCharsets.UTF_8)
			+ (after >= 0 ? "&after=" + after : "") + "&wait=" + waitSeconds;
		HttpResponse<String> response;
		CompletableFuture<HttpResponse<String>> pending = null;
		try {
			HttpRequest request = HttpRequest.newBuilder(URI.create(url))
				.timeout(Duration.ofSeconds(waitSeconds).plus(TIMEOUT_BEYOND_WAIT))
				.header("Authorization", "Bearer " + (t.token() == null ? "" : t.token()))
				.GET()
				.build();
			pending = http.sendAsync(request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
			response = pending.get();
		} catch (IllegalArgumentException e) {
			LOG.warn("incantations: bad bridge URL: {}", e.getMessage());
			return Poll.FAILED;
		} catch (ExecutionException e) {
			LOG.debug("incantations: bridge unreachable ({})", e.getCause() == null ? e : e.getCause().toString());
			return Poll.FAILED;
		} catch (InterruptedException e) {
			pending.cancel(true);
			throw e;
		}
		if (!current(mine)) {
			return Poll.UNCHANGED; // stopped meanwhile: the answer is for a session that is over
		}
		int status = response.statusCode();
		if (status == 304) {
			return Poll.UNCHANGED;
		}
		if (status == 401) {
			LOG.warn("incantations: the bridge rejected the client token (401)");
			listener.tokenRejected();
			return Poll.REJECTED;
		}
		if (status != 200) {
			LOG.debug("incantations: the bridge answered {}", status);
			return Poll.FAILED;
		}
		List<Incantation> list;
		List<String> serverPhrases;
		long next;
		try {
			JsonObject o = JsonParser.parseString(response.body()).getAsJsonObject();
			next = o.get("version").getAsLong();
			list = parse(o.getAsJsonArray("incantations"));
			serverPhrases = phrases(o.get("server_phrases"));
		} catch (RuntimeException e) {
			LOG.warn("incantations: the bridge's answer does not parse ({})", e.toString());
			return Poll.FAILED;
		}
		version = next;
		listener.updated(next, list, serverPhrases);
		if (next == after) {
			sleeper.sleep(BACKOFF_MILLIS[0]); // the bridge did not hold an unchanged list; do not spin
		}
		return Poll.UPDATED;
	}

	/**
	 * {@code server_phrases}: its strings, trimmed, the blank and repeated ones left out; {@code null}
	 * when the field is missing or not a list (a bridge before 0.3.5).
	 */
	static List<String> phrases(JsonElement field) {
		if (field == null || !field.isJsonArray()) {
			return null;
		}
		List<String> out = new ArrayList<>();
		for (JsonElement e : field.getAsJsonArray()) {
			if (e.isJsonPrimitive() && e.getAsJsonPrimitive().isString()) {
				String p = e.getAsString().strip();
				if (!p.isEmpty() && !out.contains(p)) {
					out.add(p);
				}
			}
		}
		return out;
	}

	static List<Incantation> parse(JsonArray array) {
		List<Incantation> out = new ArrayList<>();
		if (array == null) {
			return out;
		}
		for (JsonElement e : array) {
			if (!e.isJsonObject() || !e.getAsJsonObject().has("phrase")) {
				continue;
			}
			JsonObject o = e.getAsJsonObject();
			List<String> aliases = new ArrayList<>();
			if (o.has("aliases") && o.get("aliases").isJsonArray()) {
				for (JsonElement a : o.getAsJsonArray("aliases")) {
					if (a.isJsonPrimitive()) {
						aliases.add(a.getAsString());
					}
				}
			}
			double cooldown = o.has("cooldown_s") && o.get("cooldown_s").isJsonPrimitive()
				? o.get("cooldown_s").getAsDouble() : 0;
			out.add(new Incantation(o.get("phrase").getAsString(), aliases, cooldown));
		}
		return out;
	}
}
