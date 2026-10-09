package com.voiceforgaming.vfg.turn;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** Loading refuses clearly when there is nothing to load; the real models are in {@link TurnEngineTest}. */
class TurnDetectorsTest {
	@TempDir
	Path dir;

	@Test
	void onlyTheTwoModelsLoad() {
		assertThrows(IllegalArgumentException.class, () -> TurnDetectors.load(TurnDetectors.OFF, dir, dir.resolve("m.onnx")));
		assertThrows(IllegalArgumentException.class, () -> TurnDetectors.load("smart-turn-v4", dir, dir.resolve("m.onnx")));
		assertEquals(TurnDetectors.SMART_TURN, TurnDetectors.DEFAULT);
	}

	@Test
	void noLibraryInTheNativesFolderSaysWhere() {
		IllegalStateException e = assertThrows(IllegalStateException.class,
			() -> TurnDetectors.load(TurnDetectors.SMART_TURN, dir, dir.resolve("m.onnx")));
		Path lib = dir.toAbsolutePath().resolve(System.mapLibraryName("onnxruntime"));
		assertEquals(lib, OnnxRuntime.library(dir));
		assertTrue(e.getMessage().contains(lib.toString()), e.getMessage());
	}
}
