package com.voiceforgaming.vfg.net;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.google.gson.JsonObject;
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

/** The sender against a JDK HttpServer standing in for the bridge, with an injected clock and sleeper. */
class BridgeClientTest {
	private static final String TOKEN = "test-token";
	private static final UUID PLAYER = UUID.fromString("069a79f4-44e9-4726-a5be-fca90e38aaf5");

	/** One request the stand-in received, and the fake time it arrived. */
	record Seen(long at, String authorization, String contentType, String body) {
		String clientLineId() {
			return JsonParser.parseString(body).getAsJsonObject().get("client_line_id").getAsString();
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
	private final List<String> delivered = Collections.synchronizedList(new ArrayList<>());
	private final List<Boolean> problems = Collections.synchronizedList(new ArrayList<>());
	private final AtomicBoolean rejected = new AtomicBoolean();

	@BeforeEach
	void start() throws IOException {
		server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
		server.createContext("/v1/lines", exchange -> {
			String body = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
			seen.add(new Seen(now.get(), exchange.getRequestHeaders().getFirst("Authorization"),
				exchange.getRequestHeaders().getFirst("Content-Type"), body));
			Integer next = script.poll();
			int status = next == null ? otherwise : next;
			byte[] reply = (status == 202 ? "{\"id\": \"01JABCDEFGHJKMNPQRSTVWXYZ0\"}" : "{\"error\": \"no\"}")
				.getBytes(StandardCharsets.UTF_8);
			exchange.sendResponseHeaders(status, reply.length);
			try (OutputStream out = exchange.getResponseBody()) {
				out.write(reply);
			}
		});
		server.start();
		url = "http://127.0.0.1:" + server.getAddress().getPort() + "/v1/lines";
	}

	@AfterEach
	void stop() {
		server.stop(0);
	}

	private BridgeClient client() {
		return new BridgeClient(() -> new BridgeClient.Target(url, TOKEN), now::get, ms -> {
			sleeps.add(ms);
			now.addAndGet(ms);
		}, new BridgeClient.Listener() {
			@Override
			public void delivered(String clientLineId, String id) {
				delivered.add(clientLineId);
			}

			@Override
			public void tokenRejected() {
				rejected.set(true);
			}

			@Override
			public void deliveryProblem(boolean failing) {
				problems.add(failing);
			}

			@Override
			public void dropped(String clientLineId, String reason) {
				dropped.add(clientLineId);
			}
		}, () -> false);
	}

	private static LinePayload line(String text) {
		return LinePayload.create(PLAYER, "Steve", text, 1_000, 2_000, -8.2, -23.5);
	}

	@Test
	void twoHundredTwoTakesOneRequest() throws Exception {
		BridgeClient c = client();
		LinePayload p = line("build me a castle");
		c.enqueue(p, now.get());
		assertTrue(c.deliverNext());
		assertEquals(1, seen.size());
		assertEquals(List.of(p.clientLineId()), delivered);
		assertTrue(dropped.isEmpty());
		assertFalse(c.deliverNext());
	}

	@Test
	void theRequestCarriesTheBearerTokenAndTheSection2Body() throws Exception {
		BridgeClient c = client();
		LinePayload p = line("build me a castle");
		c.enqueue(p, now.get());
		c.deliverNext();
		Seen s = seen.get(0);
		assertEquals("Bearer " + TOKEN, s.authorization());
		assertEquals("application/json", s.contentType());
		JsonObject body = JsonParser.parseString(s.body()).getAsJsonObject();
		assertEquals(8, body.size());
		assertEquals(p.clientLineId(), body.get("client_line_id").getAsString());
		assertEquals("build me a castle", body.get("text").getAsString());
	}

	@Test
	void serverErrorsAreRetriedWithTheSameClientLineId() throws Exception {
		script.addAll(List.of(500, 500, 202));
		BridgeClient c = client();
		LinePayload p = line("try again");
		c.enqueue(p, now.get());
		c.deliverNext();
		assertEquals(3, seen.size());
		for (Seen s : seen) {
			assertEquals(p.clientLineId(), s.clientLineId());
		}
		assertEquals(List.of(1000L, 2000L), sleeps);
		assertEquals(List.of(p.clientLineId()), delivered);
		assertEquals(List.of(true, false), problems, "the delivery problem shows while failing, then clears");
	}

	@Test
	void alwaysFailingDropsTheLineAtSixtySeconds() throws Exception {
		otherwise = 500;
		BridgeClient c = client();
		long closedAt = now.get();
		LinePayload p = line("never arrives");
		c.enqueue(p, closedAt);
		c.deliverNext();
		// Tries at 0, 1, 3, 7, 15, 31 and 47 s; the next would start at 63 s.
		assertEquals(7, seen.size());
		assertEquals(List.of(1000L, 2000L, 4000L, 8000L, 16000L, 16000L), sleeps);
		for (Seen s : seen) {
			assertTrue(s.at() - closedAt < 60_000, "a try at " + (s.at() - closedAt));
			assertEquals(p.clientLineId(), s.clientLineId());
		}
		assertEquals(List.of(p.clientLineId()), dropped);
		assertTrue(delivered.isEmpty());
	}

	@Test
	void timeoutsAnd429And408AreRetriedToo() throws Exception {
		script.addAll(List.of(429, 408, 503, 202));
		BridgeClient c = client();
		c.enqueue(line("busy"), now.get());
		c.deliverNext();
		assertEquals(4, seen.size());
		assertEquals(1, delivered.size());
	}

	@Test
	void anUnreachableBridgeIsRetriedThenDropped() throws Exception {
		server.stop(0);
		BridgeClient c = client();
		c.enqueue(line("nobody home"), now.get());
		c.deliverNext();
		assertEquals(6, sleeps.size());
		assertEquals(1, dropped.size());
		start(); // for @AfterEach
	}

	@Test
	void fourOhOneTakesOneRequestAndMarksTheTokenRejected() throws Exception {
		otherwise = 401;
		BridgeClient c = client();
		c.enqueue(line("wrong token"), now.get());
		c.deliverNext();
		assertEquals(1, seen.size());
		assertTrue(rejected.get());
		assertEquals(1, dropped.size());
		assertTrue(sleeps.isEmpty());
	}

	@Test
	void laterLinesAreStillTriedAfterA401() throws Exception {
		script.add(401);
		BridgeClient c = client();
		c.enqueue(line("first"), now.get());
		LinePayload second = line("second");
		c.enqueue(second, now.get());
		c.deliverNext();
		c.deliverNext();
		assertEquals(2, seen.size());
		assertEquals(List.of(second.clientLineId()), delivered);
	}

	@Test
	void fourHundredTakesOneRequest() throws Exception {
		otherwise = 400;
		BridgeClient c = client();
		c.enqueue(line("bad body"), now.get());
		c.deliverNext();
		assertEquals(1, seen.size());
		assertFalse(rejected.get());
		assertEquals(1, dropped.size());
		assertTrue(sleeps.isEmpty());
	}

	@Test
	void otherStatusesAreDropped() throws Exception {
		otherwise = 404;
		BridgeClient c = client();
		c.enqueue(line("lost"), now.get());
		c.deliverNext();
		assertEquals(1, seen.size());
		assertEquals(1, dropped.size());
	}

	@Test
	void linesQueuedBeforeLeavingAreStillDeliveredEachWithinItsSixtySeconds() throws Exception {
		// Three lines closed just before the player left; the bridge is down for a while.
		script.addAll(List.of(503, 503, 503, 503));
		BridgeClient c = client();
		long left = now.get();
		List<LinePayload> lines = List.of(line("one"), line("two"), line("three"));
		for (int i = 0; i < lines.size(); i++) {
			c.enqueue(lines.get(i), left - 3000 + i * 1000);
		}
		while (c.deliverNext()) {
			// the sender keeps going after the session ended
		}
		assertEquals(lines.stream().map(LinePayload::clientLineId).toList(), delivered);
		for (int i = 0; i < lines.size(); i++) {
			String id = lines.get(i).clientLineId();
			long closedAt = left - 3000 + i * 1000;
			long deliveredAt = seen.stream().filter(s -> s.clientLineId().equals(id) && seen.indexOf(s) >= 4)
				.mapToLong(Seen::at).findFirst().orElseThrow();
			assertTrue(deliveredAt - closedAt < 60_000, "line " + i + " delivered after " + (deliveredAt - closedAt));
		}
	}

	@Test
	void aLineOlderThanSixtySecondsBeforeItsFirstTryIsDroppedWithoutARequest() throws Exception {
		BridgeClient c = client();
		c.enqueue(line("stale"), now.get() - 61_000);
		c.deliverNext();
		assertTrue(seen.isEmpty());
		assertEquals(1, dropped.size());
	}

	@Test
	void theQueueHoldsTwoHundredAndDropsTheOldest() {
		BridgeClient c = client();
		LinePayload first = line("oldest");
		c.enqueue(first, now.get());
		for (int i = 0; i < 200; i++) {
			c.enqueue(line("line " + i), now.get());
		}
		assertEquals(200, c.queued());
		assertEquals(List.of(first.clientLineId()), dropped);
	}

	@Test
	void theSenderThreadDeliversInOrder() throws Exception {
		CountDownLatch done = new CountDownLatch(3);
		BridgeClient c = new BridgeClient(() -> new BridgeClient.Target(url, TOKEN), new BridgeClient.Listener() {
			@Override
			public void delivered(String clientLineId, String id) {
				delivered.add(clientLineId);
				done.countDown();
			}
		}, () -> false);
		c.start();
		List<LinePayload> lines = List.of(line("a"), line("b"), line("c"));
		lines.forEach(p -> c.enqueue(p, System.currentTimeMillis()));
		assertTrue(done.await(10, TimeUnit.SECONDS));
		assertEquals(lines.stream().map(LinePayload::clientLineId).toList(), delivered);
		c.shutdown(2000);
	}

	@Test
	void connectionTestReadsTheTokenBeforeBodyOrder() throws Exception {
		otherwise = 400;
		assertEquals(BridgeClient.ConnectionCheck.TOKEN_ACCEPTED, BridgeClient.testConnection(url, TOKEN).get().check());
		assertEquals("{}", seen.get(0).body());
		assertEquals("Bearer " + TOKEN, seen.get(0).authorization());
		otherwise = 401;
		assertEquals(BridgeClient.ConnectionCheck.TOKEN_REJECTED, BridgeClient.testConnection(url, "x").get().check());
		otherwise = 500;
		BridgeClient.ConnectionResult r = BridgeClient.testConnection(url, TOKEN).get();
		assertEquals(BridgeClient.ConnectionCheck.UNEXPECTED, r.check());
		assertEquals(500, r.status());
		server.stop(0);
		assertEquals(BridgeClient.ConnectionCheck.UNREACHABLE, BridgeClient.testConnection(url, TOKEN).get().check());
		start();
	}
}
