package com.voiceforgaming.vfg.net;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonObject;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.UUID;

/**
 * The body of {@code POST /v1/lines} (docs/CONTRACTS.md §2): exactly eight keys.
 * Timestamps are ISO-8601 UTC with milliseconds (§1); dBFS has one decimal.
 */
public record LinePayload(
	String clientLineId,
	String playerUuid,
	String playerName,
	String text,
	long startedAtMillis,
	long endedAtMillis,
	double peakDbfs,
	double meanDbfs
) {
	public static final DateTimeFormatter TIMESTAMP =
		DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'").withZone(ZoneOffset.UTC);

	private static final Gson GSON = new GsonBuilder().disableHtmlEscaping().create();

	/** A payload with a fresh version-4 {@code client_line_id}, made when the line closes. */
	public static LinePayload create(UUID player, String playerName, String text, long startedAtMillis,
			long endedAtMillis, double peakDbfs, double meanDbfs) {
		return new LinePayload(UUID.randomUUID().toString(), player == null ? "" : player.toString(),
			playerName, text, startedAtMillis, endedAtMillis, peakDbfs, meanDbfs);
	}

	public static String timestamp(long epochMillis) {
		return TIMESTAMP.format(Instant.ofEpochMilli(epochMillis));
	}

	public JsonObject toJsonObject() {
		JsonObject o = new JsonObject();
		o.addProperty("client_line_id", clientLineId);
		o.addProperty("player_uuid", playerUuid);
		o.addProperty("player_name", playerName);
		o.addProperty("text", text);
		o.addProperty("started_at", timestamp(startedAtMillis));
		o.addProperty("ended_at", timestamp(endedAtMillis));
		o.addProperty("peak_dbfs", oneDecimal(peakDbfs));
		o.addProperty("mean_dbfs", oneDecimal(meanDbfs));
		return o;
	}

	public String toJson() {
		return GSON.toJson(toJsonObject());
	}

	private static double oneDecimal(double v) {
		double r = Math.round(v * 10.0) / 10.0;
		return r == 0.0 ? 0.0 : r;
	}
}
