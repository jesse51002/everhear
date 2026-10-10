package com.voiceforgaming.vfg.setup;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * The speech engine in use and the turn model loaded beside it, on its ONNX Runtime. The
 * capture and STT threads read both without waiting; setup puts them in, and setup or the
 * game stopping releases them. Generic so the tests can use stand-ins for both.
 *
 * <p>A turn model is kept only beside the engine it was loaded for: one offered by a setup
 * that was replaced or stopped meanwhile is closed instead, so none is left behind. Release
 * closes the turn model before the engine whose runtime it runs on.
 */
public final class EngineSlot<E extends AutoCloseable, T extends AutoCloseable> {
	private static final Logger LOG = LoggerFactory.getLogger("vfg");

	private final Object lock = new Object();
	private volatile E engine;
	private volatile T turn;

	/** The engine, or {@code null} while there is none. */
	public E engine() {
		return engine;
	}

	/** The turn model, or {@code null} while lines end on silence alone. */
	public T turn() {
		return turn;
	}

	/** Puts a newly loaded engine into the slot, which {@link #release} has emptied. */
	public void put(E loaded) {
		synchronized (lock) {
			if (engine != null) {
				throw new IllegalStateException("an engine is already in the slot");
			}
			engine = loaded;
		}
	}

	/**
	 * Keeps {@code detector} if {@code beside} is still the engine here and this thread was not
	 * interrupted (its setup replaced or stopped); otherwise closes it. Returns whether it was kept.
	 */
	public boolean offerTurn(T detector, E beside) {
		synchronized (lock) {
			if (!Thread.currentThread().isInterrupted() && beside != null && engine == beside) {
				T old = turn;
				turn = detector;
				close(old, "the turn model");
				return true;
			}
		}
		close(detector, "the turn model");
		return false;
	}

	/** Releases the turn model, then the engine whose runtime it runs on, and empties the slot. */
	public void release() {
		synchronized (lock) {
			T t = turn;
			turn = null;
			close(t, "the turn model");
			E e = engine;
			engine = null;
			close(e, "the speech engine");
		}
	}

	private static void close(AutoCloseable c, String what) {
		if (c == null) {
			return;
		}
		try {
			c.close();
		} catch (Exception e) {
			LOG.warn("closing {}: {}", what, e.toString());
		}
	}
}
