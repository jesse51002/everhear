package com.voiceforgaming.vfg.setup;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Which speech model setup loads, given the one chosen. Parakeet TDT 0.6B v2, the default, is a
 * 482 MB download and about 0.9 GB of memory; Parakeet TDT 110M (108 MB) is its fallback:
 *
 * <ul>
 * <li>Chosen and installed: the 0.6B loads; if it will not (an error, out of memory), the 110M
 *     loads in its place for this start, with a log line and a note in Voice settings.
 * <li>Chosen and not installed yet (a first start, or the first start of 0.3.5): the 110M is
 *     installed if it is not (108 MB) and loads at once, so voice works within the minute; the
 *     0.6B downloads in the background and is swapped in once loaded.
 * <li>Chosen, but the last two loads of it in a row began and never finished (the game closed or
 *     crashed mid-load, as when the system runs out of memory): the 110M is chosen in the config,
 *     with a log line and a note; picking the 0.6B again in Voice settings tries it again. One
 *     unfinished load alone is tried again, with a log line: the game may have died of something
 *     else while the 0.6B loaded in the background.
 * </ul>
 *
 * <p>Any other model (110M, Moonshine) loads as chosen.
 *
 * <p>The mark, {@code <game folder>/vfg/stt-loading}, is one line: the model and how many of its
 * loads in a row have begun without finishing, this one included ({@code parakeet-0.6b-v2-en 1}).
 * It is written as a load begins and deleted when any load finishes, well or not, and on a clean stop.
 */
public final class ModelChoice {
	private static final Logger LOG = LoggerFactory.getLogger("vfg");
	/** The mark of loads begun and never finished; still there at the next start, its last load never finished. */
	public static final String LOADING_MARK = "stt-loading";
	/** How many unfinished loads in a row choose the 110M. */
	public static final int UNFINISHED_TO_FALL_BACK = 2;

	/**
	 * What to load now, what to swap in once it is in ({@code null}: nothing), whether the 110M is
	 * chosen because the 0.6B's loads kept not finishing, and how many of them in a row did not.
	 */
	public record Plan(String loadNow, String upgradeTo, boolean crashed, int unfinished) {
		public Plan(String loadNow, String upgradeTo, boolean crashed) {
			this(loadNow, upgradeTo, crashed, 0);
		}
	}

	private final Path vfgRoot;

	public ModelChoice(Path vfgRoot) {
		this.vfgRoot = vfgRoot;
	}

	/** The plan for {@code chosen}, from what is installed and the mark of unfinished loads. */
	public Plan plan(String chosen) {
		if (!chosen.equals(Assets.PARAKEET_06B)) {
			return new Plan(chosen, null, false);
		}
		int unfinished = unfinished(chosen);
		if (unfinished >= UNFINISHED_TO_FALL_BACK) {
			return new Plan(Assets.PARAKEET_110M, null, true, unfinished);
		}
		if (unfinished > 0) {
			LOG.warn("speech engine: {} began loading at the last start and never finished; trying it again (the "
				+ "110M is chosen if it does not finish again)", chosen);
		}
		Assets.Asset big = Assets.model(chosen);
		if (AssetInstaller.isInstalled(Assets.folder(vfgRoot, big), big.sha256())) {
			return new Plan(chosen, null, false, unfinished);
		}
		return new Plan(Assets.PARAKEET_110M, chosen, false, unfinished);
	}

	/** Whether {@code model} is the one that may not fit, the 0.6B: its loads are marked. */
	public static boolean marked(String model) {
		return Assets.PARAKEET_06B.equals(model);
	}

	/** How many loads of {@code model} in a row began and never finished; 0 without a mark, or with another model's. */
	public int unfinished(String model) {
		Path mark = vfgRoot.resolve(LOADING_MARK);
		try {
			if (!Files.isRegularFile(mark)) {
				return 0;
			}
			String[] parts = Files.readString(mark, StandardCharsets.UTF_8).trim().split("\\s+");
			if (parts.length == 0 || !parts[0].equals(model)) {
				return 0;
			}
			return parts.length > 1 ? Math.max(1, Integer.parseInt(parts[1])) : 1; // a mark from before the count: 1
		} catch (IOException | NumberFormatException e) {
			return 1;
		}
	}

	/** Marks a load of {@code model} as begun: one more unfinished in a row until {@link #end}. */
	public void begin(String model) {
		int count = unfinished(model) + 1;
		try {
			Files.createDirectories(vfgRoot);
			Files.writeString(vfgRoot.resolve(LOADING_MARK), model + " " + count + "\n", StandardCharsets.UTF_8);
		} catch (IOException e) {
			LOG.debug("could not mark the load of {}: {}", model, e.toString());
		}
	}

	/** A load finished, well or not, or the game is stopping cleanly: no mark, the count starts again. */
	public void end() {
		try {
			Files.deleteIfExists(vfgRoot.resolve(LOADING_MARK));
		} catch (IOException e) {
			LOG.debug("could not clear the load mark: {}", e.toString());
		}
	}
}
