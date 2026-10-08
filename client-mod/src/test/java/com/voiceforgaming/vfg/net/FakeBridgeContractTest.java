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
 * rule of §2. Skipped where there is no {@code python3}. Loopback only.
 */
class FakeBridgeContractTest {
	private static final String TOKEN = "test";
	private Process bridge;
	private String url;
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
		ProcessBuilder pb = new ProcessBuilder("python3", script.toString(), "--port", Integer.toString(port));
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

	private int post(HttpClient http, String body) throws Exception {
		HttpRequest r = HttpRequest.newBuilder(URI.create(url)).header("Authorization", "Bearer " + TOKEN)
			.header("Content-Type", "application/json").POST(HttpRequest.BodyPublishers.ofString(body)).build();
		return http.send(r, HttpResponse.BodyHandlers.discarding()).statusCode();
	}
}
