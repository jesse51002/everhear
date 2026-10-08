package com.voiceforgaming.vfg.session;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

class ServerAddressMatchTest {
	@Test
	void caseAndDefaultPortDoNotMatter() {
		assertTrue(ServerAddressMatch.matches("Play.Example.com", "play.example.com:25565"));
	}

	@Test
	void aTrailingDotIsDropped() {
		assertTrue(ServerAddressMatch.matches("play.example.com.", "play.example.com"));
		assertTrue(ServerAddressMatch.matches("play.example.com.:25565", "play.example.com"));
	}

	@Test
	void aDifferentPortDoesNotMatch() {
		assertFalse(ServerAddressMatch.matches("1.2.3.4:25566", "1.2.3.4"));
	}

	@Test
	void ipv6InBracketsGetsTheDefaultPort() {
		assertTrue(ServerAddressMatch.matches("[::1]", "[::1]:25565"));
		assertFalse(ServerAddressMatch.matches("[::1]", "[::1]:25566"));
	}

	@Test
	void blankMatchesNothing() {
		assertFalse(ServerAddressMatch.matches("", ""));
		assertFalse(ServerAddressMatch.matches("   ", "play.example.com"));
		assertFalse(ServerAddressMatch.matches(null, null));
		assertFalse(ServerAddressMatch.matches("play.example.com", null));
	}

	@Test
	void whitespaceIsTrimmed() {
		assertTrue(ServerAddressMatch.matches("  localhost ", "localhost:25565"));
	}

	@Test
	void normalForm() {
		assertEquals("play.example.com:25565", ServerAddressMatch.normalize(" Play.Example.COM. "));
		assertEquals("[::1]:25565", ServerAddressMatch.normalize("[::1]"));
		assertEquals("[::1]:25565", ServerAddressMatch.normalize("::1"));
		assertNull(ServerAddressMatch.normalize("host:notaport"));
	}

	@Test
	void hostForTheBridgeUrl() {
		assertEquals("play.example.com", ServerAddressMatch.host("Play.Example.com:25566"));
		assertEquals("[::1]", ServerAddressMatch.host("[::1]:25565"));
		assertEquals("1.2.3.4", ServerAddressMatch.host("1.2.3.4"));
		assertNull(ServerAddressMatch.host(""));
	}
}
