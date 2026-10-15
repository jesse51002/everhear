package com.voiceforgaming.vfg.net;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.sun.net.httpserver.HttpServer;
import com.voiceforgaming.vfg.kws.Incantation;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/** The list's long poll against a JDK HttpServer standing in for the bridge, which answers what each test scripts. */
class IncantationClientTest {
	private static final String TOKEN = "test-token";
	private static final String LIST = """
		{"version": 7, "player": "Steve", "incantations": [
		  {"phrase": "fire ball", "aliases": ["fireball", "fire bowl"], "cooldown_s": 3.0},
		  {"phrase": "ice wall", "aliases": [], "cooldown_s": 5}]}""";

	/** A status and a body the stand-in answers with. */
	record Answer(int status, String body) {}

	private HttpServer server;
	private String url;
	private final List<String> queries = Collections.synchronizedList(new ArrayList<>());
	private final List<String> authorizations = Collections.synchronizedList(new ArrayList<>());
	private final ConcurrentLinkedQueue<Answer> script = new ConcurrentLinkedQueue<>();
	private final List<List<Incantation>> updates = Collections.synchronizedList(new ArrayList<>());
	private final List<List<String>> phraseUpdates = Collections.synchronizedList(new ArrayList<>());
	private final List<Long> sleeps = Collections.synchronizedList(new ArrayList<>());
	private final AtomicBoolean rejected = new AtomicBoolean();

	@BeforeEach
	void start() throws IOException {
		server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
		server.createContext("/v1/incantations", exchange -> {
			queries.add(exchange.getRequestURI().getRawQuery());
			authorizations.add(exchange.getRequestHeaders().getFirst("Authorization"));
			Answer a = script.poll();
			if (a == null) {
				a = new Answer(304, "");
			}
			byte[] body = a.body().getBytes(StandardCharsets.UTF_8);
			exchange.sendResponseHeaders(a.status(), body.length == 0 ? -1 : body.length);
			try (OutputStream out = exchange.getResponseBody()) {
				out.write(body);
			}
		});
		server.start();
		url = "http://127.0.0.1:" + server.getAddress().getPort() + "/v1/incantations";
	}

	@AfterEach
	void stop() {
		server.stop(0);
	}

	private IncantationClient client() {
		return new IncantationClient(() -> new IncantationClient.Target(url, TOKEN), 25, sleeps::add,
			new IncantationClient.Listener() {
				@Override
				public void updated(long version, List<Incantation> incantations, List<String> serverPhrases) {
					updates.add(incantations);
					phraseUpdates.add(serverPhrases);
				}

				@Override
				public void tokenRejected() {
					rejected.set(true);
				}
			});
	}

	@Test
	void aTwoHundredUpdatesTheListAndItsVersion() throws Exception {
		script.add(new Answer(200, LIST));
		IncantationClient c = client();
		assertEquals(IncantationClient.Poll.UPDATED, c.poll("Steve"));
		assertEquals(7, c.version());
		assertEquals(List.of(List.of(new Incantation("fire ball", List.of("fireball", "fire bowl"), 3.0),
			new Incantation("ice wall", List.of(), 5.0))), updates);
		assertEquals("Bearer " + TOKEN, authorizations.getFirst());
	}

	@Test
	void theFirstAskHasNoAfterAndTheNextSendsTheVersionItHas() throws Exception {
		script.add(new Answer(200, LIST));
		IncantationClient c = client();
		c.poll("Steve");
		c.poll("Steve");
		assertEquals(List.of("player=Steve&wait=25", "player=Steve&after=7&wait=25"), queries);
	}

	@Test
	void aThreeOhFourKeepsTheList() throws Exception {
		script.add(new Answer(200, LIST));
		script.add(new Answer(304, ""));
		IncantationClient c = client();
		c.poll("Steve");
		assertEquals(IncantationClient.Poll.UNCHANGED, c.poll("Steve"));
		assertEquals(1, updates.size());
		assertEquals(7, c.version());
	}

	@Test
	void theNameIsSentEncoded() throws Exception {
		script.add(new Answer(200, LIST));
		client().poll("Ste ve&x");
		assertEquals("player=Ste+ve%26x&wait=25", queries.getFirst());
	}

	@Test
	void aRejectedTokenAndErrorsAreToldApart() throws Exception {
		script.add(new Answer(401, "{\"error\": \"unauthorized\"}"));
		script.add(new Answer(503, "{\"error\": \"incantations are not set up on this bridge\"}"));
		script.add(new Answer(200, "not json"));
		IncantationClient c = client();
		assertEquals(IncantationClient.Poll.REJECTED, c.poll("Steve"));
		assertTrue(rejected.get());
		assertEquals(IncantationClient.Poll.FAILED, c.poll("Steve"));
		assertEquals(IncantationClient.Poll.FAILED, c.poll("Steve"));
		assertTrue(updates.isEmpty());
		assertEquals(-1, c.version());
	}

	@Test
	void theThreadAsksAgainAfterEachAnswerAndBacksOffOnErrors() throws Exception {
		script.add(new Answer(500, ""));
		script.add(new Answer(500, ""));
		script.add(new Answer(200, LIST));
		CountDownLatch fourth = new CountDownLatch(4);
		HttpServer counting = server;
		counting.removeContext("/v1/incantations");
		counting.createContext("/v1/incantations", exchange -> {
			queries.add(exchange.getRequestURI().getRawQuery());
			Answer a = script.poll();
			if (a == null) {
				a = new Answer(304, "");
			}
			byte[] body = a.body().getBytes(StandardCharsets.UTF_8);
			exchange.sendResponseHeaders(a.status(), body.length == 0 ? -1 : body.length);
			try (OutputStream out = exchange.getResponseBody()) {
				out.write(body);
			}
			fourth.countDown();
		});
		IncantationClient c = client();
		c.start("Steve");
		assertTrue(fourth.await(5, TimeUnit.SECONDS));
		c.stop();
		assertEquals(List.of(1000L, 2000L), sleeps.subList(0, 2), "1 s, then 2 s after the errors; none after an answer");
		assertEquals("player=Steve&after=7&wait=25", queries.get(3));
		assertEquals(1, updates.size());
	}

	@Test
	void serverPhrasesComeWithTheList() throws Exception {
		script.add(new Answer(200, """
			{"version": 9, "player": "Steve", "incantations": [{"phrase": "fire ball", "aliases": [], "cooldown_s": 3}],
			 "server_phrases": ["fire ball", " frost nova ", "", "fire ball", 7, "ice wall"]}"""));
		IncantationClient c = client();
		assertEquals(IncantationClient.Poll.UPDATED, c.poll("Steve"));
		assertEquals(List.of("fire ball", "frost nova", "ice wall"), phraseUpdates.getFirst(),
			"trimmed, blank, repeated and non-text ones left out");
	}

	@Test
	void aBridgeWithoutServerPhrasesGivesNull() throws Exception {
		script.add(new Answer(200, LIST));
		script.add(new Answer(200, LIST.replace("}]}", "}], \"server_phrases\": \"fire ball\"}")));
		IncantationClient c = client();
		c.poll("Steve");
		c.poll("Steve");
		assertEquals(2, phraseUpdates.size());
		assertEquals(null, phraseUpdates.get(0), "no field: an older bridge");
		assertEquals(null, phraseUpdates.get(1), "not a list: the same");
		assertEquals(2, updates.get(1).size(), "the player's own list still parses");
	}

	@Test
	void anEmptyServerPhrasesListIsNotNull() {
		assertEquals(List.of(), IncantationClient.phrases(com.google.gson.JsonParser.parseString("[]")));
		assertEquals(null, IncantationClient.phrases(null));
		assertEquals(null, IncantationClient.phrases(com.google.gson.JsonNull.INSTANCE));
	}
}
