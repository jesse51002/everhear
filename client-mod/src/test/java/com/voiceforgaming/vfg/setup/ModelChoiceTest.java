package com.voiceforgaming.vfg.setup;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class ModelChoiceTest {
	@TempDir
	Path root;

	private void install(String model) throws Exception {
		Assets.Asset a = Assets.model(model);
		Path folder = Assets.folder(root, a);
		Files.createDirectories(folder);
		Files.writeString(folder.resolve(AssetInstaller.OK_FILE), a.sha256() + "\n");
	}

	@Test
	void theBigModelInstalledLoadsAsChosen() throws Exception {
		install(Assets.PARAKEET_06B);
		assertEquals(new ModelChoice.Plan(Assets.PARAKEET_06B, null, false), new ModelChoice(root).plan(Assets.PARAKEET_06B));
	}

	@Test
	void theBigModelNotYetInstalledHas110MListenWhileItDownloads() throws Exception {
		ModelChoice choice = new ModelChoice(root);
		assertEquals(new ModelChoice.Plan(Assets.PARAKEET_110M, Assets.PARAKEET_06B, false), choice.plan(Assets.PARAKEET_06B),
			"a first start: 110M (108 MB) first, the 0.6B in the background");
		install(Assets.PARAKEET_110M);
		assertEquals(new ModelChoice.Plan(Assets.PARAKEET_110M, Assets.PARAKEET_06B, false), choice.plan(Assets.PARAKEET_06B),
			"0.3.4's players: their 110M listens at once");
	}

	@Test
	void anyOtherModelLoadsAsChosen() {
		ModelChoice choice = new ModelChoice(root);
		for (String m : new String[] {Assets.PARAKEET_110M, Assets.MOONSHINE_BASE, Assets.MOONSHINE_TINY}) {
			assertEquals(new ModelChoice.Plan(m, null, false), choice.plan(m));
		}
	}

	@Test
	void oneUnfinishedLoadIsTriedAgainAndTwoInARowChoose110M() throws Exception {
		install(Assets.PARAKEET_06B);
		ModelChoice choice = new ModelChoice(root);
		assertTrue(ModelChoice.marked(Assets.PARAKEET_06B));
		assertFalse(ModelChoice.marked(Assets.PARAKEET_110M), "only the big model's loads are marked");
		choice.begin(Assets.PARAKEET_06B);
		assertEquals("parakeet-0.6b-v2-en 1", Files.readString(root.resolve(ModelChoice.LOADING_MARK)).trim());
		// the game dies here (the 0.6B, or anything else); at the next start it is tried again:
		ModelChoice next = new ModelChoice(root);
		assertEquals(1, next.unfinished(Assets.PARAKEET_06B));
		assertEquals(new ModelChoice.Plan(Assets.PARAKEET_06B, null, false, 1), next.plan(Assets.PARAKEET_06B));
		next.begin(Assets.PARAKEET_06B);
		assertEquals("parakeet-0.6b-v2-en 2", Files.readString(root.resolve(ModelChoice.LOADING_MARK)).trim());
		// and dies again: now the 110M is chosen
		ModelChoice third = new ModelChoice(root);
		assertEquals(new ModelChoice.Plan(Assets.PARAKEET_110M, null, true, 2), third.plan(Assets.PARAKEET_06B));
		assertEquals(new ModelChoice.Plan(Assets.MOONSHINE_BASE, null, false), third.plan(Assets.MOONSHINE_BASE),
			"the mark is the 0.6B's alone");
		third.end();
		assertFalse(Files.exists(root.resolve(ModelChoice.LOADING_MARK)));
		assertEquals(new ModelChoice.Plan(Assets.PARAKEET_06B, null, false), third.plan(Assets.PARAKEET_06B),
			"picked again, it is tried again");
	}

	@Test
	void aFinishedLoadOrACleanStopBetweenResetsTheCount() throws Exception {
		install(Assets.PARAKEET_06B);
		ModelChoice choice = new ModelChoice(root);
		choice.begin(Assets.PARAKEET_06B); // unfinished
		choice = new ModelChoice(root);
		choice.begin(Assets.PARAKEET_06B);
		choice.end(); // this one finished
		choice.begin(Assets.PARAKEET_06B); // unfinished again: the first in a row
		assertEquals(new ModelChoice.Plan(Assets.PARAKEET_06B, null, false, 1), new ModelChoice(root).plan(Assets.PARAKEET_06B));
	}

	@Test
	void anUnfinishedBackgroundLoadIsTriedAgainOnTheUpgradePath() throws Exception {
		ModelChoice choice = new ModelChoice(root); // the 0.6B not installed: the 110M listens while it downloads
		choice.begin(Assets.PARAKEET_06B);
		assertEquals(new ModelChoice.Plan(Assets.PARAKEET_110M, Assets.PARAKEET_06B, false, 1),
			new ModelChoice(root).plan(Assets.PARAKEET_06B));
	}

	@Test
	void aMarkWithoutACountOrUnreadableCountsAsOne() throws Exception {
		Files.createDirectories(root);
		Files.writeString(root.resolve(ModelChoice.LOADING_MARK), "parakeet-0.6b-v2-en\n");
		assertEquals(1, new ModelChoice(root).unfinished(Assets.PARAKEET_06B));
		Files.writeString(root.resolve(ModelChoice.LOADING_MARK), "parakeet-0.6b-v2-en lots\n");
		assertEquals(1, new ModelChoice(root).unfinished(Assets.PARAKEET_06B));
		Files.writeString(root.resolve(ModelChoice.LOADING_MARK), "moonshine-base-en 5\n");
		assertEquals(0, new ModelChoice(root).unfinished(Assets.PARAKEET_06B), "another model's mark");
	}

	@Test
	void aFinishedLoadLeavesNoMark() throws Exception {
		install(Assets.PARAKEET_06B);
		ModelChoice choice = new ModelChoice(root);
		choice.begin(Assets.PARAKEET_06B);
		choice.end();
		choice.end(); // twice is fine, as on a clean stop after a load
		assertEquals(0, choice.unfinished(Assets.PARAKEET_06B));
		assertEquals(Assets.PARAKEET_06B, choice.plan(Assets.PARAKEET_06B).loadNow());
	}

	@Test
	void the06BIsPinnedBesideThe110M() {
		Assets.Asset big = Assets.model(Assets.PARAKEET_06B);
		assertEquals(482468385L, big.bytes());
		assertEquals("157c157bc51155e03e37d2466522a3a737dd9c72bb25f36eb18912964161e1ad", big.sha256());
		assertEquals("asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8.tar.bz2", big.path());
		assertTrue(Assets.isParakeet(big));
		assertEquals(Assets.PARAKEET_FILES, Assets.modelFiles(big), "the same four files as the 110M's");
		assertEquals(Path.of("r/models/parakeet-tdt-0.6b-v2-int8"), Assets.folder(Path.of("r"), big));
		assertEquals(Path.of("r/models/parakeet-tdt-110m-en-36000-int8"),
			Assets.folder(Path.of("r"), Assets.model(Assets.PARAKEET_110M)));
		assertEquals(big, Assets.model("anything else"), "an unknown value is the default");
	}
}
