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
import java.io.IOException;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.function.Supplier;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * The VAD and the recognizer: the only code that touches sherpa-onnx.
 *
 * <p>The VAD is used by the capture thread and the recognizer by the STT thread; each
 * has its own lock, and {@link #close()} takes both, so the engine can be released while
 * the other threads are still running and they get an {@link IllegalStateException}
 * instead of a use-after-free.
 *
 * <p>A Parakeet model with hotwords ({@link #recognition}) decodes by beam search
 * ({@code modified_beam_search}, {@link #MAX_ACTIVE_PATHS} paths), each segment on a stream of its
 * own with the {@link Hotwords} as they are at that moment; its pieces carry code names
 * ({@link ParakeetPieces}), and the text is made from them. Without hotwords, and for Moonshine,
 * it decodes greedily, as before. The recognizer can be replaced while the engine runs
 * ({@link #replaceRecognizer}), the VAD and its state kept: a bigger model swapped in once it is in.
 */
public final class SpeechEngine implements VoiceDetector, Transcriber, AutoCloseable {
	private static final Logger LOG = LoggerFactory.getLogger("vfg");
	public static final int SAMPLE_RATE = 16000;
	/** The beam with hotwords: sherpa-onnx's default, 4 (chosen by {@code ./gradlew sttCompare}). */
	public static final int MAX_ACTIVE_PATHS = 4;
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
	public static final float MIN_SILENCE_SECONDS = 0.35f;

	private final Object vadLock = new Object();
	private final Object sttLock = new Object();
	private Vad vad;
	private OfflineRecognizer recognizer;
	private ParakeetPieces pieces; // with hotwords: the recognizer reads code names
	private Hotwords.Settings boosts; // the model's boosts, or null for the hotwords' own
	private final Hotwords hotwords;

	private SpeechEngine(Vad vad, OfflineRecognizer recognizer, Recognition r, Hotwords hotwords) {
		this.vad = vad;
		this.recognizer = recognizer;
		this.pieces = r.pieces();
		this.boosts = r.boosts();
		this.hotwords = hotwords;
	}

	/**
	 * A recognizer's config; with hotwords, the model's pieces it reads by code name and the model's
	 * boosts ({@code null}: the {@link Hotwords}' own); without, both {@code null}.
	 */
	public record Recognition(OfflineRecognizerConfig config, ParakeetPieces pieces, Hotwords.Settings boosts) {
		public Recognition(OfflineRecognizerConfig config, ParakeetPieces pieces) {
			this(config, pieces, null);
		}

		public boolean hotwords() {
			return pieces != null;
		}
	}

	/**
	 * The recognizer for a model folder: Moonshine greedy; Parakeet greedy, or with {@code hotwords}
	 * by beam search over the pieces' code names, written beside {@code tokens.txt}. A folder where
	 * that file cannot be written decodes greedily, with a log line.
	 */
	public static Recognition recognition(Path modelDir, boolean parakeet, int sttThreads, boolean hotwords) {
		return recognition(modelDir, parakeet, sttThreads, hotwords, null);
	}

	/** The same with the model's own {@code boosts} ({@code null}: the {@link Hotwords}' own). */
	public static Recognition recognition(Path modelDir, boolean parakeet, int sttThreads, boolean hotwords,
			Hotwords.Settings boosts) {
		if (!parakeet) {
			return new Recognition(moonshine(modelDir, sttThreads), null);
		}
		if (hotwords) {
			try {
				ParakeetPieces pieces = ParakeetPieces.read(modelDir.resolve("tokens.txt"));
				Path coded = pieces.writeCoded(modelDir);
				return new Recognition(parakeetBeam(modelDir, sttThreads, coded, MAX_ACTIVE_PATHS), pieces, boosts);
			} catch (IOException | RuntimeException e) {
				LOG.warn("hotwords off: could not read or write the model's pieces in {} ({})", modelDir, e.toString());
			}
		}
		return new Recognition(parakeet(modelDir, sttThreads), null);
	}

	/**
	 * Loads the natives from {@code nativesDir} (onnxruntime, then the JNI library), the model from
	 * {@code modelDir} (Parakeet, else Moonshine) and the Silero VAD from {@code vadFile}; with
	 * {@code hotwords} (Parakeet only) each segment is decoded with them.
	 */
	public static SpeechEngine load(Path nativesDir, boolean parakeet, Path modelDir, Path vadFile, int sttThreads,
			float vadThreshold, Hotwords hotwords) {
		return load(nativesDir, parakeet, modelDir, vadFile, sttThreads, vadThreshold, hotwords, null);
	}

	/** The same with the model's own {@code boosts} ({@link Hotwords#PARAKEET_06B}; {@code null}: the hotwords' own). */
	public static SpeechEngine load(Path nativesDir, boolean parakeet, Path modelDir, Path vadFile, int sttThreads,
			float vadThreshold, Hotwords hotwords, Hotwords.Settings boosts) {
		return load(nativesDir, () -> recognition(modelDir, parakeet, sttThreads, hotwords != null, boosts), vadFile,
			vadThreshold, hotwords);
	}

	/** The same without hotwords: every model greedy. */
	public static SpeechEngine load(Path nativesDir, boolean parakeet, Path modelDir, Path vadFile, int sttThreads,
			float vadThreshold) {
		return load(nativesDir, parakeet, modelDir, vadFile, sttThreads, vadThreshold, null);
	}

	/**
	 * Parakeet TDT (a NeMo transducer, the default model), from a folder holding
	 * {@code encoder.int8.onnx}, {@code decoder.int8.onnx}, {@code joiner.int8.onnx} and
	 * {@code tokens.txt}. Greedy search, no hotwords.
	 */
	public static OfflineRecognizerConfig parakeet(Path modelDir, int sttThreads) {
		return parakeetModel(modelDir, sttThreads, modelDir.resolve("tokens.txt"), null).build();
	}

	/**
	 * Parakeet by beam search ({@code modified_beam_search}, {@code maxActivePaths} paths), reading the
	 * pieces by the code names in {@code codedTokens} ({@link ParakeetPieces#writeCoded}) with
	 * {@code modeling_unit cjkchar}, so each stream's hotwords can name pieces exactly.
	 */
	public static OfflineRecognizerConfig parakeetBeam(Path modelDir, int sttThreads, Path codedTokens,
			int maxActivePaths) {
		return parakeetModel(modelDir, sttThreads, codedTokens, "cjkchar").setDecodingMethod("modified_beam_search")
			.setMaxActivePaths(maxActivePaths).build();
	}

	private static OfflineRecognizerConfig.Builder parakeetModel(Path modelDir, int sttThreads, Path tokens,
			String modelingUnit) {
		String dir = modelDir.toAbsolutePath().toString();
		var transducer = OfflineTransducerModelConfig.builder().setEncoder(dir + "/encoder.int8.onnx")
			.setDecoder(dir + "/decoder.int8.onnx").setJoiner(dir + "/joiner.int8.onnx").build();
		var model = OfflineModelConfig.builder().setTransducer(transducer).setTokens(tokens.toAbsolutePath().toString())
			.setModelType("nemo_transducer").setNumThreads(sttThreads).setDebug(false);
		if (modelingUnit != null) {
			model.setModelingUnit(modelingUnit);
		}
		return OfflineRecognizerConfig.builder().setOfflineModelConfig(model.build());
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
		return load(nativesDir, () -> new Recognition(recognizerConfig.get(), null), vadFile, vadThreshold, null);
	}

	/** {@link #load} with a {@link Recognition}: with its pieces, each segment is decoded with {@code hotwords}. */
	public static SpeechEngine load(Path nativesDir, Supplier<Recognition> recognition, Path vadFile, float vadThreshold,
			Hotwords hotwords) {
		// Before any sherpa class loads; it loads onnxruntime, then the JNI lib.
		System.setProperty("sherpa_onnx.native.path", nativesDir.toAbsolutePath().toString());
		Recognition r = recognition.get();
		var recognizer = new OfflineRecognizer(r.config());
		try {
			var silero = SileroVadModelConfig.builder().setModel(vadFile.toAbsolutePath().toString())
				.setThreshold(vadThreshold).setMinSilenceDuration(MIN_SILENCE_SECONDS).setMinSpeechDuration(0.25f)
				.setMaxSpeechDuration(MAX_SPEECH_SECONDS).setWindowSize(WINDOW).build();
			var vad = new Vad(VadModelConfig.builder().setSileroVadModelConfig(silero)
				.setSampleRate(SAMPLE_RATE).setNumThreads(1).setDebug(false).build());
			return new SpeechEngine(vad, recognizer, r, hotwords);
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

	/**
	 * sherpa-onnx 1.13.8's beam search for TDT models keeps hypotheses at different frames in one beam,
	 * so now and then a path of blanks alone, which skips ahead in fewer and likelier steps, wins and
	 * the segment comes out empty: 3 of 907 segments for the 0.6B on {@code sttCompare}'s sets, each
	 * one greedy search heard whole. Such a segment is decoded again with a little silence after it,
	 * then before it, which moves the frames; this costs nothing on the segments that come out.
	 */
	static final double[][] EMPTY_RETRIES = {{0, 0.3}, {0.1, 0}};

	private String decode(float[] samples) {
		String words = pieces == null || hotwords == null ? ""
			: boosts == null ? hotwords.forModel(pieces) : hotwords.forModel(pieces, boosts);
		String[] text = new String[1];
		int tokens = decodeOnce(samples, words, text);
		if (pieces != null && tokens == 0 && samples.length >= SAMPLE_RATE / 4) {
			for (double[] pad : EMPTY_RETRIES) {
				tokens = decodeOnce(padded(samples, pad[0], pad[1]), words, text);
				if (tokens > 0) {
					LOG.debug("beam search came out empty on a {} s segment; heard with {} s of silence before and {} s after",
						String.format(java.util.Locale.ROOT, "%.1f", samples.length / (double) SAMPLE_RATE), pad[0], pad[1]);
					break;
				}
			}
		}
		return text[0];
	}

	/** Decodes once, puts the text in {@code text[0]} and returns how many pieces the recognizer answered. */
	private int decodeOnce(float[] samples, String words, String[] text) {
		OfflineStream st = words.isEmpty() ? recognizer.createStream() : recognizer.createStream(words);
		try {
			st.acceptWaveform(samples, SAMPLE_RATE);
			recognizer.decode(st);
			var result = recognizer.getResult(st);
			String[] tokens = result.getTokens();
			String t = pieces != null ? pieces.text(tokens) : result.getText();
			text[0] = t == null ? "" : t;
			return tokens == null ? 0 : tokens.length;
		} finally {
			st.release();
		}
	}

	/** {@code samples} with {@code before} and {@code after} seconds of silence. */
	static float[] padded(float[] samples, double before, double after) {
		int b = (int) Math.round(before * SAMPLE_RATE);
		int a = (int) Math.round(after * SAMPLE_RATE);
		float[] out = new float[b + samples.length + a];
		System.arraycopy(samples, 0, out, b, samples.length);
		return out;
	}

	/** Whether segments are decoded with hotwords (a Parakeet model, beam search). */
	public boolean hotwords() {
		synchronized (sttLock) {
			return pieces != null && hotwords != null;
		}
	}

	/**
	 * Swaps in a recognizer made from {@code next} (made before the lock is taken, so speech-to-text
	 * waits only for the swap) and releases the one it replaces; the VAD and its state stay. Returns
	 * {@code false}, releasing the new one, when the engine was released meanwhile.
	 */
	public boolean replaceRecognizer(Recognition next) {
		OfflineRecognizer made = new OfflineRecognizer(next.config());
		OfflineRecognizer old;
		synchronized (sttLock) {
			if (recognizer == null) {
				made.release();
				return false;
			}
			old = recognizer;
			recognizer = made;
			pieces = next.pieces();
			boosts = next.boosts();
		}
		old.release();
		return true;
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
