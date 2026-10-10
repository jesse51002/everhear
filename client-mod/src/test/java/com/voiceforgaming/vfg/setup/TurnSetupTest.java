package com.voiceforgaming.vfg.setup;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.voiceforgaming.vfg.session.StatusBoard;
import com.voiceforgaming.vfg.session.VoiceStatus;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;

/** The turn model's install, retries, load and swap, with a stand-in installer, sleeper and models. */
class TurnSetupTest {
	private final StatusBoard status = new StatusBoard();
	private final EngineSlot<Model, Model> slot = new EngineSlot<>();
	private final Model engine = new Model();
	/** Each wait asked for, and the turn model's state while waiting. */
	private final List<Long> waits = new ArrayList<>();
	private final List<VoiceStatus.TurnModel> statesWhileWaiting = new ArrayList<>();
	private int installs;
	private int loads;

	static final class Model implements AutoCloseable {
		boolean closed;

		@Override
		public void close() {
			closed = true;
		}
	}

	TurnSetupTest() {
		slot.put(engine);
	}

	@AfterEach
	void clearInterrupt() {
		Thread.interrupted();
	}

	private TurnSetup setup() {
		return new TurnSetup(status, millis -> {
			waits.add(millis);
			statesWhileWaiting.add(status.get().turnModel());
		});
	}

	/** Fails {@code failures} times, as a download the installer gave up on, then installs. */
	private TurnSetup.Install failing(int failures) {
		return () -> {
			installs++;
			if (installs <= failures) {
				throw new AssetInstaller.SetupException("could not download smart-turn-v3.2-int8.onnx (HTTP 503)");
			}
		};
	}

	private TurnSetup.Load<Model> loading(Model m) {
		return () -> {
			loads++;
			return m;
		};
	}

	@Test
	void anInstalledModelIsLoadedAndSwappedIn() {
		Model turn = new Model();
		assertTrue(setup().run("smart-turn-v3.2", failing(0), loading(turn), slot, engine));
		assertSame(turn, slot.turn());
		assertTrue(waits.isEmpty());
		assertEquals(VoiceStatus.TurnModel.READY, status.get().turnModel());
	}

	@Test
	void aFailedDownloadIsTriedAgainLaterAndLaterUntilItWorks() {
		Model turn = new Model();
		assertTrue(setup().run("smart-turn-v3.2", failing(5), loading(turn), slot, engine));
		assertEquals(List.of(30_000L, 120_000L, 600_000L, 600_000L, 600_000L), waits);
		assertEquals(6, installs);
		assertEquals(1, loads);
		assertSame(turn, slot.turn(), "the try that works swaps the model in");
		assertEquals(List.of(VoiceStatus.TurnModel.RETRYING), statesWhileWaiting.stream().distinct().toList());
		VoiceStatus st = status.get();
		assertEquals(VoiceStatus.TurnModel.READY, st.turnModel());
		assertNull(st.turnError());
	}

	@Test
	void whileItWaitsTheStatusSaysWhy() {
		List<String> errors = new ArrayList<>();
		TurnSetup setup = new TurnSetup(status, millis -> {
			errors.add(status.get().turnError());
			throw new InterruptedException(); // stopped during the first wait
		});
		assertFalse(setup.run("smart-turn-v3.2", failing(1), loading(new Model()), slot, engine));
		assertEquals(List.of("could not download smart-turn-v3.2-int8.onnx (HTTP 503)"), errors);
	}

	@Test
	void anInterruptDuringAWaitEndsTheRetries() {
		int[] sleeps = {0};
		TurnSetup setup = new TurnSetup(status, millis -> {
			if (++sleeps[0] == 2) {
				throw new InterruptedException(); // a new setup, or the game stopping
			}
		});
		assertFalse(setup.run("smart-turn-v3.2", failing(10), loading(new Model()), slot, engine));
		assertEquals(2, installs);
		assertEquals(0, loads);
		assertNull(slot.turn());
	}

	@Test
	void aModelThatWillNotLoadIsNotTriedAgain() {
		TurnSetup.Load<Model> broken = () -> {
			loads++;
			throw new IllegalStateException("Invalid input name: audio");
		};
		assertFalse(setup().run("smart-turn-v3.2", failing(0), broken, slot, engine));
		assertEquals(1, installs);
		assertEquals(1, loads);
		assertTrue(waits.isEmpty());
		assertNull(slot.turn());
		VoiceStatus st = status.get();
		assertEquals(VoiceStatus.TurnModel.FAILED, st.turnModel());
		assertEquals("Invalid input name: audio", st.turnError());
	}

	@Test
	void aModelLoadedForAReplacedEngineIsClosed() {
		Model turn = new Model();
		TurnSetup.Load<Model> meanwhileReplaced = () -> {
			slot.release();
			slot.put(new Model());
			return turn;
		};
		assertFalse(setup().run("smart-turn-v3.2", failing(0), meanwhileReplaced, slot, engine));
		assertTrue(turn.closed);
		assertNull(slot.turn());
		assertEquals(VoiceStatus.TurnModel.LOADING, status.get().turnModel(), "not READY");
	}

	@Test
	void aModelLoadedByAnInterruptedSetupIsClosedAndReportsNothing() {
		Model turn = new Model();
		TurnSetup.Load<Model> interrupted = () -> {
			Thread.currentThread().interrupt();
			status.turnModel(VoiceStatus.TurnModel.OFF, null); // what the setup after it says
			return turn;
		};
		assertFalse(setup().run("smart-turn-v3.2", failing(0), interrupted, slot, engine));
		assertTrue(turn.closed);
		assertNull(slot.turn());
		assertEquals(VoiceStatus.TurnModel.OFF, status.get().turnModel());
	}
}
