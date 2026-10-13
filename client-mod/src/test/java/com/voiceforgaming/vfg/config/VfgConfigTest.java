package com.voiceforgaming.vfg.config;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.FileTime;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class VfgConfigTest {
	@TempDir
	Path dir;

	@Test
	void aMissingFileGetsTheDefaultsWritten() throws Exception {
		Path file = dir.resolve("config/vfg-client.json");
		VfgConfig c = VfgConfig.load(file);
		assertTrue(Files.exists(file));
		JsonObject o = JsonParser.parseString(Files.readString(file)).getAsJsonObject();
		assertEquals(List.of("server_address", "bridge_url", "client_token", "mic_device", "model", "icon_corner", "muted",
			"turn_model", "turn_threshold", "silence_seconds", "vad_threshold", "stt_threads", "log_text", "incantations",
			"incantation_threshold", "incantation_boost", "incantation_recheck_seconds", "incantation_paths",
			"incantation_spotter_aliases", "vosk", "vosk_confirm"),
			List.copyOf(o.keySet()));
		assertEquals("parakeet-110m-en", o.get("model").getAsString());
		assertEquals("top_left", o.get("icon_corner").getAsString());
		assertEquals(3, o.get("silence_seconds").getAsInt());
		assertEquals("3", o.get("silence_seconds").toString());
		assertEquals(0.5, o.get("vad_threshold").getAsDouble());
		assertEquals(2, o.get("stt_threads").getAsInt());
		assertFalse(o.get("muted").getAsBoolean());
		assertFalse(o.get("log_text").getAsBoolean());
		assertEquals(3.0, c.silenceSeconds);
	}

	@Test
	void aFileThatDoesNotParseIsRenamedBadAndTheDefaultsAreUsed() throws Exception {
		Path file = dir.resolve("vfg-client.json");
		Files.writeString(file, "{ not json");
		VfgConfig c = VfgConfig.load(file);
		assertEquals("", c.serverAddress);
		assertEquals("{ not json", Files.readString(dir.resolve("vfg-client.json.bad")));
		assertTrue(JsonParser.parseString(Files.readString(file)).isJsonObject());
	}

	@Test
	void outOfRangeValuesFallBackToTheDefault() throws Exception {
		Path file = dir.resolve("vfg-client.json");
		Files.writeString(file, """
			{ "server_address": "play.example.com", "silence_seconds": 1, "vad_threshold": 0.95,
			  "stt_threads": 9, "model": "whisper", "icon_corner": "middle", "muted": "yes" }
			""");
		VfgConfig c = VfgConfig.load(file);
		assertEquals("play.example.com", c.serverAddress);
		assertEquals(3.0, c.silenceSeconds);
		assertEquals(0.5, c.vadThreshold);
		assertEquals(2, c.sttThreads);
		assertEquals("parakeet-110m-en", c.model, "an unknown model falls back to the default");
		assertEquals("top_left", c.iconCorner);
		assertFalse(c.muted);
	}

	@Test
	void aSavedMoonshineModelIsKept() throws Exception {
		for (String model : List.of("moonshine-base-en", "moonshine-tiny-en")) {
			Path file = dir.resolve(model + ".json");
			Files.writeString(file, "{ \"model\": \"" + model + "\" }");
			VfgConfig c = VfgConfig.load(file);
			assertEquals(model, c.model, "not migrated");
			c.save(file);
			assertTrue(Files.readString(file).contains("\"model\": \"" + model + "\""));
		}
	}

	@Test
	void parakeetIsTheDefaultAndItsShortNameWorks() throws Exception {
		assertEquals("parakeet-110m-en", new VfgConfig().model);
		Path file = dir.resolve("short.json");
		Files.writeString(file, "{ \"model\": \"parakeet-110m\" }");
		assertEquals("parakeet-110m-en", VfgConfig.load(file).model);
		Files.writeString(file, "{ \"server_address\": \"x\" }");
		assertEquals("parakeet-110m-en", VfgConfig.load(file).model, "a file with no model gets the default");
	}

	@Test
	void inRangeValuesAreKept() throws Exception {
		Path file = dir.resolve("vfg-client.json");
		Files.writeString(file, """
			{ "silence_seconds": 8, "vad_threshold": 0.3, "stt_threads": 4, "model": "moonshine-tiny-en",
			  "icon_corner": "bottom_right", "muted": true, "log_text": true, "client_token": "abc" }
			""");
		VfgConfig c = VfgConfig.load(file);
		assertEquals(8.0, c.silenceSeconds);
		assertEquals(0.3, c.vadThreshold);
		assertEquals(4, c.sttThreads);
		assertEquals("moonshine-tiny-en", c.model);
		assertEquals("bottom_right", c.iconCorner);
		assertTrue(c.muted);
		assertTrue(c.logText);
		assertEquals("abc", c.clientToken);
	}

	@Test
	void theTurnModelIsSmartTurnWithItsOwnThresholdWrittenAsNull() throws Exception {
		Path file = dir.resolve("vfg-client.json");
		VfgConfig c = VfgConfig.load(file);
		assertEquals("smart-turn-v3.2", c.turnModel);
		assertNull(c.turnThreshold);
		JsonObject o = JsonParser.parseString(Files.readString(file)).getAsJsonObject();
		assertEquals("smart-turn-v3.2", o.get("turn_model").getAsString());
		assertTrue(o.get("turn_threshold").isJsonNull());
		assertTrue(Files.readString(file).contains("\"turn_threshold\": null"), "the key is there, with no number");
	}

	@Test
	void turnModelIsOneOfTheModelsOrTheDefault() throws Exception {
		Path file = dir.resolve("vfg-client.json");
		for (String model : List.of("smart-turn-v3.2", "turn-1-mini", "off")) {
			Files.writeString(file, "{ \"turn_model\": \"" + model + "\" }");
			assertEquals(model, VfgConfig.load(file).turnModel);
		}
		Files.writeString(file, "{ \"turn_model\": \"semantic-vad\" }");
		assertEquals("smart-turn-v3.2", VfgConfig.load(file).turnModel);
		Files.writeString(file, "{ \"turn_model\": 3 }");
		assertEquals("smart-turn-v3.2", VfgConfig.load(file).turnModel);
	}

	@Test
	void turnThresholdIs005To095AndAnythingElseIsTheModelsOwn() throws Exception {
		Path file = dir.resolve("vfg-client.json");
		for (double kept : new double[] {0.05, 0.5, 0.72, 0.95}) {
			Files.writeString(file, "{ \"turn_model\": \"off\", \"turn_threshold\": " + kept + " }");
			assertEquals(kept, VfgConfig.load(file).turnThreshold);
		}
		for (String unset : List.of("0.04", "0.96", "-1", "\"0.6\"", "true", "null")) {
			Files.writeString(file, "{ \"turn_model\": \"off\", \"turn_threshold\": " + unset + " }");
			assertNull(VfgConfig.load(file).turnThreshold, unset);
		}
		Files.writeString(file, "{ \"turn_model\": \"off\" }");
		assertNull(VfgConfig.load(file).turnThreshold, "no key");
	}

	@Test
	void aSetThresholdIsWrittenAsANumberAndAnUnsetOneStaysUnsetAcrossModels() throws Exception {
		Path file = dir.resolve("vfg-client.json");
		VfgConfig c = VfgConfig.load(file);
		c.turnThreshold = 0.7;
		c.save(file);
		assertEquals(0.7, JsonParser.parseString(Files.readString(file)).getAsJsonObject().get("turn_threshold")
			.getAsDouble());
		assertEquals(0.7, VfgConfig.load(file).turnThreshold);

		c.turnThreshold = null;
		c.save(file);
		VfgConfig smart = VfgConfig.load(file);
		smart.turnModel = "turn-1-mini"; // a player switches models
		smart.save(file);
		VfgConfig mini = VfgConfig.load(file);
		assertEquals("turn-1-mini", mini.turnModel);
		assertNull(mini.turnThreshold, "no threshold carried over from the other model");
	}

	@Test
	void silenceSecondsIsStill2To30() throws Exception {
		Path file = dir.resolve("vfg-client.json");
		for (double kept : new double[] {2, 3, 4.5, 30}) {
			Files.writeString(file, "{ \"turn_model\": \"off\", \"silence_seconds\": " + kept + " }");
			assertEquals(kept, VfgConfig.load(file).silenceSeconds);
		}
		for (double out : new double[] {1.9, 30.5}) {
			Files.writeString(file, "{ \"turn_model\": \"off\", \"silence_seconds\": " + out + " }");
			assertEquals(3.0, VfgConfig.load(file).silenceSeconds, "falls back to the default, 3");
		}
	}

	@Test
	void aFileFromBeforeTheTurnModelMovesFrom5To3AndIsWrittenBackOnce() throws Exception {
		Path file = dir.resolve("vfg-client.json");
		Files.writeString(file, """
			{ "server_address": "play.example.com", "client_token": "abc", "model": "parakeet-110m-en",
			  "muted": false, "silence_seconds": 5, "vad_threshold": 0.5, "stt_threads": 2, "log_text": false }
			""");
		VfgConfig c = VfgConfig.load(file);
		assertEquals(3.0, c.silenceSeconds);
		assertEquals("smart-turn-v3.2", c.turnModel);
		JsonObject o = JsonParser.parseString(Files.readString(file)).getAsJsonObject();
		assertEquals("3", o.get("silence_seconds").toString(), "written back");
		assertEquals("smart-turn-v3.2", o.get("turn_model").getAsString());
		assertTrue(o.get("turn_threshold").isJsonNull());
		assertEquals("play.example.com", o.get("server_address").getAsString());
		assertEquals("abc", o.get("client_token").getAsString());

		// Read again: nothing moves and nothing is written.
		String written = Files.readString(file);
		FileTime before = FileTime.fromMillis(1_000_000);
		Files.setLastModifiedTime(file, before);
		assertEquals(3.0, VfgConfig.load(file).silenceSeconds);
		assertEquals(written, Files.readString(file));
		assertEquals(before, Files.getLastModifiedTime(file));

		// A 5 chosen after the move is the player's, and stays.
		Files.writeString(file, written.replace("\"silence_seconds\": 3", "\"silence_seconds\": 5"));
		String chosen = Files.readString(file);
		Files.setLastModifiedTime(file, before);
		assertEquals(5.0, VfgConfig.load(file).silenceSeconds);
		assertEquals(chosen, Files.readString(file));
		assertEquals(before, Files.getLastModifiedTime(file));
	}

	@Test
	void theMoveKeepsASilenceThePlayerChose() throws Exception {
		Path file = dir.resolve("vfg-client.json");
		Files.writeString(file, "{ \"silence_seconds\": 8 }");
		assertEquals(8.0, VfgConfig.load(file).silenceSeconds);
		JsonObject o = JsonParser.parseString(Files.readString(file)).getAsJsonObject();
		assertEquals("8", o.get("silence_seconds").toString());
		assertEquals("smart-turn-v3.2", o.get("turn_model").getAsString(), "written back with the new keys");
	}

	@Test
	void offWithFiveSecondsIsReadAsWritten() throws Exception {
		Path file = dir.resolve("vfg-client.json");
		Files.writeString(file, "{ \"turn_model\": \"off\", \"silence_seconds\": 5 }");
		VfgConfig c = VfgConfig.load(file);
		assertEquals("off", c.turnModel);
		assertEquals(5.0, c.silenceSeconds);
	}

	@Test
	void copyAndToStringCarryTheTurnKeys() {
		VfgConfig c = new VfgConfig();
		c.turnModel = "turn-1-mini";
		c.turnThreshold = 0.42;
		VfgConfig copy = c.copy();
		assertEquals("turn-1-mini", copy.turnModel);
		assertEquals(0.42, copy.turnThreshold);
		assertTrue(c.toString().contains("turn_model=turn-1-mini"), c.toString());
		assertTrue(c.toString().contains("turn_threshold=0.42"), c.toString());
		assertTrue(new VfgConfig().toString().contains("turn_threshold=(the model's own)"));
	}

	@Test
	void theSpotterIsOnAtThreshold025AndBoost15ByDefault() throws Exception {
		Path file = dir.resolve("vfg-client.json");
		VfgConfig c = VfgConfig.load(file);
		assertTrue(c.incantations);
		assertEquals(0.25, c.incantationThreshold);
		assertEquals(1.5, c.incantationBoost);
		assertEquals(3.0, c.incantationRecheckSeconds);
		JsonObject o = JsonParser.parseString(Files.readString(file)).getAsJsonObject();
		assertTrue(o.get("incantations").getAsBoolean());
		assertEquals(0.25, o.get("incantation_threshold").getAsDouble());
		assertEquals(1.5, o.get("incantation_boost").getAsDouble());
		assertEquals("3", o.get("incantation_recheck_seconds").toString());
	}

	@Test
	void theRecheckReachesBack1To10SecondsOr0ForNone() throws Exception {
		Path file = dir.resolve("vfg-client.json");
		for (double kept : new double[] {0, 1, 2.5, 3, 10}) {
			Files.writeString(file, "{ \"turn_model\": \"off\", \"incantation_recheck_seconds\": " + kept + " }");
			assertEquals(kept, VfgConfig.load(file).incantationRecheckSeconds);
		}
		for (String out : List.of("0.5", "0.99", "10.5", "-1", "\"3\"", "true")) {
			Files.writeString(file, "{ \"turn_model\": \"off\", \"incantation_recheck_seconds\": " + out + " }");
			assertEquals(3.0, VfgConfig.load(file).incantationRecheckSeconds, out + " falls back to the default, 3");
		}
		VfgConfig c = VfgConfig.load(file);
		c.incantationRecheckSeconds = 2.5;
		c.save(file);
		assertEquals("2.5", JsonParser.parseString(Files.readString(file)).getAsJsonObject()
			.get("incantation_recheck_seconds").toString());
	}

	@Test
	void incantationSettingsInRangeAreKeptAndOthersFallBack() throws Exception {
		Path file = dir.resolve("vfg-client.json");
		Files.writeString(file, """
			{ "turn_model": "smart-turn-v3.2", "incantations": false, "incantation_threshold": 0.1,
			  "incantation_boost": 2 }
			""");
		VfgConfig c = VfgConfig.load(file);
		assertFalse(c.incantations);
		assertEquals(0.1, c.incantationThreshold);
		assertEquals(2.0, c.incantationBoost);
		Files.writeString(file, """
			{ "turn_model": "smart-turn-v3.2", "incantations": "no", "incantation_threshold": 0.99,
			  "incantation_boost": 9 }
			""");
		VfgConfig bad = VfgConfig.load(file);
		assertTrue(bad.incantations);
		assertEquals(0.25, bad.incantationThreshold);
		assertEquals(1.5, bad.incantationBoost);
	}

	@Test
	void copyAndToStringCarryTheIncantationKeys() {
		VfgConfig c = new VfgConfig();
		c.incantations = false;
		c.incantationThreshold = 0.2;
		c.incantationBoost = 2.0;
		c.incantationRecheckSeconds = 0;
		VfgConfig copy = c.copy();
		assertFalse(copy.incantations);
		assertEquals(0.2, copy.incantationThreshold);
		assertEquals(2.0, copy.incantationBoost);
		assertEquals(0.0, copy.incantationRecheckSeconds);
		assertTrue(c.toString().contains("incantations=false, incantation_threshold=0.2, incantation_boost=2.0,"
			+ " incantation_recheck_seconds=0.0"), c.toString());
	}

	@Test
	void theSpottersBeamIs16PathsAndVoskIsOnByDefault() throws Exception {
		Path file = dir.resolve("vfg-client.json");
		VfgConfig c = VfgConfig.load(file);
		assertEquals(16, c.incantationPaths);
		assertTrue(c.vosk);
		JsonObject o = JsonParser.parseString(Files.readString(file)).getAsJsonObject();
		assertEquals("16", o.get("incantation_paths").toString());
		assertTrue(o.get("vosk").getAsBoolean());
		assertTrue(c.toString().endsWith(", incantation_paths=16, incantation_spotter_aliases=10, vosk=true,"
			+ " vosk_confirm=false]"), c.toString());
		assertFalse(c.voskConfirm);
		Files.writeString(file, "{ \"turn_model\": \"off\", \"vosk_confirm\": true }");
		VfgConfig on = VfgConfig.load(file);
		assertTrue(on.voskConfirm);
		assertTrue(on.copy().voskConfirm);
		Files.writeString(file, "{ \"turn_model\": \"off\", \"vosk_confirm\": \"yes\" }");
		assertFalse(VfgConfig.load(file).voskConfirm);
	}

	@Test
	void theBeamIsAWholeNumberFrom1To64AndVoskTrueOrFalse() throws Exception {
		Path file = dir.resolve("vfg-client.json");
		for (int kept : new int[] {1, 4, 8, 32, 64}) {
			Files.writeString(file, "{ \"turn_model\": \"off\", \"incantation_paths\": " + kept + " }");
			assertEquals(kept, VfgConfig.load(file).incantationPaths);
		}
		for (String out : List.of("0", "65", "-4", "8.5", "\"8\"", "true", "null")) {
			Files.writeString(file, "{ \"turn_model\": \"off\", \"incantation_paths\": " + out + " }");
			assertEquals(16, VfgConfig.load(file).incantationPaths, out + " falls back to the default, 16");
		}
		Files.writeString(file, "{ \"turn_model\": \"off\", \"vosk\": false }");
		VfgConfig off = VfgConfig.load(file);
		assertFalse(off.vosk);
		assertTrue(off.incantations, "the spotter stays on");
		for (String out : List.of("\"no\"", "0", "null")) {
			Files.writeString(file, "{ \"turn_model\": \"off\", \"vosk\": " + out + " }");
			assertTrue(VfgConfig.load(file).vosk, out + " falls back to the default, true");
		}
		off.incantationPaths = 8;
		off.save(file);
		VfgConfig again = VfgConfig.load(file);
		assertEquals(8, again.incantationPaths);
		assertFalse(again.vosk);
		VfgConfig copy = again.copy();
		assertEquals(8, copy.incantationPaths);
		assertFalse(copy.vosk);
		assertTrue(again.toString().contains("incantation_paths=8, incantation_spotter_aliases=10, vosk=false"),
			again.toString());
	}

	@Test
	void theSpotterKeepsTheFirst10AliasesByDefaultAnd0To24Otherwise() throws Exception {
		Path file = dir.resolve("vfg-client.json");
		VfgConfig c = VfgConfig.load(file);
		assertEquals(10, c.incantationSpotterAliases);
		assertEquals("10", JsonParser.parseString(Files.readString(file)).getAsJsonObject()
			.get("incantation_spotter_aliases").toString());
		for (int kept : new int[] {0, 6, 20, 24}) {
			Files.writeString(file, "{ \"turn_model\": \"off\", \"incantation_spotter_aliases\": " + kept + " }");
			assertEquals(kept, VfgConfig.load(file).incantationSpotterAliases);
		}
		for (String out : List.of("-1", "25", "6.5", "\"6\"", "false")) {
			Files.writeString(file, "{ \"turn_model\": \"off\", \"incantation_spotter_aliases\": " + out + " }");
			assertEquals(10, VfgConfig.load(file).incantationSpotterAliases, out + " falls back to the default, 10");
		}
		VfgConfig six = VfgConfig.load(file);
		six.incantationSpotterAliases = 6;
		assertEquals(6, six.copy().incantationSpotterAliases);
	}

	@Test
	void theCastAndListEndpointsAreOnTheSameBridge() {
		VfgConfig c = new VfgConfig().withEnv(Map.of());
		c.serverAddress = "play.example.com";
		assertEquals("http://play.example.com:8787/v1/casts", c.castsUrl());
		assertEquals("http://play.example.com:8787/v1/incantations", c.incantationsUrl());
		c.bridgeUrl = "http://10.0.0.5:9000/";
		assertEquals("http://10.0.0.5:9000/v1/casts", c.castsUrl());
		assertEquals("http://10.0.0.5:9000/v1/incantations", c.incantationsUrl());
		c.bridgeUrl = "";
		c.serverAddress = "";
		assertNull(c.castsUrl());
	}

	@Test
	void environmentOverridesAreUsedButNeverWrittenBack() throws Exception {
		Path file = dir.resolve("vfg-client.json");
		VfgConfig c = VfgConfig.load(file).withEnv(Map.of("VFG_SERVER_ADDRESS", "localhost",
			"VFG_BRIDGE_URL", "http://127.0.0.1:8787", "VFG_CLIENT_TOKEN", "test"));
		assertEquals("localhost", c.effectiveServerAddress());
		assertEquals("http://127.0.0.1:8787/v1/lines", c.linesUrl());
		assertEquals("test", c.effectiveClientToken());
		assertTrue(c.hasToken());
		assertTrue(c.hasEnvOverrides());
		c.save(file);
		String saved = Files.readString(file);
		assertFalse(saved.contains("localhost"));
		assertFalse(saved.contains("8787"));
		assertFalse(saved.contains("\"test\""));
	}

	@Test
	void theTokenIsNeverInToString() {
		VfgConfig c = new VfgConfig();
		c.clientToken = "s3cret-token-value";
		assertFalse(c.toString().contains("s3cret"));
	}

	@Test
	void anEmptyBridgeUrlMeansTheServerHostOnPort8787() {
		assertEquals("http://play.example.com:8787/v1/lines", VfgConfig.linesUrl("", "Play.Example.com:25565"));
		assertEquals("http://[::1]:8787/v1/lines", VfgConfig.linesUrl("", "[::1]"));
		assertEquals("http://10.0.0.5:9000/v1/lines", VfgConfig.linesUrl("http://10.0.0.5:9000/", "ignored"));
		assertNull(VfgConfig.linesUrl("", ""));
	}

	@Test
	void doneAcceptsOnlyHttpAndHttpsBridgeUrls() {
		assertTrue(VfgConfig.isValidBridgeUrl(""));
		assertTrue(VfgConfig.isValidBridgeUrl("http://127.0.0.1:8787"));
		assertTrue(VfgConfig.isValidBridgeUrl("https://bridge.example.com"));
		assertFalse(VfgConfig.isValidBridgeUrl("ftp://bridge.example.com"));
		assertFalse(VfgConfig.isValidBridgeUrl("127.0.0.1:8787"));
		assertFalse(VfgConfig.isValidBridgeUrl("http://"));
		assertFalse(VfgConfig.isValidBridgeUrl("http://bad host"));
	}
}
