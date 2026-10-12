package com.voiceforgaming.vfg.kws;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.List;
import java.util.Set;
import org.junit.jupiter.api.Test;

/** The keyword string sherpa-onnx's createStream takes, built from a player's incantations over the model's own pieces. */
class KeywordsTest {
	private static final Set<String> TOKENS = Set.of("▁FI", "RE", "▁BA", "LL", "B", "AL", "L", "▁BO", "W", "▁I", "CE",
		"▁WA", "▁", "▁LE", "VE");

	private static Keywords build(List<Incantation> list, double threshold, double boost) throws Exception {
		return Keywords.build(list, SentencePieceTest.model(), TOKENS, threshold, boost);
	}

	@Test
	void thePhraseAndEachAliasAreKeywordsWithBoostThresholdAndTag() throws Exception {
		Keywords k = build(List.of(new Incantation("fire ball", List.of("fireball", "fire bowl"), 3)), 0.25, 1.5);
		assertEquals("▁FI RE ▁BA LL :1.5 #0.25 @fire_ball/▁FI RE B AL L :1.5 #0.25 @fireball"
			+ "/▁FI RE ▁BO W L :1.5 #0.25 @fire_bowl", k.spec());
		assertEquals(3, k.size());
	}

	@Test
	void aTagMapsBackToThePhraseAndTheKeywordHeard() throws Exception {
		Keywords k = build(List.of(new Incantation("Fire Ball", List.of("fireball"), 3)), 0.25, 1.5);
		assertEquals(new Keywords.Target("fire ball", "fire ball"), k.target("fire_ball"));
		assertEquals(new Keywords.Target("fire ball", "fireball"), k.target("fireball"));
		assertNull(k.target("ice_wall"));
		assertNull(k.target(null));
	}

	@Test
	void theThresholdAndBoostComeFromTheSettings() throws Exception {
		Keywords k = build(List.of(new Incantation("ice wall", List.of(), 3)), 0.1, 2.0);
		assertEquals("▁I CE ▁WA LL :2 #0.1 @ice_wall", k.spec());
	}

	@Test
	void aKeywordWithATokenTheModelLacksIsLeftOutAndListed() throws Exception {
		Keywords k = build(List.of(new Incantation("fire ball", List.of("level 3"), 3)), 0.25, 1.5);
		assertEquals("▁FI RE ▁BA LL :1.5 #0.25 @fire_ball", k.spec());
		assertEquals(List.of("level 3"), k.skipped());
	}

	@Test
	void aKeywordTwoIncantationsShareGoesToTheFirst() throws Exception {
		Keywords k = build(List.of(new Incantation("fire ball", List.of("fireball"), 3),
			new Incantation("fireball", List.of("fire ball", "ice wall"), 3)), 0.25, 1.5);
		assertEquals(3, k.size());
		assertEquals("fire ball", k.target("fireball").phrase());
		assertEquals("fireball", k.target("ice_wall").phrase());
	}

	@Test
	void aOneWordPhraseIsAKeywordOfItsOwn() throws Exception {
		Keywords k = build(List.of(new Incantation("Fireball", List.of(), 3), new Incantation("ice", List.of(), 3)),
			0.25, 1.5);
		assertEquals("▁FI RE B AL L :1.5 #0.25 @fireball/▁I CE :1.5 #0.25 @ice", k.spec());
		assertEquals(new Keywords.Target("fireball", "fireball"), k.target("fireball"));
		assertEquals(new Keywords.Target("ice", "ice"), k.target("ice"));
	}

	@Test
	void aKeywordWhoseTokensStartAnotherIsMarkedAcrossIncantationsAndAliases() throws Exception {
		Keywords k = build(List.of(new Incantation("fire", List.of(), 3),
			new Incantation("fire ball", List.of("fireball"), 3), new Incantation("ice wall", List.of("ice"), 3)), 0.25,
			1.5);
		assertTrue(k.startsAnother("fire"), "\"fire\" starts \"fire ball\" and \"fireball\"");
		assertTrue(k.startsAnother("ice"), "an alias that starts its own phrase");
		assertFalse(k.startsAnother("fire_ball"));
		assertFalse(k.startsAnother("fireball"));
		assertFalse(k.startsAnother("ice_wall"));
		assertFalse(k.startsAnother(null));
		assertTrue(k.anyStartsAnother());
		assertEquals("▁FI RE ▁BA LL :1.5 #0.25 @fire_ball/▁FI RE B AL L :1.5 #0.25 @fireball/▁I CE ▁WA LL :1.5 #0.25"
			+ " @ice_wall", k.specStartingNone());
		Keywords plain = build(List.of(new Incantation("fire ball", List.of(), 3)), 0.25, 1.5);
		assertFalse(plain.anyStartsAnother());
		assertEquals(plain.spec(), plain.specStartingNone());
	}

	@Test
	void noIncantationsIsNoKeywords() throws Exception {
		Keywords k = build(List.of(), 0.25, 1.5);
		assertTrue(k.isEmpty());
		assertEquals("", k.spec());
		Keywords blank = build(List.of(new Incantation("?!", List.of(""), 3)), 0.25, 1.5);
		assertTrue(blank.isEmpty());
	}
}
