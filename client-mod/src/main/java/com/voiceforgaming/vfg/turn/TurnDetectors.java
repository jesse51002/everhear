package com.voiceforgaming.vfg.turn;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

/** The config's {@code turn_model} values, and loading the detector for one. */
public final class TurnDetectors {
	/** Smart Turn v3.2 (Pipecat, BSD-2-Clause): the default. */
	public static final String SMART_TURN = "smart-turn-v3.2";
	/** turn-1-mini (p99lab, Apache-2.0): measured against it. */
	public static final String TURN_1_MINI = "turn-1-mini";
	/** No model: lines end on silence alone. */
	public static final String OFF = "off";
	/** Every value, the default first. */
	public static final List<String> MODELS = List.of(SMART_TURN, TURN_1_MINI, OFF);
	public static final String DEFAULT = SMART_TURN;

	private TurnDetectors() {}

	/**
	 * Loads {@code turnModel}'s detector from {@code modelFile}, on the ONNX Runtime library
	 * sherpa-onnx loads from {@code nativesDir}. {@code off} has no detector. Throws
	 * {@link OnnxRuntime.OrtException} when ONNX Runtime refuses the model, and
	 * {@link IllegalStateException} when the library is not there.
	 */
	public static TurnDetector load(String turnModel, Path nativesDir, Path modelFile) {
		if (!turnModel.equals(SMART_TURN) && !turnModel.equals(TURN_1_MINI)) {
			throw new IllegalArgumentException("no turn model called " + turnModel);
		}
		OnnxRuntime runtime = OnnxRuntime.open(nativesDir);
		byte[] model;
		try {
			model = Files.readAllBytes(modelFile);
		} catch (IOException e) {
			throw new UncheckedIOException("could not read " + modelFile, e);
		}
		OnnxRuntime.Session session = runtime.session(model);
		return turnModel.equals(SMART_TURN) ? new SmartTurn(session) : new TurnOneMini(session);
	}
}
