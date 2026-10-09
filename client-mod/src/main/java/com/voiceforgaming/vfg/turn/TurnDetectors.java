package com.voiceforgaming.vfg.turn;

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
	 * sherpa-onnx loads from {@code nativesDir}.
	 */
	public static TurnDetector load(String turnModel, Path nativesDir, Path modelFile) {
		throw new UnsupportedOperationException("turn models are not built yet");
	}
}
