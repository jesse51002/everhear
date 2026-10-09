package com.voiceforgaming.vfg.speech;

import com.k2fsa.sherpa.onnx.OfflineModelConfig;
import com.k2fsa.sherpa.onnx.OfflineMoonshineModelConfig;
import com.k2fsa.sherpa.onnx.OfflineRecognizer;
import com.k2fsa.sherpa.onnx.OfflineRecognizerConfig;
import com.k2fsa.sherpa.onnx.OfflineStream;
import com.k2fsa.sherpa.onnx.OfflineTransducerModelConfig;
import com.k2fsa.sherpa.onnx.SileroVadModelConfig;
import com.k2fsa.sherpa.onnx.SpeechSegment;
import com.k2fsa.sherpa.onnx.Vad;
import com.k2fsa.sherpa.onnx.VadModelConfig;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.function.Supplier;

/**
 * The VAD and the recognizer: the only code that touches sherpa-onnx.
 *
 * <p>The VAD is used by the capture thread and the recognizer by the STT thread; each
 * has its own lock, and {@link #close()} takes both, so the engine can be released while
 * the other threads are still running and they get an {@link IllegalStateException}
 * instead of a use-after-free.
 */
public final class SpeechEngine implements VoiceDetector, Transcriber, AutoCloseable {
	public static final int SAMPLE_RATE = 16000;
	public static final int WINDOW = 512;

	/**
	 * Moonshine (base and tiny, 2026-02-27, through sherpa-onnx 1.13.8) returns empty text
	 * for any clip of about 9.3 s or more. So the VAD ends a segment after 7 s of unbroken
	 * speech (it looks for the quietest point past that, so segments run to about 8 s),
	 * and anything longer than {@link #MAX_DECODE_SECONDS} is decoded in pieces. Parakeet has
	 * no such limit; the same segments keep lines the same whichever model is chosen.
	 */
	public static final float MAX_SPEECH_SECONDS = 7f;
	public static final double MAX_DECODE_SECONDS = 9.0;
	private static final double PIECE_SECONDS = 8.0;

	/**
	 * The silence that ends a segment: the VAD pops it once this much silence follows its last
	 * sample. Past {@link #MAX_SPEECH_SECONDS} it ends one at the first 0.1 s dip instead, and pops
	 * it 0.1 s after its last sample, while the player is still talking.
	 */
	public static final float MIN_SILENCE_SECONDS = 0.6f;

	private final Object vadLock = new Object();
	private final Object sttLock = new Object();
	private Vad vad;
	private OfflineRecognizer recognizer;

	private SpeechEngine(Vad vad, OfflineRecognizer recognizer) {
		this.vad = vad;
		this.recognizer = recognizer;
	}

	/**
	 * Loads the natives from {@code nativesDir} (onnxruntime, then the JNI library), the
	 * Moonshine model from {@code modelDir} and the Silero VAD from {@code vadFile}.
	 */
	public static SpeechEngine load(Path nativesDir, boolean parakeet, Path modelDir, Path vadFile, int sttThreads,
			float vadThreshold) {
		return load(nativesDir, () -> parakeet ? parakeet(modelDir, sttThreads) : moonshine(modelDir, sttThreads),
			vadFile, vadThreshold);
	}

	/**
	 * Parakeet TDT (a NeMo transducer, the default model), from a folder holding
	 * {@code encoder.int8.onnx}, {@code decoder.int8.onnx}, {@code joiner.int8.onnx} and
	 * {@code tokens.txt}. Greedy search, no hotwords.
	 */
	public static OfflineRecognizerConfig parakeet(Path modelDir, int sttThreads) {
		String dir = modelDir.toAbsolutePath().toString();
		var transducer = OfflineTransducerModelConfig.builder().setEncoder(dir + "/encoder.int8.onnx")
			.setDecoder(dir + "/decoder.int8.onnx").setJoiner(dir + "/joiner.int8.onnx").build();
		var model = OfflineModelConfig.builder().setTransducer(transducer).setTokens(dir + "/tokens.txt")
			.setModelType("nemo_transducer").setNumThreads(sttThreads).setDebug(false).build();
		return OfflineRecognizerConfig.builder().setOfflineModelConfig(model).build();
	}

	/** Moonshine (base or tiny), from a folder holding its four files. */
	public static OfflineRecognizerConfig moonshine(Path modelDir, int sttThreads) {
		String dir = modelDir.toAbsolutePath().toString();
		var moon = OfflineMoonshineModelConfig.builder()
			.setEncoder(dir + "/encoder_model.ort").setMergedDecoder(dir + "/decoder_model_merged.ort").build();
		var model = OfflineModelConfig.builder().setMoonshine(moon).setTokens(dir + "/tokens.txt")
			.setNumThreads(sttThreads).setDebug(false).build();
		return OfflineRecognizerConfig.builder().setOfflineModelConfig(model).build();
	}

	/**
	 * The mod's VAD in front of any sherpa-onnx offline recognizer: the mod's own models above,
	 * or others that {@code ./gradlew sttCompare} measures through the same VAD and decoding code.
	 */
	public static SpeechEngine load(Path nativesDir, Supplier<OfflineRecognizerConfig> recognizerConfig, Path vadFile,
			float vadThreshold) {
		// Before any sherpa class loads; it loads onnxruntime, then the JNI lib.
		System.setProperty("sherpa_onnx.native.path", nativesDir.toAbsolutePath().toString());
		var recognizer = new OfflineRecognizer(recognizerConfig.get());
		try {
			var silero = SileroVadModelConfig.builder().setModel(vadFile.toAbsolutePath().toString())
				.setThreshold(vadThreshold).setMinSilenceDuration(MIN_SILENCE_SECONDS).setMinSpeechDuration(0.25f)
				.setMaxSpeechDuration(MAX_SPEECH_SECONDS).setWindowSize(WINDOW).build();
			var vad = new Vad(VadModelConfig.builder().setSileroVadModelConfig(silero)
				.setSampleRate(SAMPLE_RATE).setNumThreads(1).setDebug(false).build());
			return new SpeechEngine(vad, recognizer);
		} catch (RuntimeException | Error e) {
			recognizer.release();
			throw e;
		}
	}


	// --- VAD, capture thread ---

	@Override
	public void accept(float[] window) {
		synchronized (vadLock) {
			liveVad().acceptWaveform(window);
		}
	}

	@Override
	public List<RawSegment> drain() {
		synchronized (vadLock) {
			Vad v = liveVad();
			List<RawSegment> out = new ArrayList<>(1);
			while (!v.empty()) {
				SpeechSegment s = v.front();
				v.pop();
				out.add(new RawSegment(s.getStart(), s.getSamples()));
			}
			return out;
		}
	}

	@Override
	public boolean speaking() {
		synchronized (vadLock) {
			return vad != null && vad.isSpeechDetected();
		}
	}

	@Override
	public void reset() {
		synchronized (vadLock) {
			liveVad().reset();
		}
	}

	@Override
	public void flush() {
		synchronized (vadLock) {
			liveVad().flush();
		}
	}

	private Vad liveVad() {
		if (vad == null) {
			throw new IllegalStateException("speech engine released");
		}
		return vad;
	}

	// --- recognizer, STT thread ---

	@Override
	public String transcribe(float[] samples16k) {
		synchronized (sttLock) {
			if (recognizer == null) {
				throw new IllegalStateException("speech engine released");
			}
			if (samples16k.length <= MAX_DECODE_SECONDS * SAMPLE_RATE) {
				return decode(samples16k);
			}
			// Equal pieces of at most 8 s, so none is past the model's limit.
			int pieces = (int) Math.ceil(samples16k.length / (PIECE_SECONDS * SAMPLE_RATE));
			int size = (samples16k.length + pieces - 1) / pieces;
			StringBuilder text = new StringBuilder();
			for (int from = 0; from < samples16k.length; from += size) {
				String part = decode(Arrays.copyOfRange(samples16k, from, Math.min(samples16k.length, from + size))).trim();
				if (!part.isEmpty()) {
					text.append(text.isEmpty() ? "" : " ").append(part);
				}
			}
			return text.toString();
		}
	}

	private String decode(float[] samples) {
		OfflineStream st = recognizer.createStream();
		try {
			st.acceptWaveform(samples, SAMPLE_RATE);
			recognizer.decode(st);
			String text = recognizer.getResult(st).getText();
			return text == null ? "" : text;
		} finally {
			st.release();
		}
	}

	@Override
	public void close() {
		synchronized (vadLock) {
			synchronized (sttLock) {
				if (vad != null) {
					vad.release();
					vad = null;
				}
				if (recognizer != null) {
					recognizer.release();
					recognizer = null;
				}
			}
		}
	}
}
