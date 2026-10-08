package com.voiceforgaming.vfg.config;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.nio.file.Files;
import java.nio.file.Path;
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
			"silence_seconds", "vad_threshold", "stt_threads", "log_text"), List.copyOf(o.keySet()));
		assertEquals("parakeet-110m-en", o.get("model").getAsString());
		assertEquals("top_left", o.get("icon_corner").getAsString());
		assertEquals(5, o.get("silence_seconds").getAsInt());
		assertEquals("5", o.get("silence_seconds").toString());
		assertEquals(0.5, o.get("vad_threshold").getAsDouble());
		assertEquals(2, o.get("stt_threads").getAsInt());
		assertFalse(o.get("muted").getAsBoolean());
		assertFalse(o.get("log_text").getAsBoolean());
		assertEquals(5.0, c.silenceSeconds);
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
		assertEquals(5.0, c.silenceSeconds);
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
