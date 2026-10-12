package com.voiceforgaming.vfg.kws;

import com.k2fsa.sherpa.onnx.KeywordSpotter;
import com.k2fsa.sherpa.onnx.KeywordSpotterConfig;
import com.k2fsa.sherpa.onnx.KeywordSpotterResult;
import com.k2fsa.sherpa.onnx.OnlineModelConfig;
import com.k2fsa.sherpa.onnx.OnlineStream;
import com.k2fsa.sherpa.onnx.OnlineTransducerModelConfig;
import com.k2fsa.sherpa.onnx.SileroVadModelConfig;
import com.k2fsa.sherpa.onnx.Vad;
import com.k2fsa.sherpa.onnx.VadModelConfig;
import com.voiceforgaming.vfg.speech.SampleClock;
import com.voiceforgaming.vfg.speech.SpeechEngine;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * sherpa-onnx's streaming {@link KeywordSpotter} over icefall's English GigaSpeech keyword
 * model (a 3.3M-parameter zipformer transducer, int8): the only sherpa-onnx code of the spotter.
 *
 * <p>sherpa-onnx takes keywords two ways: from {@code keywords_file}, which is required, must
 * exist and is read once when the spotter loads, and from {@code createStream(String)}, which
 * adds its own keywords to the file's for that stream only (it does not replace them). So the
 * file here is empty, and each stream gets exactly the player's keywords. A keyword with a token
 * the model lacks makes sherpa-onnx refuse the whole string; {@link Keywords} leaves those out.
 *
 * <p>The model decodes 320 ms chunks, 8 output frames of 40 ms, and a keyword fires once
 * {@link #TRAILING_BLANKS} blank frames follow its last token and its tokens' mean probability
 * reaches its threshold. Each token's time is its output frame times 40 ms, counted from the
 * stream's start or its last reset, whichever is later. The spotter resets a stream itself after
 * 1.5 s of blanks (sherpa-onnx issue #3990 is about that reset); {@link SpotterWorker} keeps its
 * streams shorter than that in silence.
 *
 * <p>The spotter's own VAD ({@link #vad}, a {@link SpotterVad}) is sherpa-onnx's too, so it is here.
 *
 * <p>One CPU thread. Used from {@code vfg-kws} only; {@link #close()} may come from another
 * thread, so every call takes the lock and fails once it is closed.
 */
public final class Spotter implements KeywordModel {
	public static final String ENCODER = "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx";
	public static final String DECODER = "decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx";
	public static final String JOINER = "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx";
	public static final String TOKENS = "tokens.txt";
	public static final String PIECES = "bpe.model";
	/** The files the mod takes from the model's archive. */
	public static final List<String> FILES = List.of(ENCODER, DECODER, JOINER, TOKENS, PIECES);
	/** The empty keywords file sherpa-onnx requires; the keywords come with each stream. */
	private static final String NO_KEYWORDS = "no-keywords.txt";

	/** Output frames per decoded chunk: 320 ms. */
	public static final int CHUNK_FRAMES = 8;
	/** 16 kHz samples per output frame: 40 ms. */
	public static final int FRAME_SAMPLES = SampleClock.SAMPLE_RATE / 25;
	/** Blank frames after the last token before a keyword fires (sherpa-onnx's Java default is 2). */
	public static final int TRAILING_BLANKS = 1;
	/** The silence that ends speech for the spotter's own VAD ({@link #vad}): 0.15 s. */
	public static final float VAD_MIN_SILENCE_SECONDS = 0.15f;
	/** The speech that starts it: 0.1 s. */
	public static final float VAD_MIN_SPEECH_SECONDS = 0.1f;
	/**
	 * Past this much unbroken speech sherpa-onnx's VAD raises its threshold to end it sooner; with
	 * 0.15 s of silence ending speech, it is never reached in practice.
	 */
	private static final float VAD_MAX_SPEECH_SECONDS = 20f;

	private final Object lock = new Object();
	private final SentencePiece pieces;
	private final Set<String> tokens;
	private final int trailingBlanks;
	private KeywordSpotter spotter;

	private Spotter(KeywordSpotter spotter, SentencePiece pieces, Set<String> tokens, int trailingBlanks) {
		this.spotter = spotter;
		this.pieces = pieces;
		this.tokens = Set.copyOf(tokens);
		this.trailingBlanks = trailingBlanks;
	}

	/**
	 * Loads the model from {@code modelDir} (the files in {@link #FILES}) on the natives in
	 * {@code nativesDir}, which the speech engine has usually loaded already.
	 */
	public static Spotter load(Path nativesDir, Path modelDir) throws IOException {
		return load(nativesDir, modelDir, TRAILING_BLANKS);
	}

	/** With another number of trailing blank frames, for measuring. */
	public static Spotter load(Path nativesDir, Path modelDir, int trailingBlanks) throws IOException {
		if (System.getProperty("sherpa_onnx.native.path") == null) {
			System.setProperty("sherpa_onnx.native.path", nativesDir.toAbsolutePath().toString());
		}
		String dir = modelDir.toAbsolutePath().toString();
		SentencePiece pieces = SentencePiece.load(modelDir.resolve(PIECES));
		Set<String> tokens = readTokens(modelDir.resolve(TOKENS));
		Path none = modelDir.resolve(NO_KEYWORDS);
		if (!Files.isRegularFile(none) || Files.size(none) != 0) {
			Files.writeString(none, "", StandardCharsets.UTF_8);
		}
		var transducer = OnlineTransducerModelConfig.builder().setEncoder(dir + "/" + ENCODER)
			.setDecoder(dir + "/" + DECODER).setJoiner(dir + "/" + JOINER).build();
		var model = OnlineModelConfig.builder().setTransducer(transducer).setTokens(dir + "/" + TOKENS)
			.setNumThreads(1).setDebug(false).setProvider("cpu").build();
		var config = KeywordSpotterConfig.builder().setOnlineModelConfig(model)
			.setKeywordsFile(none.toAbsolutePath().toString()).setNumTrailingBlanks(trailingBlanks)
			.setMaxActivePaths(4).build();
		return new Spotter(new KeywordSpotter(config), pieces, tokens, trailingBlanks);
	}

	/**
	 * The spotter's own VAD: Silero, from {@code vadFile} (the file the speech engine loads) at the
	 * capture's {@code threshold}, with {@link #VAD_MIN_SILENCE_SECONDS} of silence ending speech.
	 * It runs on every window the spotter is handed, about 70 µs each.
	 */
	public static SpotterVad vad(Path nativesDir, Path vadFile, double threshold) {
		if (System.getProperty("sherpa_onnx.native.path") == null) {
			System.setProperty("sherpa_onnx.native.path", nativesDir.toAbsolutePath().toString());
		}
		var silero = SileroVadModelConfig.builder().setModel(vadFile.toAbsolutePath().toString())
			.setThreshold((float) threshold).setMinSilenceDuration(VAD_MIN_SILENCE_SECONDS)
			.setMinSpeechDuration(VAD_MIN_SPEECH_SECONDS).setMaxSpeechDuration(VAD_MAX_SPEECH_SECONDS)
			.setWindowSize(SpeechEngine.WINDOW).build();
		return new SileroVad(new Vad(VadModelConfig.builder().setSileroVadModelConfig(silero)
			.setSampleRate(SampleClock.SAMPLE_RATE).setNumThreads(1).setDebug(false).build()));
	}

	/** The pieces of a {@code tokens.txt}: {@code <piece> <id>} a line. */
	static Set<String> readTokens(Path file) throws IOException {
		Set<String> out = new HashSet<>();
		for (String line : Files.readAllLines(file, StandardCharsets.UTF_8)) {
			int space = line.lastIndexOf(' ');
			if (space > 0) {
				out.add(line.substring(0, space));
			}
		}
		return out;
	}

	@Override
	public Stream open(String keywords) {
		synchronized (lock) {
			OnlineStream stream = live().createStream(keywords);
			if (stream.getPtr() == 0) {
				throw new IllegalArgumentException("the keyword model refused the keywords");
			}
			return new SherpaStream(stream);
		}
	}

	@Override
	public SentencePiece pieces() {
		return pieces;
	}

	@Override
	public Set<String> tokens() {
		return tokens;
	}

	@Override
	public int chunkFrames() {
		return CHUNK_FRAMES;
	}

	@Override
	public int trailingBlanks() {
		return trailingBlanks;
	}

	private KeywordSpotter live() {
		if (spotter == null) {
			throw new IllegalStateException("keyword spotter released");
		}
		return spotter;
	}

	@Override
	public void close() {
		synchronized (lock) {
			if (spotter != null) {
				spotter.release();
				spotter = null;
			}
		}
	}

	/** sherpa-onnx's VAD as the spotter uses it: only whether speech is in progress; its segments are dropped. */
	private static final class SileroVad implements SpotterVad {
		private final Object lock = new Object();
		private Vad vad;

		SileroVad(Vad vad) {
			this.vad = vad;
		}

		private Vad live() {
			if (vad == null) {
				throw new IllegalStateException("spotter VAD released");
			}
			return vad;
		}

		@Override
		public boolean speaking(float[] window) {
			synchronized (lock) {
				Vad v = live();
				v.acceptWaveform(window);
				while (!v.empty()) {
					v.pop();
				}
				return v.isSpeechDetected();
			}
		}

		@Override
		public void reset() {
			synchronized (lock) {
				live().reset();
			}
		}

		@Override
		public void close() {
			synchronized (lock) {
				if (vad != null) {
					vad.release();
					vad = null;
				}
			}
		}
	}

	private final class SherpaStream implements Stream {
		private OnlineStream stream;

		SherpaStream(OnlineStream stream) {
			this.stream = stream;
		}

		private OnlineStream liveStream() {
			if (stream == null) {
				throw new IllegalStateException("stream closed");
			}
			return stream;
		}

		@Override
		public void accept(float[] samples16k) {
			synchronized (lock) {
				live();
				liveStream().acceptWaveform(samples16k, SampleClock.SAMPLE_RATE);
			}
		}

		@Override
		public boolean ready() {
			synchronized (lock) {
				return live().isReady(liveStream());
			}
		}

		@Override
		public void decode() {
			synchronized (lock) {
				live().decode(liveStream());
			}
		}

		@Override
		public Hit hit() {
			synchronized (lock) {
				KeywordSpotterResult r = live().getResult(liveStream());
				String keyword = r.getKeyword();
				return keyword == null || keyword.isEmpty() ? null : new Hit(keyword, r.getTimestamps());
			}
		}

		@Override
		public void reset() {
			synchronized (lock) {
				live().reset(liveStream());
			}
		}

		@Override
		public void close() {
			synchronized (lock) {
				if (stream != null) {
					stream.release();
					stream = null;
				}
			}
		}
	}
}
