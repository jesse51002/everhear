package com.voiceforgaming.vfg.net;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.net.ServerSocket;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/**
 * The mod's real request bytes against {@code tools/fake_bridge.py}, which checks every
 * rule of §2, and the incantation invoke's and list's bodies. Skipped where there is no
 * {@code python3}. Loopback only.
 */
class FakeBridgeContractTest {
	private static final String TOKEN = "test";
	private Process bridge;
	private String url;
	private String base;
	private final List<String> printed = Collections.synchronizedList(new ArrayList<>());

	@BeforeEach
	void startBridge() throws Exception {
		Path script = Path.of(System.getProperty("vfg.projectDir", "."), "tools", "fake_bridge.py");
		assumeTrue(Files.isRegularFile(script), "tools/fake_bridge.py not found");
		assumeTrue(hasPython(), "python3 is not installed");
		int port;
		try (ServerSocket s = new ServerSocket(0)) {
			port = s.getLocalPort();
		}
		ProcessBuilder pb = new ProcessBuilder("python3", script.toString(), "--port", Integer.toString(port),
			"--incantation", "fire ball=fireball,fire bowl");
		pb.environment().put("VFG_CLIENT_TOKEN", TOKEN);
		pb.redirectErrorStream(true);
		bridge = pb.start();
		BufferedReader out = new BufferedReader(new InputStreamReader(bridge.getInputStream(), StandardCharsets.UTF_8));
		String first = out.readLine();
		assertTrue(first != null && first.startsWith("fake bridge on"), "fake bridge said: " + first);
		Thread reader = new Thread(() -> {
			try {
				String line;
				while ((line = out.readLine()) != null) {
					printed.add(line);
				}
			} catch (IOException ignored) {
				// process ended
			}
		});
		reader.setDaemon(true);
		reader.start();
		url = "http://127.0.0.1:" + port + "/v1/lines";
		base = "http://127.0.0.1:" + port;
	}

	@AfterEach
	void stopBridge() throws InterruptedException {
		if (bridge != null) {
			bridge.destroy();
			bridge.waitFor(5, TimeUnit.SECONDS);
		}
	}

	private static boolean hasPython() {
		try {
			Process p = new ProcessBuilder("python3", "--version").redirectErrorStream(true).start();
			return p.waitFor(10, TimeUnit.SECONDS) && p.exitValue() == 0;
		} catch (IOException | InterruptedException e) {
			return false;
		}
	}

	@Test
	void theFakeBridgeAcceptsWhatTheModSendsAndRepeatsTheIdForARetry() throws Exception {
		List<String> ids = new ArrayList<>();
		List<String> dropped = new ArrayList<>();
		BridgeClient client = new BridgeClient(() -> new BridgeClient.Target(url, TOKEN), System::currentTimeMillis,
			ms -> {}, new BridgeClient.Listener() {
				@Override
				public void delivered(String clientLineId, String id) {
					ids.add(id);
				}

				@Override
				public void dropped(String clientLineId, String reason) {
					dropped.add(reason);
				}
			}, () -> false);
		long t = System.currentTimeMillis();
		LinePayload p = LinePayload.create(UUID.randomUUID(), "Steve", "build me a castle", t - 2300, t, -8.2, -23.5);
		client.enqueue(p, t);
		client.enqueue(p, t); // the same client_line_id again, as a retry would send it
		client.enqueue(LinePayload.create(UUID.randomUUID(), "Steve", "x".repeat(2000), t, t, -100.0, -100.0), t);
		client.enqueue(LinePayload.create(UUID.randomUUID(), "Steve", "it's \"quoted\" & <ok> ✓", t, t, 0.0, -0.1), t);
		while (client.deliverNext()) {
			// one at a time, in order
		}
		assertTrue(dropped.isEmpty(), "dropped: " + dropped);
		assertEquals(4, ids.size());
		assertEquals(ids.get(0), ids.get(1), "a repeated client_line_id gets the first id");
		for (String id : ids) {
			assertEquals(26, id.length());
		}
		Thread.sleep(200);
		assertEquals(3, printed.size(), "one printed line per accepted line: " + printed);
		assertTrue(printed.get(0).contains("Steve peak -8.2 mean -23.5 | build me a castle"), printed.get(0));
	}

	@Test
	void theTokenIsCheckedBeforeTheBodySoTheConnectionTestWorks() throws Exception {
		assertEquals(BridgeClient.ConnectionCheck.TOKEN_ACCEPTED, BridgeClient.testConnection(url, TOKEN).get().check());
		assertEquals(BridgeClient.ConnectionCheck.TOKEN_REJECTED, BridgeClient.testConnection(url, "wrong").get().check());
	}

	@Test
	void theFakeBridgeRejectsBodiesThatBreakSection2() throws Exception {
		HttpClient http = HttpClient.newHttpClient();
		String good = LinePayload.create(UUID.randomUUID(), "Steve", "hi", 1_000, 2_000, -8.2, -23.5).toJson();
		assertEquals(202, post(http, good));
		assertEquals(400, post(http, good.replace("\"text\":\"hi\"", "\"text\":\"   \"")));
		assertEquals(400, post(http, good.replace("-8.2", "1.5")));
		assertEquals(400, post(http, good.replace("\"mean_dbfs\":-23.5", "\"mean_dbfs\":-23.5,\"extra\":1")));
		assertEquals(400, post(http, good.replace(".000Z", "Z")));
		assertEquals(400, post(http, LinePayload.create(UUID.randomUUID(), "Steve", "hi", 2_000, 1_000, -1, -2).toJson()));
		assertEquals(400, post(http, new LinePayload(UUID.nameUUIDFromBytes(new byte[] {1}).toString(), "u", "Steve",
			"hi", 1_000, 2_000, -1, -2).toJson()));
	}

	@Test
	void theFakeBridgeAcceptsTheModsCastAndRepeatsTheAnswerForARetry() throws Exception {
		List<String> answers = new ArrayList<>();
		CastClient client = new CastClient(() -> new CastClient.Target(base + "/v1/casts", TOKEN),
			System::currentTimeMillis, ms -> {}, new CastClient.Listener() {
				@Override
				public void attempted(CastPayload cast, int attempt, int status, String body) {
					answers.add(status + " " + body);
				}
			});
		long t = System.currentTimeMillis();
		CastPayload cast = CastPayload.create(UUID.randomUUID(), "Steve", "fire ball", "fireball", t - 900, t - 300);
		assertEquals("fired", client.deliver(cast, t).castStatus());
		assertEquals("fired", client.deliver(cast, t).castStatus(), "a repeated client_cast_id");
		assertEquals(answers.get(0), answers.get(1));
		CastPayload noHeard = CastPayload.create(null, "Steve", "fire ball", null, t, t);
		assertEquals(202, client.deliver(noHeard, t).status(), "heard is optional");
		assertEquals(404, client.deliver(CastPayload.create(UUID.randomUUID(), "Steve", "ice wall", null, t, t), t)
			.status(), "not bound for the player");
		Thread.sleep(200);
		assertEquals(2, printed.stream().filter(l -> l.contains("cast \"fire ball\"")).count(), String.valueOf(printed));
	}

	@Test
	void theFakeBridgeRejectsCastBodiesThatBreakTheContract() throws Exception {
		HttpClient http = HttpClient.newHttpClient();
		String good = CastPayload.create(UUID.randomUUID(), "Steve", "fire ball", "fireball", 1_000, 2_000).toJson();
		assertEquals(202, post(http, base + "/v1/casts", good));
		assertEquals(400, post(http, base + "/v1/casts", good.replace("\"ended_at\":\"1970-01-01T00:00:02.000Z\"",
			"\"ended_at\":\"1970-01-01T00:00:00.500Z\"")), "ended before it started");
		assertEquals(400, post(http, base + "/v1/casts", good.replace("\"phrase\"", "\"extra\":1,\"phrase\"")));
		assertEquals(400, post(http, base + "/v1/casts", good.replace("\"Steve\"", "\"Steve Jobs\"")));
		assertEquals(401, HttpClient.newHttpClient().send(HttpRequest.newBuilder(URI.create(base + "/v1/casts"))
			.header("Authorization", "Bearer wrong").POST(HttpRequest.BodyPublishers.ofString(good)).build(),
			HttpResponse.BodyHandlers.discarding()).statusCode());
	}

	@Test
	void theFakeBridgeListsTheIncantationsAndHoldsAnUnchangedList() throws Exception {
		List<List<com.voiceforgaming.vfg.kws.Incantation>> lists = new ArrayList<>();
		IncantationClient client = new IncantationClient(() -> new IncantationClient.Target(base + "/v1/incantations",
			TOKEN), 0, ms -> {}, (version, list) -> lists.add(list));
		assertEquals(IncantationClient.Poll.UPDATED, client.poll("Steve"));
		assertEquals(1, client.version());
		assertEquals(List.of(List.of(new com.voiceforgaming.vfg.kws.Incantation("fire ball",
			List.of("fireball", "fire bowl"), 3.0))), lists);
		assertEquals(IncantationClient.Poll.UNCHANGED, client.poll("Steve"), "after=1 and wait=0: 304 at once");
	}

	private int post(HttpClient http, String url, String body) throws Exception {
		HttpRequest r = HttpRequest.newBuilder(URI.create(url)).header("Authorization", "Bearer " + TOKEN)
			.header("Content-Type", "application/json").POST(HttpRequest.BodyPublishers.ofString(body)).build();
		return http.send(r, HttpResponse.BodyHandlers.discarding()).statusCode();
	}

	private int post(HttpClient http, String body) throws Exception {
		HttpRequest r = HttpRequest.newBuilder(URI.create(url)).header("Authorization", "Bearer " + TOKEN)
			.header("Content-Type", "application/json").POST(HttpRequest.BodyPublishers.ofString(body)).build();
		return http.send(r, HttpResponse.BodyHandlers.discarding()).statusCode();
	}
}
