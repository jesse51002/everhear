package com.voiceforgaming.vfg.net;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonObject;
import java.util.UUID;

/**
 * The body of {@code POST /v1/casts}, the incantation invoke: "Steve said fire ball", with when the
 * phrase started and ended on the mod's audio clock (the clock a line's {@code started_at} uses).
 * {@code phrase} is the incantation's own phrase; {@code heard} the keyword the spotter matched,
 * the phrase or one of its aliases. Timestamps are ISO-8601 UTC with milliseconds, as a line's.
 */
public record CastPayload(
	String clientCastId,
	String playerUuid,
	String playerName,
	String phrase,
	String heard,
	long startedAtMillis,
	long endedAtMillis
) {
	private static final Gson GSON = new GsonBuilder().disableHtmlEscaping().create();

	/** A payload with a fresh version-4 {@code client_cast_id}: one per hit. */
	public static CastPayload create(UUID player, String playerName, String phrase, String heard, long startedAtMillis,
			long endedAtMillis) {
		return new CastPayload(UUID.randomUUID().toString(), player == null ? "" : player.toString(), playerName, phrase,
			heard, startedAtMillis, endedAtMillis);
	}

	public JsonObject toJsonObject() {
		JsonObject o = new JsonObject();
		o.addProperty("client_cast_id", clientCastId);
		o.addProperty("player_uuid", playerUuid);
		o.addProperty("player_name", playerName);
		o.addProperty("phrase", phrase);
		if (heard != null) {
			o.addProperty("heard", heard);
		}
		o.addProperty("started_at", LinePayload.timestamp(startedAtMillis));
		o.addProperty("ended_at", LinePayload.timestamp(endedAtMillis));
		return o;
	}

	public String toJson() {
		return GSON.toJson(toJsonObject());
	}
}
