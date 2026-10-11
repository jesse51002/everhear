package com.voiceforgaming.vfg.net;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class CastPayloadTest {
	private static final UUID PLAYER = UUID.fromString("069a79f4-44e9-4726-a5be-fca90e38aaf5");
	private static final long START = Instant.parse("2026-10-10T18:22:29.100Z").toEpochMilli();
	private static final long END = Instant.parse("2026-10-10T18:22:29.7Z").toEpochMilli();

	@Test
	void theKeysOfTheIncantationInvokeInOrder() {
		JsonObject o = JsonParser.parseString(
			CastPayload.create(PLAYER, "Steve", "fire ball", "fireball", START, END).toJson()).getAsJsonObject();
		assertEquals(List.of("client_cast_id", "player_uuid", "player_name", "phrase", "heard", "started_at",
			"ended_at"), List.copyOf(o.keySet()));
		assertEquals("069a79f4-44e9-4726-a5be-fca90e38aaf5", o.get("player_uuid").getAsString());
		assertEquals("Steve", o.get("player_name").getAsString());
		assertEquals("fire ball", o.get("phrase").getAsString());
		assertEquals("fireball", o.get("heard").getAsString());
		assertEquals("2026-10-10T18:22:29.100Z", o.get("started_at").getAsString());
		assertEquals("2026-10-10T18:22:29.700Z", o.get("ended_at").getAsString());
	}

	@Test
	void heardIsLeftOutWhenThereIsNone() {
		JsonObject o = JsonParser.parseString(
			CastPayload.create(PLAYER, "Steve", "fire ball", null, START, END).toJson()).getAsJsonObject();
		assertFalse(o.has("heard"));
	}

	@Test
	void eachHitHasANewVersion4ClientCastId() {
		String a = CastPayload.create(PLAYER, "Steve", "fire ball", null, START, END).clientCastId();
		String b = CastPayload.create(PLAYER, "Steve", "fire ball", null, START, END).clientCastId();
		assertNotEquals(a, b);
		assertEquals(4, UUID.fromString(a).version());
		assertEquals(a, UUID.fromString(a).toString());
	}
}
