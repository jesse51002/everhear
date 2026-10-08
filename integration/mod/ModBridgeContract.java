// The client mod's real HTTP client (com.voiceforgaming.vfg.net.BridgeClient and
// LinePayload, from client-mod/build/classes/java/main) against the REAL bridge.
// Run by integration/harness.py with the JDK's source launcher, nothing compiled to disk:
//
//   VFG_MOD_TOKEN=<the bridge's test token> \
//   java -cp <mod classes>:<gson>:<slf4j-api> ModBridgeContract.java http://127.0.0.1:<port>/v1/lines
//
// It prints one tab-separated record per fact; the harness checks them against vfg.db.
// The token comes from the environment so it never shows in a process list.
import com.voiceforgaming.vfg.net.BridgeClient;
import com.voiceforgaming.vfg.net.LinePayload;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.UUID;

public class ModBridgeContract {
	static void out(String key, String value) {
		System.out.println(key + "\t" + value.replace("\n", " "));
	}

	static final class Recorder implements BridgeClient.Listener {
		final String name;
		final List<String> delivered = Collections.synchronizedList(new ArrayList<>());
		final List<String> dropped = Collections.synchronizedList(new ArrayList<>());
		boolean tokenRejected;

		Recorder(String name) {
			this.name = name;
		}

		@Override
		public void delivered(String clientLineId, String id) {
			delivered.add(clientLineId + " " + id);
			out(name + ".delivered", clientLineId + " " + id);
		}

		@Override
		public void tokenRejected() {
			tokenRejected = true;
			out(name + ".token_rejected", "true");
		}

		@Override
		public void dropped(String clientLineId, String reason) {
			dropped.add(clientLineId + " " + reason);
			out(name + ".dropped", clientLineId + " " + reason);
		}
	}

	static BridgeClient client(String url, String token, Recorder r) {
		// No real sleeping between retries: a retry would only mean the bridge is down.
		return new BridgeClient(() -> new BridgeClient.Target(url, token), System::currentTimeMillis,
			ms -> {}, r, () -> false);
	}

	static void send(BridgeClient c, String tag, LinePayload p, long closedAt) throws InterruptedException {
		out(tag + ".payload", p.toJson());
		c.enqueue(p, closedAt);
		while (c.deliverNext()) {
			// one at a time, in order, as vfg-send does
		}
	}

	public static void main(String[] args) throws Exception {
		String url = args[0];
		String token = System.getenv("VFG_MOD_TOKEN");
		long t = System.currentTimeMillis();

		// 1. A line, then the same line again as a retry sends it (same client_line_id).
		Recorder good = new Recorder("good");
		BridgeClient c = client(url, token, good);
		LinePayload p = LinePayload.create(UUID.randomUUID(), "Steve", "build a tower by the lake",
			t - 2300, t, -8.2, -23.5);
		send(c, "line", p, t);
		send(c, "retry", p, t);

		// 2. Edge payloads the mod can produce: no player uuid, 2000 characters, quotes and unicode, 0 dB.
		send(c, "nouuid", LinePayload.create(null, "Alex", "no uuid here", t - 500, t, -30.0, -40.0), t);
		send(c, "long", LinePayload.create(UUID.randomUUID(), "Steve", "build " + "x".repeat(1994),
			t - 900, t, -12.3, -25.0), t);
		send(c, "quoted", LinePayload.create(UUID.randomUUID(), "Steve", "it's \"quoted\" & <ok> ✓",
			t - 400, t, 0.0, -0.1), t);

		// 3. A wrong token: the line is dropped with 401 and the token marked rejected.
		Recorder bad = new Recorder("bad");
		send(client(url, "wrong-" + token, bad), "badtoken",
			LinePayload.create(UUID.randomUUID(), "Steve", "this must never be stored", t, t, -10.0, -20.0), t);

		// 4. The settings screen's connection test: {} with the token.
		out("conn.good", String.valueOf(BridgeClient.testConnection(url, token).get()));
		out("conn.bad", String.valueOf(BridgeClient.testConnection(url, "wrong").get()));
		out("done", "true");
	}
}
