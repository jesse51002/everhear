package com.voiceforgaming.vfg.net;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.function.LongSupplier;
import java.util.function.Supplier;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Posts each cast to the bridge's incantation invoke ({@code POST /v1/casts}), each on a
 * {@code vfg-cast} thread of its own, so neither pipeline ever waits on it.
 *
 * <ul>
 * <li>{@code 202}: answered; the body says {@code fired}, {@code cooldown}, {@code duplicate} or
 *     {@code failed}.
 * <li>{@code 404}: the player has no such incantation; {@code 503}: the kit is not on the box;
 *     {@code 400}: a bad body; {@code 401}: the token is rejected. None is tried again.
 * <li>No answer, {@code 408}, {@code 429} or another {@code 5xx}: tried again after 0.1, 0.2, 0.4, 0.8 s
 *     with the same {@code client_cast_id}, while the try would start within 2 s of the hit; then
 *     dropped. A late spell is worse than none.
 * </ul>
 *
 * One line is logged per cast, with its phrase and what became of it; never audio, and never
 * the token.
 */
public final class CastClient {
	private static final Logger LOG = LoggerFactory.getLogger("vfg");
	/** A cast not answered within this of its hit is dropped. */
	public static final long WINDOW_MILLIS = 2000;
	private static final long[] BACKOFF_MILLIS = {100, 200, 400, 800};
	private static final long MIN_TIMEOUT_MILLIS = 100;
	private static final long MAX_TIMEOUT_MILLIS = 1000;

	/** Where casts go: the full {@code …/v1/casts} URL and the client token. */
	public record Target(String castsUrl, String token) {
		@Override
		public String toString() {
			return "Target[" + castsUrl + "]"; // never the token
		}
	}

	@FunctionalInterface
	public interface Sleeper {
		void sleep(long millis) throws InterruptedException;
	}

	/** What happened to a cast, called on its {@code vfg-cast} thread. */
	public interface Listener {
		/** Every POST: its status code, or 0 with the error when there was no answer, and the body. */
		default void attempted(CastPayload cast, int attempt, int status, String body) {}

		/** The bridge's last answer: {@code 202} with the cast's status, or another final code without one. */
		default void answered(CastPayload cast, int status, String castStatus, long answeredAtMillis) {}

		default void dropped(CastPayload cast, String reason) {}

		default void tokenRejected() {}
	}

	/** What {@link #deliver} ended with: the HTTP status (0 when dropped) and the cast's status, if any. */
	public record Result(int status, String castStatus, String reason) {}

	private final Supplier<Target> target;
	private final LongSupplier clock;
	private final Sleeper sleeper;
	private final Listener listener;
	private final HttpClient http;
	private final ExecutorService threads;

	public CastClient(Supplier<Target> target, LongSupplier clock, Sleeper sleeper, Listener listener) {
		this.target = target;
		this.clock = clock;
		this.sleeper = sleeper;
		this.listener = listener;
		this.http = HttpClient.newBuilder()
			.connectTimeout(Duration.ofMillis(MAX_TIMEOUT_MILLIS))
			.version(HttpClient.Version.HTTP_1_1)
			.followRedirects(HttpClient.Redirect.NEVER)
			.build();
		this.threads = Executors.newCachedThreadPool(r -> {
			Thread t = new Thread(r, "vfg-cast");
			t.setDaemon(true);
			return t;
		});
	}

	public CastClient(Supplier<Target> target, Listener listener) {
		this(target, System::currentTimeMillis, Thread::sleep, listener);
	}

	/** Posts {@code cast}, heard at {@code heardAtMillis}, on a thread of its own; returns at once. */
	public void post(CastPayload cast, long heardAtMillis) {
		post(cast, heardAtMillis, null);
	}

	/** As {@link #post(CastPayload, long)}, with which way the spotter heard it for the log line ("the re-check"). */
	public void post(CastPayload cast, long heardAtMillis, String by) {
		try {
			threads.execute(() -> {
				try {
					deliver(cast, heardAtMillis, by);
				} catch (InterruptedException e) {
					Thread.currentThread().interrupt();
				} catch (RuntimeException e) {
					LOG.warn("cast \"{}\" dropped: {}", cast.phrase(), e.toString());
				}
			});
		} catch (java.util.concurrent.RejectedExecutionException e) {
			LOG.debug("cast \"{}\" not sent: shutting down", cast.phrase());
		}
	}

	/** Stops taking casts; one in flight gets {@code graceMillis}. */
	public void shutdown(long graceMillis) {
		threads.shutdown();
		try {
			threads.awaitTermination(graceMillis, TimeUnit.MILLISECONDS);
		} catch (InterruptedException e) {
			Thread.currentThread().interrupt();
		}
		threads.shutdownNow();
	}

	/** Posts {@code cast} on this thread, tries included, and returns how it ended. */
	public Result deliver(CastPayload cast, long heardAtMillis) throws InterruptedException {
		return deliver(cast, heardAtMillis, null);
	}

	private Result deliver(CastPayload cast, long heardAtMillis, String by) throws InterruptedException {
		long deadline = heardAtMillis + WINDOW_MILLIS;
		for (int attempt = 1; ; attempt++) {
			long now = clock.getAsLong();
			if (now >= deadline) {
				return drop(cast, "not answered within 2 s of the hit");
			}
			Result r = attempt(cast, by, heardAtMillis, attempt, Math.max(MIN_TIMEOUT_MILLIS, Math.min(MAX_TIMEOUT_MILLIS,
				deadline - now)));
			if (r != null) {
				return r;
			}
			long wait = BACKOFF_MILLIS[Math.min(attempt - 1, BACKOFF_MILLIS.length - 1)];
			if (clock.getAsLong() + wait >= deadline) {
				return drop(cast, "not answered within 2 s of the hit");
			}
			sleeper.sleep(wait);
		}
	}

	/** One POST; {@code null} when it is worth trying again. */
	private Result attempt(CastPayload cast, String by, long heardAtMillis, int number, long timeoutMillis)
			throws InterruptedException {
		Target t = target.get();
		if (t == null || t.castsUrl() == null || t.castsUrl().isBlank()) {
			return drop(cast, "no bridge URL set");
		}
		HttpResponse<String> response;
		try {
			HttpRequest request = HttpRequest.newBuilder(URI.create(t.castsUrl()))
				.timeout(Duration.ofMillis(timeoutMillis))
				.header("Authorization", "Bearer " + (t.token() == null ? "" : t.token()))
				.header("Content-Type", "application/json")
				.POST(HttpRequest.BodyPublishers.ofString(cast.toJson(), StandardCharsets.UTF_8))
				.build();
			response = http.send(request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
		} catch (IOException e) {
			listener.attempted(cast, number, 0, e.getClass().getSimpleName());
			return null;
		} catch (IllegalArgumentException e) {
			return drop(cast, "bad bridge URL");
		}
		int status = response.statusCode();
		String body = response.body() == null ? "" : response.body();
		listener.attempted(cast, number, status, body);
		if (status == 408 || status == 429 || (status >= 500 && status <= 599 && status != 503)) {
			return null;
		}
		long now = clock.getAsLong();
		// after the phrase: from its ended_at, as the logs before the re-check; after the hit: the trip to the box
		String late = String.format(Locale.ROOT, "%.2f s after the phrase, %.2f s after the hit",
			(now - cast.endedAtMillis()) / 1000.0, (now - heardAtMillis) / 1000.0);
		switch (status) {
			case 202 -> {
				String castStatus = field(body, "status");
				String copy = field(body, "copy");
				String error = field(body, "error");
				LOG.info("cast \"{}\" (heard \"{}\"{}): {}{}{}, answered {}", cast.phrase(), cast.heard(),
					by == null ? "" : " by " + by, castStatus, copy == null ? "" : " as " + copy,
					error == null ? "" : ": " + error, late);
				listener.answered(cast, status, castStatus, now);
				return new Result(status, castStatus, null);
			}
			case 404 -> LOG.info("cast \"{}\" (heard \"{}\"): the bridge has no such incantation for this player",
				cast.phrase(), cast.heard());
			case 503 -> LOG.warn("cast \"{}\" dropped: the kit is not on the box (503)", cast.phrase());
			case 401 -> {
				LOG.warn("cast \"{}\" dropped: the bridge rejected the client token (401)", cast.phrase());
				listener.tokenRejected();
			}
			case 400 -> LOG.warn("cast \"{}\" rejected as a bad body (400): {}", cast.phrase(), abbreviate(body));
			default -> LOG.warn("cast \"{}\" dropped: unexpected status {}", cast.phrase(), status);
		}
		listener.answered(cast, status, null, now);
		return new Result(status, null, "status " + status);
	}

	private Result drop(CastPayload cast, String reason) {
		LOG.info("cast \"{}\" (heard \"{}\") dropped: {}", cast.phrase(), cast.heard(), reason);
		listener.dropped(cast, reason);
		return new Result(0, null, reason);
	}

	private static String field(String body, String key) {
		try {
			JsonElement e = JsonParser.parseString(body);
			if (e.isJsonObject()) {
				JsonObject o = e.getAsJsonObject();
				if (o.has(key) && o.get(key).isJsonPrimitive()) {
					return o.get(key).getAsString();
				}
			}
		} catch (RuntimeException ignored) {
			// not JSON
		}
		return null;
	}

	private static String abbreviate(String s) {
		s = s.replaceAll("\\s+", " ").trim();
		return s.length() > 300 ? s.substring(0, 300) + "…" : s;
	}
}
