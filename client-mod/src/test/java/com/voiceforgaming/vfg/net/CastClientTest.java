package com.voiceforgaming.vfg.net;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.google.gson.JsonParser;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/** The cast sender against a JDK HttpServer standing in for the bridge, with an injected clock and sleeper. */
class CastClientTest {
	private static final String TOKEN = "test-token";
	private static final UUID PLAYER = UUID.fromString("069a79f4-44e9-4726-a5be-fca90e38aaf5");

	/** One request the stand-in received, and the fake time it arrived. */
	record Seen(long at, String authorization, String body) {
		String clientCastId() {
			return JsonParser.parseString(body).getAsJsonObject().get("client_cast_id").getAsString();
		}
	}

	private HttpServer server;
	private String url;
	private final AtomicLong now = new AtomicLong(1_000_000);
	private final List<Long> sleeps = Collections.synchronizedList(new ArrayList<>());
	private final List<Seen> seen = Collections.synchronizedList(new ArrayList<>());
	private final ConcurrentLinkedQueue<Integer> script = new ConcurrentLinkedQueue<>();
	private volatile int otherwise = 202;
	private final List<String> dropped = Collections.synchronizedList(new ArrayList<>());
	private final AtomicBoolean rejected = new AtomicBoolean();

	@BeforeEach
	void start() throws IOException {
		server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
		server.createContext("/v1/casts", exchange -> {
			String body = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
			seen.add(new Seen(now.get(), exchange.getRequestHeaders().getFirst("Authorization"), body));
			Integer next = script.poll();
			int status = next == null ? otherwise : next;
			byte[] reply = (status == 202 ? "{\"id\": \"01JABCDEFGHJKMNPQRSTVWXYZ0\", \"status\": \"fired\", \"copy\": "
				+ "\"command_2\"}" : "{\"error\": \"no\"}").getBytes(StandardCharsets.UTF_8);
			exchange.sendResponseHeaders(status, reply.length);
			try (OutputStream out = exchange.getResponseBody()) {
				out.write(reply);
			}
		});
		server.start();
		url = "http://127.0.0.1:" + server.getAddress().getPort() + "/v1/casts";
	}

	@AfterEach
	void stop() {
		server.stop(0);
	}

	private CastClient client() {
		return new CastClient(() -> new CastClient.Target(url, TOKEN), now::get, ms -> {
			sleeps.add(ms);
			now.addAndGet(ms);
		}, new CastClient.Listener() {
			@Override
			public void dropped(CastPayload cast, String reason) {
				dropped.add(reason);
			}

			@Override
			public void tokenRejected() {
				rejected.set(true);
			}
		});
	}

	private static CastPayload cast() {
		return CastPayload.create(PLAYER, "Steve", "fire ball", "fireball", 999_000, 999_600);
	}

	@Test
	void aFiredCastTakesOneRequestWithTheBearerTokenAndTheBody() throws Exception {
		CastPayload p = cast();
		CastClient.Result r = client().deliver(p, now.get());
		assertEquals(202, r.status());
		assertEquals("fired", r.castStatus());
		assertEquals(1, seen.size());
		assertEquals("Bearer " + TOKEN, seen.getFirst().authorization());
		assertEquals(JsonParser.parseString(p.toJson()), JsonParser.parseString(seen.getFirst().body()));
		assertTrue(sleeps.isEmpty());
	}

	@Test
	void aFailureIsTriedAgainWithTheSameClientCastId() throws Exception {
		script.addAll(List.of(500, 429, 202));
		CastPayload p = cast();
		CastClient.Result r = client().deliver(p, now.get());
		assertEquals(202, r.status());
		assertEquals(3, seen.size());
		for (Seen s : seen) {
			assertEquals(p.clientCastId(), s.clientCastId());
		}
		assertEquals(List.of(100L, 200L), sleeps);
	}

	@Test
	void aCastNotAnsweredWithinTwoSecondsOfTheHitIsDropped() throws Exception {
		otherwise = 500;
		long heardAt = now.get();
		client().deliver(cast(), heardAt);
		// tries at 0, 0.1, 0.3, 0.7 and 1.5 s; the next would start at 2.3 s
		assertEquals(List.of(100L, 200L, 400L, 800L), sleeps);
		assertEquals(5, seen.size());
		for (Seen s : seen) {
			assertTrue(s.at() - heardAt < CastClient.WINDOW_MILLIS, "a try at " + (s.at() - heardAt));
		}
		assertEquals(List.of("not answered within 2 s of the hit"), dropped);
	}

	@Test
	void aCastHeardLongerAgoThanTwoSecondsIsNotSent() throws Exception {
		client().deliver(cast(), now.get() - 2_500);
		assertTrue(seen.isEmpty());
		assertEquals(1, dropped.size());
	}

	@Test
	void anUnreachableBridgeIsTriedAgainThenDropped() throws Exception {
		server.stop(0);
		client().deliver(cast(), now.get());
		assertEquals(List.of(100L, 200L, 400L, 800L), sleeps);
		assertEquals(1, dropped.size());
		start(); // for @AfterEach
	}

	@Test
	void noSuchIncantationAndNoKitAreFinal() throws Exception {
		otherwise = 404;
		assertEquals(404, client().deliver(cast(), now.get()).status());
		otherwise = 503;
		assertEquals(503, client().deliver(cast(), now.get()).status());
		otherwise = 400;
		assertEquals(400, client().deliver(cast(), now.get()).status());
		assertEquals(3, seen.size());
		assertTrue(sleeps.isEmpty());
	}

	@Test
	void fourOhOneMarksTheTokenRejected() throws Exception {
		otherwise = 401;
		client().deliver(cast(), now.get());
		assertTrue(rejected.get());
		assertEquals(1, seen.size());
	}

	@Test
	void postReturnsAtOnceAndSendsOnItsOwnThread() throws Exception {
		CountDownLatch answered = new CountDownLatch(1);
		List<String> threads = Collections.synchronizedList(new ArrayList<>());
		CastClient c = new CastClient(() -> new CastClient.Target(url, TOKEN), new CastClient.Listener() {
			@Override
			public void answered(CastPayload cast, int status, String castStatus, long answeredAtMillis) {
				threads.add(Thread.currentThread().getName());
				answered.countDown();
			}
		});
		c.post(cast(), System.currentTimeMillis());
		assertTrue(answered.await(5, TimeUnit.SECONDS));
		assertEquals(List.of("vfg-cast"), threads);
		c.shutdown(1000);
	}
}
