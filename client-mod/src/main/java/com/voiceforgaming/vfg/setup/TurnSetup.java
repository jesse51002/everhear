package com.voiceforgaming.vfg.setup;

import com.voiceforgaming.vfg.session.StatusBoard;
import com.voiceforgaming.vfg.session.VoiceStatus;
import java.util.Locale;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Installs and loads the turn model on the setup thread once the speech engine is in, and
 * offers it to the {@link EngineSlot}; voice never waits for it, and until it is in lines end
 * on silence alone. The status board says which it is, for Voice settings.
 *
 * <p>An install that fails (Hugging Face unreachable, a download stalled: the installer's own
 * three tries spent) is tried again after {@link #RETRY_WAITS_MILLIS}, then every 10 min, for
 * as long as this setup lives; the interrupt that replaces or stops a setup ends the wait at
 * once. A model that installs but will not load is not tried again: it would fail the same way.
 */
public final class TurnSetup {
	private static final Logger LOG = LoggerFactory.getLogger("vfg");
	/** The waits before each try after the first; the last repeats. */
	public static final long[] RETRY_WAITS_MILLIS = {30_000, 120_000, 600_000};

	/** Installs the model's file; {@link AssetInstaller#install} in the mod. */
	@FunctionalInterface
	public interface Install {
		void install() throws AssetInstaller.SetupException, InterruptedException;
	}

	/** Loads the installed model; {@code TurnDetectors.load} in the mod. */
	@FunctionalInterface
	public interface Load<T> {
		T load() throws Exception;
	}

	private final StatusBoard status;
	private final AssetInstaller.Sleeper sleeper;

	public TurnSetup(StatusBoard status, AssetInstaller.Sleeper sleeper) {
		this.status = status;
		this.sleeper = sleeper;
	}

	/**
	 * Installs {@code name} (trying again while it fails), loads it, and offers it to
	 * {@code slot} beside {@code beside}. Returns whether it is in; {@code false} when it
	 * would not load, or this thread was interrupted.
	 */
	public <E extends AutoCloseable, T extends AutoCloseable> boolean run(String name, Install install, Load<T> load,
			EngineSlot<E, T> slot, E beside) {
		report(VoiceStatus.TurnModel.LOADING, null);
		try {
			for (int failures = 0; ; failures++) {
				try {
					install.install();
					break;
				} catch (AssetInstaller.SetupException e) {
					long wait = RETRY_WAITS_MILLIS[Math.min(failures, RETRY_WAITS_MILLIS.length - 1)];
					LOG.warn("turn model {} not installed ({}); lines end on silence alone, trying again in {}", name,
						e.getMessage(), duration(wait));
					report(VoiceStatus.TurnModel.RETRYING, e.getMessage());
					sleeper.sleep(wait);
					report(VoiceStatus.TurnModel.LOADING, null);
				}
			}
		} catch (InterruptedException e) {
			return false;
		} catch (RuntimeException e) {
			LOG.warn("turn model {} not installed ({}); lines end on silence alone", name, e.toString());
			report(VoiceStatus.TurnModel.FAILED, e.toString());
			return false;
		}
		if (Thread.currentThread().isInterrupted()) {
			return false;
		}
		T loaded;
		try {
			long t0 = System.nanoTime();
			loaded = load.load();
			LOG.info("turn model loaded ({}, {} ms)", name, (System.nanoTime() - t0) / 1_000_000);
		} catch (Throwable t) {
			String message = t.getMessage() == null ? t.getClass().getSimpleName() : t.getMessage();
			LOG.warn("turn model {} failed to load ({}); lines end on silence alone", name, message);
			report(VoiceStatus.TurnModel.FAILED, message);
			return false;
		}
		if (!slot.offerTurn(loaded, beside)) {
			return false;
		}
		report(VoiceStatus.TurnModel.READY, null);
		return true;
	}

	/** A replaced or stopped setup leaves the status to the one after it. */
	private void report(VoiceStatus.TurnModel state, String error) {
		if (!Thread.currentThread().isInterrupted()) {
			status.turnModel(state, error);
		}
	}

	private static String duration(long millis) {
		return millis < 60_000 ? String.format(Locale.ROOT, "%d s", millis / 1000)
			: String.format(Locale.ROOT, "%d min", millis / 60_000);
	}
}
