package com.voiceforgaming.vfg.setup;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;

/** The engine and the turn model beside it, with stand-ins that record when they are closed. */
class EngineSlotTest {
	/** Closed ones, in order. */
	private final List<String> closed = new ArrayList<>();
	private final EngineSlot<Fake, Fake> slot = new EngineSlot<>();

	final class Fake implements AutoCloseable {
		final String name;
		final boolean failOnClose;

		Fake(String name) {
			this(name, false);
		}

		Fake(String name, boolean failOnClose) {
			this.name = name;
			this.failOnClose = failOnClose;
		}

		boolean isClosed() {
			return closed.contains(name);
		}

		@Override
		public void close() throws Exception {
			closed.add(name);
			if (failOnClose) {
				throw new Exception(name + " would not close");
			}
		}
	}

	@AfterEach
	void clearInterrupt() {
		Thread.interrupted();
	}

	@Test
	void aTurnModelBesideTheEngineInTheSlotIsKept() {
		Fake engine = new Fake("engine");
		Fake turn = new Fake("turn");
		slot.put(engine);
		assertTrue(slot.offerTurn(turn, engine));
		assertSame(engine, slot.engine());
		assertSame(turn, slot.turn());
		assertTrue(closed.isEmpty());
	}

	@Test
	void aTurnModelBesideAReplacedEngineIsClosed() {
		Fake first = new Fake("first engine");
		slot.put(first);
		slot.release();
		Fake second = new Fake("second engine");
		slot.put(second);
		Fake turn = new Fake("turn");
		assertFalse(slot.offerTurn(turn, first));
		assertNull(slot.turn());
		assertTrue(turn.isClosed());
		assertFalse(second.isClosed());
	}

	@Test
	void aTurnModelBesideAReleasedEngineIsClosed() {
		Fake engine = new Fake("engine");
		slot.put(engine);
		slot.release(); // the game stopping
		Fake turn = new Fake("turn");
		assertFalse(slot.offerTurn(turn, engine));
		assertNull(slot.turn());
		assertNull(slot.engine());
		assertTrue(turn.isClosed());
	}

	@Test
	void anInterruptedOfferIsClosed() {
		Fake engine = new Fake("engine");
		slot.put(engine);
		Fake turn = new Fake("turn");
		Thread.currentThread().interrupt(); // its setup was replaced
		assertFalse(slot.offerTurn(turn, engine));
		assertTrue(Thread.currentThread().isInterrupted(), "the interrupt stays for the setup to see");
		assertNull(slot.turn());
		assertTrue(turn.isClosed());
		assertFalse(engine.isClosed());
	}

	@Test
	void releaseClosesTheTurnModelBeforeTheEngine() {
		Fake engine = new Fake("engine");
		Fake turn = new Fake("turn");
		slot.put(engine);
		slot.offerTurn(turn, engine);
		slot.release();
		assertEquals(List.of("turn", "engine"), closed);
		assertNull(slot.turn());
		assertNull(slot.engine());
		slot.release();
		assertEquals(2, closed.size(), "releasing an empty slot closes nothing");
	}

	@Test
	void aTurnModelThatWillNotCloseStillLetsTheEngineClose() {
		Fake engine = new Fake("engine");
		slot.put(engine);
		slot.offerTurn(new Fake("turn", true), engine);
		slot.release();
		assertEquals(List.of("turn", "engine"), closed);
	}

	@Test
	void anEngineGoesOnlyIntoAnEmptySlot() {
		slot.put(new Fake("engine"));
		assertThrows(IllegalStateException.class, () -> slot.put(new Fake("another")));
	}
}
