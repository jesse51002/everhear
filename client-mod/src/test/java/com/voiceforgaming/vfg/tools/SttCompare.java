package com.voiceforgaming.vfg.tools;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.voiceforgaming.vfg.audio.Resampler;
import com.voiceforgaming.vfg.speech.SpeechEngine;
import com.voiceforgaming.vfg.speech.VoiceDetector;
import com.voiceforgaming.vfg.testing.SpeechTimeline;
import com.voiceforgaming.vfg.testing.SttModels;
import com.voiceforgaming.vfg.testing.TestAssets;
import com.voiceforgaming.vfg.testing.Tts;
import com.voiceforgaming.vfg.testing.Wav;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.TimeUnit;

/**
 * {@code ./gradlew sttCompare}: Moonshine base (the mod's model), Moonshine tiny and two
 * Parakeet TDT models on the same audio, cut into the same segments by the mod's own VAD
 * (7 s cap) after the mod's own 48 kHz-to-16 kHz path. For each model: word error rate on
 * real recordings, on Minecraft phrases and on hard words, speech-to-text time per segment
 * (2 threads), download size, load time and resident memory. Each model runs in a JVM of
 * its own so its load time and memory are its own. Measurement only: the mod is unchanged.
 */
public final class SttCompare {
	private static final int THREADS = 2;
	private static final double GAP_SECONDS = 2.0;
	private static final int TAKES = 2;
	private static final Gson GSON = new GsonBuilder().setPrettyPrinting().disableHtmlEscaping().create();

	/** Hotwords for the "Parakeet + hotwords" run: names and Minecraft words from the phrases. */
	private static final List<String> HOTWORDS = List.of("SteveJM", "Alex", "Steve", "TP", "creeper", "netherite",
		"redstone");

	/** One thing said: its category, what was said (the reference), and how it was made. */
	record Utterance(String category, String reference, String source) {}

	/** A segment the VAD cut, and the utterance it belongs to. */
	record Seg(int utterance, double seconds) {}

	/** What one model made of the segments. */
	record ModelRun(String id, String label, long downloadBytes, long loadMillis, long rssBeforeKb, long rssLoadedKb,
			long rssPeakKb, List<String> texts, List<Long> sttMillis) {}

	private SttCompare() {}

	public static void main(String[] args) throws Exception {
		if (args.length >= 1 && args[0].equals("--one")) {
			one(args[1], Path.of(args[2]), Path.of(args[3]), args.length > 4 ? Path.of(args[4]) : null);
			return;
		}
		Path out = Path.of(System.getProperty("vfg.compareOut", "build/stt-compare")).toAbsolutePath();
		Files.createDirectories(out);

		// 1. The audio: real recordings and TTS phrases, one timeline, through the mic path.
		SpeechEngine vadEngine = SttModels.engine(SttModels.MOONSHINE_BASE, THREADS, null, 0);
		List<Utterance> utterances = new ArrayList<>();
		SpeechTimeline timeline = new SpeechTimeline().silence(1.0);
		Path parakeetDir = SttModels.install(SttModels.PARAKEET_06B);
		Path parakeet110Dir = SttModels.install(SttModels.PARAKEET_110M);
		Wav.Audio silero = Wav.read(TestAssets.fetch(TestAssets.SILERO_TEST_WAV));
		addRecording(timeline, utterances, "Kennedy (sherpa-onnx test clip)",
			"ask not what your country can do for you ask what you can do for your country",
			TestAssets.kennedy());
		addRecording(timeline, utterances, "LibriSpeech (Parakeet's test clip)",
			"well i don't wish to see it any more observed phoebe turning away her eyes it is certainly very like the old portrait",
			Wav.read(parakeetDir.resolve("test_wavs/0.wav")));
		addRecording(timeline, utterances, "en-english (Parakeet 110M's test clip)", "i love you",
			Wav.read(parakeet110Dir.resolve("test_wavs/en-english.wav")));
		addRecording(timeline, utterances, "lecture 0.0-2.3 s (sherpa-onnx VAD test)", "and says how do i get to dublin",
			cut(silero, 0.0, 2.3));
		addRecording(timeline, utterances, "lecture 20.2-35.65 s (sherpa-onnx VAD test; reference by ear)",
			"where we actually are and how the theories that people argue about in the journals and in the literature "
				+ "actually could be implemented in the world if at all and this spills over into normative arguments "
				+ "made by other scholars",
			cut(silero, 20.2, 35.65));
		try (Tts tts = new Tts()) {
			String[][] minecraft = {
				{"Build me a castle by the cave.", "build me a castle by the cave"},
				{"Make it day.", "make it day"},
				{"Who's winning?", "who's winning"},
				{"Give everyone diamonds.", "give everyone diamonds"},
				{"Spawn a zombie behind Steve.", "spawn a zombie behind steve"},
				{"Turn off the rain, please.", "turn off the rain please"},
			};
			String[][] hard = {
				{"That was a five second pause.", "that was a five second pause"},
				{"Put a torch on the pillar.", "put a torch on the pillar"},
				{"T P me to Alex.", "tp me to alex"},
				{"Spawn a creeper behind Steve J M.", "spawn a creeper behind stevejm"},
				{"Craft a netherite pickaxe.", "craft a netherite pickaxe"},
				{"Set my spawn point at the village.", "set my spawn point at the village"},
				{"Give me sixty four redstone.", "give me sixty four redstone"},
				{"Summon an ender dragon.", "summon an ender dragon"},
			};
			for (int take = 1; take <= TAKES; take++) {
				for (int voice = 0; voice < tts.voices(); voice++) {
					for (String[] p : minecraft) {
						addTts(timeline, utterances, tts, "minecraft", p, voice, take);
					}
					for (String[] p : hard) {
						addTts(timeline, utterances, tts, "hard", p, voice, take);
					}
				}
			}
		}
		float[] at16k = micPath(timeline.at48k());
		Wav.write(out.resolve("corpus-16k.wav"), at16k, 16000);
		System.out.printf(Locale.ROOT, "corpus: %d utterances, %.1f s of audio%n", utterances.size(), at16k.length / 16000.0);

		// 2. The segments, cut once by the mod's VAD (7 s cap), each tied to the utterance it overlaps most.
		List<float[]> segments = new ArrayList<>();
		List<Seg> segs = new ArrayList<>();
		VoiceDetector vad = vadEngine;
		vad.reset();
		List<VoiceDetector.RawSegment> raw = new ArrayList<>();
		for (int off = 0; off + 512 <= at16k.length; off += 512) {
			vad.accept(Arrays.copyOfRange(at16k, off, off + 512));
			raw.addAll(vad.drain());
		}
		vad.flush();
		raw.addAll(vad.drain());
		vadEngine.close();
		List<SpeechTimeline.Clip> clips = timeline.clips();
		for (VoiceDetector.RawSegment r : raw) {
			long from = r.start() * 3; // 16 kHz sample -> 48 kHz sample
			long to = (r.start() + r.samples().length) * 3;
			int best = -1;
			long bestOverlap = 0;
			long bestDistance = Long.MAX_VALUE;
			for (int c = 0; c < clips.size(); c++) {
				long overlap = Math.min(to, clips.get(c).end()) - Math.max(from, clips.get(c).start());
				long distance = Math.max(0, Math.max(clips.get(c).start() - to, from - clips.get(c).end()));
				if (overlap > bestOverlap || (bestOverlap == 0 && overlap <= 0 && distance < bestDistance)) {
					best = c;
					bestOverlap = Math.max(0, overlap);
					bestDistance = distance;
				}
			}
			segments.add(r.samples());
			segs.add(new Seg(best, r.samples().length / 16000.0));
		}
		Path segmentFile = out.resolve("segments.f32");
		writeSegments(segmentFile, segments);
		System.out.printf(Locale.ROOT, "segments: %d from the mod's VAD%n", segments.size());

		// 3. Each model in a JVM of its own.
		List<ModelRun> runs = new ArrayList<>();
		for (SttModels.Model m : SttModels.ALL) {
			runs.add(child(m.id(), segmentFile, out.resolve("run-" + m.id() + ".json"), null));
		}
		Path hotwords = Files.writeString(out.resolve("hotwords.txt"), String.join("\n", HOTWORDS) + "\n");
		runs.add(child(SttModels.PARAKEET_06B.id(), segmentFile, out.resolve("run-parakeet-hotwords.json"), hotwords));

		// 4. Word error rates and the table.
		report(out, utterances, segs, runs);
	}

	private static Wav.Audio cut(Wav.Audio a, double from, double to) {
		return new Wav.Audio(Arrays.copyOfRange(a.samples(), (int) (from * a.rate()), (int) (to * a.rate())), a.rate());
	}

	private static void addRecording(SpeechTimeline t, List<Utterance> u, String source, String reference,
			Wav.Audio audio) {
		t.speech(source, List.of(), audio).silence(GAP_SECONDS);
		u.add(new Utterance("recordings", reference, source));
	}

	/**
	 * Kitten samples noise, so each call says it a little differently: every take is kept on
	 * disk the first time and reused, so a second run hears exactly the same audio.
	 */
	private static void addTts(SpeechTimeline t, List<Utterance> u, Tts tts, String category, String[] phrase,
			int voice, int take) throws IOException {
		Path cache = TestAssets.root().resolve("stt-compare-tts")
			.resolve("v" + voice + "-t" + take + "-" + phrase[1].replaceAll("[^a-z0-9]+", "-") + ".wav");
		if (!Files.exists(cache)) {
			Wav.Audio a = tts.say(phrase[0], voice, 1.0f);
			Wav.write(cache, a.samples(), a.rate());
		}
		t.speech(phrase[0], List.of(), Wav.read(cache)).silence(GAP_SECONDS);
		u.add(new Utterance(category, phrase[1], "TTS voice " + voice + " take " + take + ": " + phrase[0]));
	}

	/** What a 48 kHz sound card and the mod make of it: 16-bit samples, then the mod's Resampler. */
	private static float[] micPath(float[] at48k) {
		float[] q = new float[at48k.length];
		for (int i = 0; i < q.length; i++) {
			q[i] = Wav.toShort(at48k[i]) / 32768f;
		}
		Resampler r = new Resampler();
		List<float[]> parts = new ArrayList<>();
		int total = 0;
		for (int off = 0; off < q.length; off += 1536) {
			float[] part = r.process(Arrays.copyOfRange(q, off, Math.min(q.length, off + 1536)));
			parts.add(part);
			total += part.length;
		}
		float[] out = new float[total];
		int o = 0;
		for (float[] p : parts) {
			System.arraycopy(p, 0, out, o, p.length);
			o += p.length;
		}
		return out;
	}

	private static void writeSegments(Path file, List<float[]> segments) throws IOException {
		try (DataOutputStream d = new DataOutputStream(Files.newOutputStream(file))) {
			d.writeInt(segments.size());
			for (float[] s : segments) {
				d.writeInt(s.length);
				for (float x : s) {
					d.writeFloat(x);
				}
			}
		}
	}

	private static List<float[]> readSegments(Path file) throws IOException {
		try (DataInputStream d = new DataInputStream(new java.io.BufferedInputStream(Files.newInputStream(file)))) {
			int n = d.readInt();
			List<float[]> out = new ArrayList<>(n);
			for (int i = 0; i < n; i++) {
				float[] s = new float[d.readInt()];
				for (int k = 0; k < s.length; k++) {
					s[k] = d.readFloat();
				}
				out.add(s);
			}
			return out;
		}
	}

	// --- one model, in its own JVM ---

	private static ModelRun child(String id, Path segments, Path result, Path hotwords) throws Exception {
		List<String> cmd = new ArrayList<>(List.of(ProcessHandle.current().info().command().orElse("java"),
			"--enable-native-access=ALL-UNNAMED", "-Dvfg.testAssets=" + TestAssets.root(),
			"-cp", System.getProperty("java.class.path"), SttCompare.class.getName(), "--one", id,
			segments.toString(), result.toString()));
		if (hotwords != null) {
			cmd.add(hotwords.toString());
		}
		System.out.printf(Locale.ROOT, "running %s%s ...%n", id, hotwords == null ? "" : " with hotwords");
		Process p = new ProcessBuilder(cmd).redirectErrorStream(true).start();
		try (InputStream in = p.getInputStream()) {
			for (String line : new String(in.readAllBytes(), StandardCharsets.UTF_8).split("\n")) {
				if (line.contains("Exception") || line.contains("rror") || line.startsWith("\tat ")) {
					System.out.println("  " + line);
				}
			}
		}
		if (!p.waitFor(30, TimeUnit.MINUTES) || p.exitValue() != 0) {
			throw new IllegalStateException(id + " failed");
		}
		return GSON.fromJson(Files.readString(result), ModelRun.class);
	}

	private static long statusKb(String key) throws IOException {
		for (String line : Files.readAllLines(Path.of("/proc/self/status"))) {
			if (line.startsWith(key + ":")) {
				return Long.parseLong(line.replaceAll("[^0-9]", ""));
			}
		}
		return -1;
	}

	/** Resets this process's peak resident memory (VmHWM) to its current value (Linux). */
	private static void resetPeak() {
		try {
			Files.writeString(Path.of("/proc/self/clear_refs"), "5");
		} catch (IOException e) {
			System.err.println("could not reset the peak RSS: " + e.getMessage());
		}
	}

	private static void one(String id, Path segmentFile, Path result, Path hotwords) throws Exception {
		SttModels.Model m = SttModels.byId(id);
		SttModels.install(m);
		List<float[]> segments = readSegments(segmentFile);
		System.gc();
		resetPeak();
		long rssBefore = statusKb("VmRSS");
		long t0 = System.nanoTime();
		SpeechEngine engine = SttModels.engine(m, THREADS, hotwords, 1.5f);
		long loadMillis = (System.nanoTime() - t0) / 1_000_000;
		long rssLoaded = statusKb("VmRSS");
		engine.transcribe(segments.getFirst()); // warm-up, not timed
		List<String> texts = new ArrayList<>();
		List<Long> millis = new ArrayList<>();
		for (float[] s : segments) {
			long t = System.nanoTime();
			texts.add(engine.transcribe(s));
			millis.add((System.nanoTime() - t) / 1_000_000);
		}
		long rssPeak = statusKb("VmHWM");
		engine.close();
		String label = m.label() + (hotwords == null ? "" : " + hotwords");
		ModelRun run = new ModelRun(hotwords == null ? id : id + "+hotwords", label, m.archive().bytes(), loadMillis,
			rssBefore, rssLoaded, rssPeak, texts, millis);
		Files.writeString(result, GSON.toJson(run));
	}

	// --- scoring ---

	private static final String[] ONES = {"zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
		"ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"};
	private static final String[] TENS = {"", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty",
		"ninety"};

	private static String numberWords(int n) {
		if (n < 20) {
			return ONES[n];
		}
		if (n < 100) {
			return TENS[n / 10] + (n % 10 == 0 ? "" : " " + ONES[n % 10]);
		}
		return Integer.toString(n);
	}

	/**
	 * Lowercase words without punctuation; numbers under 100 as words; "any more" as one word;
	 * runs of single letters joined ("t p" is "tp"); "steve jm" as one name.
	 */
	static List<String> words(String text) {
		String s = text.toLowerCase(Locale.ROOT).replace("%", " percent ").replace("-", " ").replace("'", "")
			.replace("’", "");
		s = s.replaceAll("[^a-z0-9 ]", " ");
		StringBuilder b = new StringBuilder();
		for (String w : s.trim().split("\\s+")) {
			if (w.matches("\\d{1,2}")) {
				w = numberWords(Integer.parseInt(w));
			}
			b.append(w).append(' ');
		}
		s = " " + b.toString().trim() + " ";
		s = s.replace(" any more ", " anymore ");
		List<String> out = new ArrayList<>();
		StringBuilder letters = new StringBuilder();
		for (String w : s.trim().split("\\s+")) {
			if (w.isEmpty()) {
				continue;
			}
			if (w.length() == 1 && !w.equals("a") && !w.equals("i")) {
				letters.append(w);
				continue;
			}
			if (!letters.isEmpty()) {
				out.add(letters.toString());
				letters.setLength(0);
			}
			out.add(w);
		}
		if (!letters.isEmpty()) {
			out.add(letters.toString());
		}
		for (int i = 0; i + 1 < out.size(); i++) {
			if (out.get(i).equals("steve") && out.get(i + 1).equals("jm")) {
				out.set(i, "stevejm");
				out.remove(i + 1);
			}
		}
		return out;
	}

	/** Word-level edit distance: substitutions + deletions + insertions. */
	static int edits(List<String> ref, List<String> hyp) {
		int[] prev = new int[hyp.size() + 1];
		int[] cur = new int[hyp.size() + 1];
		for (int j = 0; j <= hyp.size(); j++) {
			prev[j] = j;
		}
		for (int i = 1; i <= ref.size(); i++) {
			cur[0] = i;
			for (int j = 1; j <= hyp.size(); j++) {
				int sub = prev[j - 1] + (ref.get(i - 1).equals(hyp.get(j - 1)) ? 0 : 1);
				cur[j] = Math.min(sub, Math.min(prev[j] + 1, cur[j - 1] + 1));
			}
			int[] t = prev;
			prev = cur;
			cur = t;
		}
		return prev[hyp.size()];
	}

	private static void report(Path out, List<Utterance> utterances, List<Seg> segs, List<ModelRun> runs)
			throws IOException {
		List<String> categories = List.of("recordings", "minecraft", "hard");
		StringBuilder md = new StringBuilder();
		StringBuilder detail = new StringBuilder();
		md.append("# Speech-to-text comparison\n\nThe mod's default is Parakeet TDT 110M; Moonshine base and tiny "
			+ "can still be chosen. Parakeet 0.6B and hotwords are measured here only.\n\n");
		md.append(String.format(Locale.ROOT, "%d utterances (%d recordings, %d Minecraft phrases, %d hard phrases; "
				+ "TTS phrases in all 8 Kitten voices, 2 takes each), cut into %d segments by the mod's VAD after its 48 kHz path. "
				+ "%d speech-to-text threads, on %s.%n%n", utterances.size(),
			utterances.stream().filter(u -> u.category().equals("recordings")).count(),
			utterances.stream().filter(u -> u.category().equals("minecraft")).count(),
			utterances.stream().filter(u -> u.category().equals("hard")).count(), segs.size(), THREADS, cpu()));
		md.append("| Model | Download | Load | RSS after load / peak | STT per segment, mean / p95 | Real-time factor "
			+ "| WER recordings | WER Minecraft | WER hard words | WER all |\n");
		md.append("|---|---|---|---|---|---|---|---|---|---|\n");
		double totalAudio = segs.stream().mapToDouble(Seg::seconds).sum();
		for (ModelRun r : runs) {
			Map<String, int[]> perCategory = new LinkedHashMap<>(); // edits, reference words
			for (String c : categories) {
				perCategory.put(c, new int[2]);
			}
			detail.append("\n## ").append(r.label()).append("\n\n");
			for (int u = 0; u < utterances.size(); u++) {
				StringBuilder hyp = new StringBuilder();
				for (int s = 0; s < segs.size(); s++) {
					if (segs.get(s).utterance() == u) {
						hyp.append(r.texts().get(s)).append(' ');
					}
				}
				List<String> ref = words(utterances.get(u).reference());
				List<String> got = words(hyp.toString());
				int e = edits(ref, got);
				int[] acc = perCategory.get(utterances.get(u).category());
				acc[0] += e;
				acc[1] += ref.size();
				if (e > 0) {
					detail.append(String.format(Locale.ROOT, "- %s — %d error%s: \"%s\"%n", utterances.get(u).source(), e,
						e == 1 ? "" : "s", hyp.toString().trim()));
				}
			}
			int allEdits = perCategory.values().stream().mapToInt(a -> a[0]).sum();
			int allWords = perCategory.values().stream().mapToInt(a -> a[1]).sum();
			List<Long> ms = new ArrayList<>(r.sttMillis());
			ms.sort(null);
			double mean = ms.stream().mapToLong(Long::longValue).average().orElse(0);
			long p95 = ms.get((int) Math.min(ms.size() - 1, Math.ceil(ms.size() * 0.95) - 1));
			double rtf = ms.stream().mapToLong(Long::longValue).sum() / 1000.0 / totalAudio;
			md.append(String.format(Locale.ROOT, "| %s | %d MB | %.1f s | %d / %d MB | %.0f / %d ms | %.3f | %s | %s | %s | %s |%n",
				r.label(), r.downloadBytes() / 1_000_000, r.loadMillis() / 1000.0, (r.rssLoadedKb() - r.rssBeforeKb()) / 1024,
				(r.rssPeakKb() - r.rssBeforeKb()) / 1024,
				mean, p95, rtf, pct(perCategory.get("recordings")), pct(perCategory.get("minecraft")),
				pct(perCategory.get("hard")), pct(new int[] {allEdits, allWords})));
		}
		md.append("\nWER is word errors (substitutions, deletions, insertions) over reference words, after lowercasing, "
			+ "dropping punctuation, numbers as words, and joining spelled letters (\"T P\" = \"tp\"). "
			+ "Memory is resident memory added by loading the model (the mod's VAD and the natives included), "
			+ "right after loading and at the peak while transcribing. STT time excludes one warm-up decode.\n");
		md.append("\n# Every utterance with errors\n").append(detail);
		Files.writeString(out.resolve("report.md"), md, StandardCharsets.UTF_8);
		System.out.println();
		System.out.println(md.substring(0, md.indexOf("\n# Every utterance")));
		System.out.println("Full report, with every mistake: " + out.resolve("report.md"));
	}

	private static String pct(int[] editsAndWords) {
		return editsAndWords[1] == 0 ? "-" : String.format(Locale.ROOT, "%.1f%%", 100.0 * editsAndWords[0] / editsAndWords[1]);
	}

	private static String cpu() {
		try {
			for (String line : Files.readAllLines(Path.of("/proc/cpuinfo"))) {
				if (line.startsWith("model name")) {
					return line.substring(line.indexOf(':') + 1).trim();
				}
			}
		} catch (IOException e) {
			// not Linux
		}
		return System.getProperty("os.arch");
	}
}
