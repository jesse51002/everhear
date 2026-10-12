package com.voiceforgaming.vfg.kws;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.sun.jna.NativeLibrary;
import java.io.IOException;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import org.vosk.LibVosk;
import org.vosk.LogLevel;
import org.vosk.Model;

/**
 * Vosk 0.3.45 ({@code com.alphacephei:vosk}, Apache-2.0) over {@code vosk-model-small-en-us-0.15}
 * (Alpha Cephei, Apache-2.0): the only Vosk code of the mod. Its Java API, in the mod's jar, runs on
 * JNA, which Minecraft ships. Its library is not in the jar: the installer fetches this PC's into
 * the natives folder ({@code Assets.voskNatives}), and JNA is told to look there first, under both
 * names Vosk's Java API asks for ({@code vosk}, and {@code libvosk} on Windows), so it loads that
 * file in place: on Windows its MinGW runtime is found beside it.
 *
 * <p>Used from {@code vfg-vosk} only; {@link #close()} may come from another thread, so every
 * call takes the lock and fails once it is closed.
 */
public final class Vosk implements VoskModel {
	/** The files the mod takes from the model's zip, below its top folder. */
	public static final List<String> FILES = List.of("am/final.mdl", "conf/mfcc.conf", "conf/model.conf",
		"graph/disambig_tid.int", "graph/Gr.fst", "graph/HCLr.fst", "graph/phones/word_boundary.int",
		"ivector/final.dubm", "ivector/final.ie", "ivector/final.mat", "ivector/global_cmvn.stats",
		"ivector/online_cmvn.conf", "ivector/splice.conf", "README");
	public static final float SAMPLE_RATE = 16000f;

	private final Object lock = new Object();
	private final Set<String> vocabulary;
	private Model model;

	private Vosk(Model model, Set<String> vocabulary) {
		this.model = model;
		this.vocabulary = Set.copyOf(vocabulary);
	}

	/**
	 * Loads the library in {@code nativesDir} (once per game) and the model in {@code modelDir}, the
	 * files in {@link #FILES}. Vosk's own log lines go to the game's standard error; only its warnings
	 * and errors are kept.
	 */
	public static Vosk load(Path nativesDir, Path modelDir) throws IOException {
		String dir = nativesDir.toAbsolutePath().toString();
		NativeLibrary.addSearchPath("vosk", dir);
		NativeLibrary.addSearchPath("libvosk", dir);
		LibVosk.setLogLevel(LogLevel.WARNINGS);
		Set<String> words = VoskVocabulary.read(modelDir);
		return new Vosk(new Model(modelDir.toAbsolutePath().toString()), words);
	}

	@Override
	public boolean knows(String word) {
		return vocabulary.contains(word);
	}

	@Override
	public Recognizer recognizer(String grammarJson) {
		synchronized (lock) {
			org.vosk.Recognizer r = new org.vosk.Recognizer(live(), SAMPLE_RATE, grammarJson);
			if (r.getPointer() == null) {
				throw new IllegalArgumentException("Vosk refused the grammar");
			}
			r.setWords(true); // a final result's words with their times; partials stay plain text
			return new VoskRecognizer(r);
		}
	}

	private Model live() {
		if (model == null) {
			throw new IllegalStateException("Vosk released");
		}
		return model;
	}

	@Override
	public void close() {
		synchronized (lock) {
			if (model != null) {
				model.close();
				model = null;
			}
		}
	}

	/**
	 * A final result's words, {@code {"result": [{"word", "start", "end", "conf"}, …], "text": "…"}};
	 * none for anything else.
	 */
	static List<Word> words(String json) {
		List<Word> out = new ArrayList<>();
		try {
			JsonElement e = JsonParser.parseString(json);
			if (e.isJsonObject() && e.getAsJsonObject().has("result") && e.getAsJsonObject().get("result").isJsonArray()) {
				for (JsonElement w : e.getAsJsonObject().getAsJsonArray("result")) {
					JsonObject o = w.getAsJsonObject();
					out.add(new Word(o.get("word").getAsString(), o.get("start").getAsDouble(), o.get("end").getAsDouble()));
				}
			}
		} catch (RuntimeException ignored) {
			// not what Vosk answers: nothing heard
		}
		return out;
	}

	/** One of Vosk's JSON answers: {@code {"partial": "…"}} or {@code {"text": "…"}}. */
	static String text(String json, String key) {
		try {
			JsonElement e = JsonParser.parseString(json);
			if (e.isJsonObject()) {
				JsonObject o = e.getAsJsonObject();
				if (o.has(key) && o.get(key).isJsonPrimitive()) {
					return o.get(key).getAsString();
				}
			}
		} catch (RuntimeException ignored) {
			// not JSON: nothing heard
		}
		return "";
	}

	private final class VoskRecognizer implements Recognizer {
		private org.vosk.Recognizer recognizer;

		VoskRecognizer(org.vosk.Recognizer recognizer) {
			this.recognizer = recognizer;
		}

		private org.vosk.Recognizer liveRecognizer() {
			live();
			if (recognizer == null) {
				throw new IllegalStateException("recognizer closed");
			}
			return recognizer;
		}

		@Override
		public boolean accept(short[] samples) {
			synchronized (lock) {
				return liveRecognizer().acceptWaveForm(samples, samples.length);
			}
		}

		@Override
		public String partial() {
			synchronized (lock) {
				return text(liveRecognizer().getPartialResult(), "partial");
			}
		}

		@Override
		public List<Word> result() {
			synchronized (lock) {
				return words(liveRecognizer().getResult());
			}
		}

		@Override
		public void reset() {
			synchronized (lock) {
				liveRecognizer().reset();
			}
		}

		@Override
		public void close() {
			synchronized (lock) {
				if (recognizer != null) {
					recognizer.close();
					recognizer = null;
				}
			}
		}
	}
}
