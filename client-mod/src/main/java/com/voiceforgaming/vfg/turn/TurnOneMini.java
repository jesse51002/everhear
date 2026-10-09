package com.voiceforgaming.vfg.turn;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * turn-1-mini (p99lab, Apache-2.0), its {@code step.int8dyn} graph: one 160 ms step of
 * 2,560 samples in {@code audio} [1, 2560], with seven state tensors fed back from one
 * step's {@code new_state_*} to the next one's {@code state_*}; out, {@code probs} [8],
 * one per 20 ms frame, of which the last is the answer. The 4 kHz low-pass the model needs
 * is inside the graph.
 *
 * <p>A score replays the clip's last {@link #MAX_STEPS} steps (2.56 s) from zeroed state,
 * zeros in front of the clip to make whole steps, so it depends on the clip alone and
 * not on what was scored before. The attention reaches back 1.2 s per layer.
 */
public final class TurnOneMini implements TurnDetector {
	/** Its scores are not calibrated; this stands until a measurement on our own audio picks one. */
	public static final double THRESHOLD = 0.5;
	/** 160 ms at 16 kHz. */
	public static final int STEP = 2560;
	public static final int MAX_STEPS = 16;
	private static final int FRAMES = 8;

	/** The state tensors by name, after {@code state_} and {@code new_state_}, and their shapes. */
	private static final String[] STATES = {"audio", "conv1", "conv2", "k", "v", "k_valid", "h6"};
	private static final long[][] STATE_SHAPES = {{1, 304}, {1, 80, 2}, {1, 256, 2}, {6, 4, 60, 64}, {6, 4, 60, 64},
		{60}, {60, 256}};

	private final Object lock = new Object();
	private final float[] chunk = new float[STEP];
	private final OnnxRuntime.Tensor audio;
	private final OnnxRuntime.Tensor probs;
	/** Two sets of state: a step reads one and writes the other, and the next step the other way round. */
	private final OnnxRuntime.Tensor[][] state = new OnnxRuntime.Tensor[2][STATES.length];
	private final List<Map<String, OnnxRuntime.Tensor>> inputs = new ArrayList<>(2);
	private final List<Map<String, OnnxRuntime.Tensor>> outputs = new ArrayList<>(2);
	private OnnxRuntime.Session session;

	/** Takes over {@code session}, and runs it once on silence so a wrong model fails here and not at the first pause. */
	TurnOneMini(OnnxRuntime.Session session) {
		this.session = session;
		try {
			audio = session.tensor(1, STEP);
			probs = session.tensor(FRAMES);
			for (int i = 0; i < STATES.length; i++) {
				state[0][i] = session.tensor(STATE_SHAPES[i]);
				state[1][i] = session.tensor(STATE_SHAPES[i]);
			}
			for (int p = 0; p < 2; p++) {
				Map<String, OnnxRuntime.Tensor> in = new LinkedHashMap<>();
				Map<String, OnnxRuntime.Tensor> out = new LinkedHashMap<>();
				in.put("audio", audio);
				out.put("probs", probs);
				for (int i = 0; i < STATES.length; i++) {
					in.put("state_" + STATES[i], state[p][i]);
					out.put("new_state_" + STATES[i], state[1 - p][i]);
				}
				inputs.add(in);
				outputs.add(out);
			}
			score(new float[0]);
		} catch (RuntimeException | Error e) {
			session.close();
			throw e;
		}
	}

	@Override
	public String name() {
		return TurnDetectors.TURN_1_MINI;
	}

	@Override
	public double defaultThreshold() {
		return THRESHOLD;
	}

	@Override
	public float score(float[] clip) {
		synchronized (lock) {
			if (session == null) {
				throw new IllegalStateException("turn model released");
			}
			int n = Math.min(clip.length, MAX_STEPS * STEP);
			int steps = Math.max(1, (n + STEP - 1) / STEP);
			int pad = steps * STEP - n;
			for (OnnxRuntime.Tensor t : state[0]) {
				t.zero();
			}
			// The padded clip is pad zeros, then clip[from..]; step s is its samples [s * STEP, (s + 1) * STEP).
			int from = clip.length - n;
			for (int s = 0; s < steps; s++) {
				int start = s * STEP - pad;
				int zeros = Math.clamp(-start, 0, STEP);
				Arrays.fill(chunk, 0, zeros, 0f);
				System.arraycopy(clip, from + start + zeros, chunk, zeros, STEP - zeros);
				audio.set(chunk);
				session.run(inputs.get(s & 1), outputs.get(s & 1));
			}
			return probs.get(FRAMES - 1);
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
