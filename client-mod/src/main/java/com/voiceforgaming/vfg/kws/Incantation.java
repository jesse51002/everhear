package com.voiceforgaming.vfg.kws;

import java.util.List;

/**
 * One of the player's incantations as the bridge lists them ({@code GET /v1/incantations}): its
 * phrase, its aliases, and its cooldown, which the bridge applies; the mod listens for the phrase
 * and every alias.
 */
public record Incantation(String phrase, List<String> aliases, double cooldownSeconds) {
	public Incantation {
		aliases = List.copyOf(aliases);
	}
}
