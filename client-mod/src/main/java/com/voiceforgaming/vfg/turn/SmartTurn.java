package com.voiceforgaming.vfg.turn;

import java.util.Arrays;
import java.util.Map;

/**
 * Smart Turn v3.2 (Pipecat, BSD-2-Clause), in soniqo's export with the Whisper front-end
 * inside the graph: the last 8 s of the line in, at the end of {@code audio} float32
 * [1, 128000] with zeros in front; {@code probability} float32 [1, 1] out. One run per score.
 */
public final class SmartTurn implements TurnDetector {
	/** Pipecat's own: finished above 0.5. */
	public static final double THRESHOLD = 0.5;
	/** 8 s at 16 kHz. */
	public static final int WINDOW = 8 * SAMPLE_RATE;

	private final Object lock = new Object();
	private final float[] window = new float[WINDOW];
	private final OnnxRuntime.Tensor input;
	private final OnnxRuntime.Tensor probability;
	private final OnnxRuntime.Binding binding;
	private OnnxRuntime.Session session;

	/** Takes over {@code session}, and runs it once on silence so a wrong model fails here and not at the first pause. */
	SmartTurn(OnnxRuntime.Session session) {
		this.session = session;
		try {
			input = session.tensor(1, WINDOW);
			probability = session.tensor(1, 1);
			binding = session.binding(Map.of("audio", input), Map.of("probability", probability));
			score(new float[0]);
		} catch (RuntimeException | Error e) {
			session.close();
			throw e;
		}
	}

	@Override
	public String name() {
		return TurnDetectors.SMART_TURN;
	}

	@Override
	public double defaultThreshold() {
		return THRESHOLD;
	}

	@Override
	public float score(float[] audio) {
		synchronized (lock) {
			if (session == null) {
				throw new IllegalStateException("turn model released");
			}
			int n = Math.min(audio.length, WINDOW);
			Arrays.fill(window, 0, WINDOW - n, 0f);
			System.arraycopy(audio, audio.length - n, window, WINDOW - n, n);
			input.set(window);
			session.run(binding);
			return probability.get(0);
		}
	}

	@Override
	public void close() {
		synchronized (lock) {
			if (session != null) {
				session.close();
				session = null;
			}
		}
	}
}
