package com.voiceforgaming.vfg.net;

import com.google.gson.JsonElement;
import com.google.gson.JsonParser;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayDeque;
import java.util.concurrent.CompletableFuture;
import java.util.function.LongSupplier;
import java.util.function.Supplier;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * The {@code vfg-send} thread: posts lines to the bridge one at a time, in order
 * (docs/CONTRACTS.md §2).
 *
 * <ul>
 * <li>{@code 202}: delivered.
 * <li>{@code 400}: dropped with a warning carrying the response body; not retried.
 * <li>{@code 401}: dropped; the token is marked rejected; later lines are still tried.
 * <li>Network error, timeout, {@code 408}, {@code 429} or {@code 5xx}: wait 1, 2, 4, 8, 16, 16… s
 *     and post again with the same {@code client_line_id}, while the next try would start
 *     within 60 s of the line closing; then dropped.
 * <li>Anything else: dropped.
 * </ul>
 *
 * The queue holds at most 200 waiting lines; beyond that the oldest goes. The token is
 * never logged, and a line's text only when {@code log_text} is on.
 */
public final class BridgeClient {
	private static final Logger LOG = LoggerFactory.getLogger("vfg");

	public static final long LINE_WINDOW_MILLIS = 60_000;
	public static final int MAX_QUEUE = 200;
	public static final Duration CONNECT_TIMEOUT = Duration.ofSeconds(3);
	public static final Duration REQUEST_TIMEOUT = Duration.ofSeconds(5);
	private static final long[] BACKOFF_MILLIS = {1000, 2000, 4000, 8000, 16000};

	/** Where lines go: the full {@code …/v1/lines} URL and the client token. */
	public record Target(String linesUrl, String token) {
		@Override
		public String toString() {
			return "Target[" + linesUrl + "]"; // never the token
		}
	}

	@FunctionalInterface
	public interface Sleeper {
		void sleep(long millis) throws InterruptedException;
	}

	/** Delivery news, called on {@code vfg-send}. */
	public interface Listener {
		default void delivered(String clientLineId, String id) {}

		default void tokenRejected() {}

		default void deliveryProblem(boolean failing) {}

		default void dropped(String clientLineId, String reason) {}

		/** Every POST: its status code, or 0 with the error when there was no answer. */
		default void attempted(String clientLineId, int attempt, int status, String error) {}
	}

	/** The result of the settings screen's connection test. */
	public enum ConnectionCheck { TOKEN_ACCEPTED, TOKEN_REJECTED, UNREACHABLE, UNEXPECTED }

	public record ConnectionResult(ConnectionCheck check, int status) {}

	private record Pending(LinePayload payload, String json, long closedAtMillis) {}

	private enum Outcome { DELIVERED, DROPPED, RETRY }

	private final Supplier<Target> target;
	private final LongSupplier clock;
	private final Sleeper sleeper;
	private final Listener listener;
	private final Supplier<Boolean> logText;
	private final HttpClient http;
	private final ArrayDeque<Pending> queue = new ArrayDeque<>();
	private volatile boolean stopped;
	private volatile boolean failing;
	private Thread thread;

	public BridgeClient(Supplier<Target> target, LongSupplier clock, Sleeper sleeper, Listener listener,
			Supplier<Boolean> logText) {
		this.target = target;
		this.clock = clock;
		this.sleeper = sleeper;
		this.listener = listener;
		this.logText = logText;
		this.http = newHttpClient();
	}

	public BridgeClient(Supplier<Target> target, Listener listener, Supplier<Boolean> logText) {
		this(target, System::currentTimeMillis, Thread::sleep, listener, logText);
	}

	private static HttpClient newHttpClient() {
		return HttpClient.newBuilder()
			.connectTimeout(CONNECT_TIMEOUT)
			.version(HttpClient.Version.HTTP_1_1)
			.followRedirects(HttpClient.Redirect.NEVER)
			.build();
	}

	/** Queues a line that closed at {@code closedAtMillis}; its 60 s count from then. */
	public void enqueue(LinePayload payload, long closedAtMillis) {
		synchronized (queue) {
			queue.addLast(new Pending(payload, payload.toJson(), closedAtMillis));
			while (queue.size() > MAX_QUEUE) {
				Pending old = queue.removeFirst();
				LOG.warn("send queue full: dropped line {}", old.payload().clientLineId());
				listener.dropped(old.payload().clientLineId(), "queue full");
			}
			queue.notifyAll();
		}
	}

	public int queued() {
		synchronized (queue) {
			return queue.size();
		}
	}

	/** Whether the line being sent is failing right now. */
	public boolean failing() {
		return failing;
	}

	public synchronized void start() {
		if (thread != null) {
			return;
		}
		thread = new Thread(this::run, "vfg-send");
		thread.setDaemon(true);
		thread.start();
	}

	/** Stops the sender, giving a request in flight {@code graceMillis} to finish. */
	public void shutdown(long graceMillis) {
		stopped = true;
		synchronized (queue) {
			queue.notifyAll();
		}
		Thread t;
		synchronized (this) {
			t = thread;
		}
		if (t == null) {
			return;
		}
		try {
			t.join(graceMillis);
		} catch (InterruptedException e) {
			Thread.currentThread().interrupt();
		}
		t.interrupt();
	}

	private void run() {
		while (!stopped) {
			Pending next;
			synchronized (queue) {
				while (queue.isEmpty() && !stopped) {
					try {
						queue.wait();
					} catch (InterruptedException e) {
						return;
					}
				}
				if (stopped) {
					return;
				}
				next = queue.removeFirst();
			}
			try {
				deliver(next);
			} catch (InterruptedException e) {
				return;
			}
		}
	}

	/** Delivers the line at the head of the queue, retries included. For tests: returns false when there is none. */
	public boolean deliverNext() throws InterruptedException {
		Pending next;
		synchronized (queue) {
			next = queue.pollFirst();
		}
		if (next == null) {
			return false;
		}
		deliver(next);
		return true;
	}

	private void deliver(Pending p) throws InterruptedException {
		String lineId = p.payload().clientLineId();
		long deadline = p.closedAtMillis() + LINE_WINDOW_MILLIS;
		if (clock.getAsLong() > deadline) {
			drop(lineId, "older than 60 s before its first try");
			return;
		}
		int attempt = 0;
		while (!stopped || attempt == 0) {
			Outcome outcome = attempt(p, attempt + 1);
			if (outcome != Outcome.RETRY) {
				setFailing(false);
				return;
			}
			long wait = BACKOFF_MILLIS[Math.min(attempt, BACKOFF_MILLIS.length - 1)];
			if (clock.getAsLong() + wait > deadline) {
				setFailing(false);
				drop(lineId, "not delivered within 60 s");
				return;
			}
			setFailing(true);
			sleeper.sleep(wait);
			attempt++;
		}
	}

	private Outcome attempt(Pending p, int number) throws InterruptedException {
		String lineId = p.payload().clientLineId();
		Target t = target.get();
		if (t == null || t.linesUrl() == null || t.linesUrl().isBlank()) {
			LOG.warn("line {}: no bridge URL set; will retry", lineId);
			return Outcome.RETRY;
		}
		HttpResponse<String> response;
		try {
			HttpRequest request = HttpRequest.newBuilder(URI.create(t.linesUrl()))
				.timeout(REQUEST_TIMEOUT)
				.header("Authorization", "Bearer " + (t.token() == null ? "" : t.token()))
				.header("Content-Type", "application/json")
				.POST(HttpRequest.BodyPublishers.ofString(p.json(), StandardCharsets.UTF_8))
				.build();
			response = http.send(request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
		} catch (IOException e) {
			LOG.info("line {}: bridge unreachable ({}); will retry", lineId, e.getClass().getSimpleName());
			listener.attempted(lineId, number, 0, e.getClass().getSimpleName());
			return Outcome.RETRY;
		} catch (IllegalArgumentException e) {
			LOG.warn("line {}: bad bridge URL: {}", lineId, e.getMessage());
			listener.attempted(lineId, number, 0, "bad bridge URL");
			return Outcome.RETRY;
		}
		int status = response.statusCode();
		listener.attempted(lineId, number, status, abbreviate(response.body()));
		if (status == 202) {
			String id = idOf(response.body());
			LOG.debug("line {} delivered as {}", lineId, id);
			if (Boolean.TRUE.equals(logText.get())) {
				LOG.info("sent: {}", p.payload().text());
			}
			listener.delivered(lineId, id);
			return Outcome.DELIVERED;
		}
		if (status == 400) {
			LOG.warn("line {} rejected as a bad body (400): {}", lineId, abbreviate(response.body()));
			listener.dropped(lineId, "400");
			return Outcome.DROPPED;
		}
		if (status == 401) {
			LOG.warn("line {} dropped: the bridge rejected the client token (401)", lineId);
			listener.tokenRejected();
			listener.dropped(lineId, "401");
			return Outcome.DROPPED;
		}
		if (status == 408 || status == 429 || (status >= 500 && status <= 599)) {
			LOG.info("line {}: bridge answered {}; will retry", lineId, status);
			return Outcome.RETRY;
		}
		LOG.warn("line {} dropped: unexpected status {}", lineId, status);
		listener.dropped(lineId, "status " + status);
		return Outcome.DROPPED;
	}

	private void drop(String lineId, String reason) {
		LOG.warn("line {} dropped: {}", lineId, reason);
		listener.dropped(lineId, reason);
	}

	private void setFailing(boolean now) {
		if (failing != now) {
			failing = now;
			listener.deliveryProblem(now);
		}
	}

	private static String idOf(String body) {
		try {
			JsonElement e = JsonParser.parseString(body);
			if (e.isJsonObject() && e.getAsJsonObject().has("id")) {
				return e.getAsJsonObject().get("id").getAsString();
			}
		} catch (RuntimeException ignored) {
			// fall through
		}
		return "?";
	}

	private static String abbreviate(String s) {
		if (s == null) {
			return "";
		}
		s = s.replaceAll("\\s+", " ").trim();
		return s.length() > 300 ? s.substring(0, 300) + "…" : s;
	}

	/**
	 * The settings screen's "Test connection": posts {@code {}} with the token. The bridge
	 * checks the token before the body (§2), so {@code 401} means the token is rejected
	 * and {@code 400} means the bridge is reachable and the token accepted.
	 */
	public static CompletableFuture<ConnectionResult> testConnection(String linesUrl, String token) {
		HttpRequest request;
		try {
			request = HttpRequest.newBuilder(URI.create(linesUrl))
				.timeout(REQUEST_TIMEOUT)
				.header("Authorization", "Bearer " + (token == null ? "" : token))
				.header("Content-Type", "application/json")
				.POST(HttpRequest.BodyPublishers.ofString("{}", StandardCharsets.UTF_8))
				.build();
		} catch (IllegalArgumentException e) {
			return CompletableFuture.completedFuture(new ConnectionResult(ConnectionCheck.UNREACHABLE, 0));
		}
		return newHttpClient().sendAsync(request, HttpResponse.BodyHandlers.discarding())
			.handle((response, error) -> {
				if (error != null || response == null) {
					return new ConnectionResult(ConnectionCheck.UNREACHABLE, 0);
				}
				int status = response.statusCode();
				return switch (status) {
					case 400 -> new ConnectionResult(ConnectionCheck.TOKEN_ACCEPTED, status);
					case 401 -> new ConnectionResult(ConnectionCheck.TOKEN_REJECTED, status);
					default -> new ConnectionResult(ConnectionCheck.UNEXPECTED, status);
				};
			});
	}
}
