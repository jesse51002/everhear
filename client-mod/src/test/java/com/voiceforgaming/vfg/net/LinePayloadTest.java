package com.voiceforgaming.vfg.net;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class LinePayloadTest {
	private static final UUID PLAYER = UUID.fromString("069a79f4-44e9-4726-a5be-fca90e38aaf5");

	private static LinePayload sample() {
		long start = Instant.parse("2026-10-07T18:22:29.100Z").toEpochMilli();
		long end = Instant.parse("2026-10-07T18:22:31Z").toEpochMilli();
		return LinePayload.create(PLAYER, "Steve", "build me a castle", start, end, -8.2449, -23.4567);
	}

	@Test
	void exactlyTheEightKeysOfSection2() {
		JsonObject o = JsonParser.parseString(sample().toJson()).getAsJsonObject();
		List<String> keys = new ArrayList<>(o.keySet());
		assertEquals(List.of("client_line_id", "player_uuid", "player_name", "text", "started_at", "ended_at",
			"peak_dbfs", "mean_dbfs"), keys);
		assertEquals("069a79f4-44e9-4726-a5be-fca90e38aaf5", o.get("player_uuid").getAsString());
		assertEquals("Steve", o.get("player_name").getAsString());
		assertEquals("build me a castle", o.get("text").getAsString());
	}

	@Test
	void timestampsAreUtcWithMillisecondsAndAWholeSecondIsDotZeroZeroZero() {
		JsonObject o = JsonParser.parseString(sample().toJson()).getAsJsonObject();
		assertEquals("2026-10-07T18:22:29.100Z", o.get("started_at").getAsString());
		assertEquals("2026-10-07T18:22:31.000Z", o.get("ended_at").getAsString());
		assertTrue(sample().toJson().contains("\"ended_at\":\"2026-10-07T18:22:31.000Z\""));
	}

	@Test
	void dbfsHasOneDecimal() {
		String json = sample().toJson();
		assertTrue(json.contains("\"peak_dbfs\":-8.2"), json);
		assertTrue(json.contains("\"mean_dbfs\":-23.5"), json);
		LinePayload zero = LinePayload.create(PLAYER, "Steve", "hi", 0, 0, -0.04, -100.0);
		String z = zero.toJson();
		assertTrue(z.contains("\"peak_dbfs\":0.0"), z);
		assertTrue(z.contains("\"mean_dbfs\":-100.0"), z);
	}

	@Test
	void clientLineIdIsAVersion4Uuid() {
		String id = sample().clientLineId();
		UUID uuid = UUID.fromString(id);
		assertEquals(4, uuid.version());
		assertEquals(2, uuid.variant());
		assertEquals(id, uuid.toString());
	}

	@Test
	void textIsNotHtmlEscaped() {
		String json = LinePayload.create(PLAYER, "Steve", "don't <go> & stop", 0, 0, -1, -2).toJson();
		assertTrue(json.contains("don't <go> & stop"), json);
	}
}
