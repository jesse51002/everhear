package com.voiceforgaming.vfg.tools;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.reflect.TypeToken;
import com.voiceforgaming.vfg.audio.Resampler;
import com.voiceforgaming.vfg.speech.Hotwords;
import com.voiceforgaming.vfg.speech.SpeechEngine;
import com.voiceforgaming.vfg.speech.VoiceDetector;
import com.voiceforgaming.vfg.testing.KokoroTakes;
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
import java.util.Random;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

/**
 * {@code ./gradlew sttCompare}: the speech-to-text models on the same audio, cut into the same
 * segments by the mod's own VAD (7 s cap) after the mod's own 48 kHz-to-16 kHz path, each model in
 * a JVM of its own. Parakeet TDT 0.6B v2 (the mod's default) and 110M (its fallback) run greedily
 * and with the mod's hotwords (beam search, {@link Hotwords}); Moonshine base and tiny greedily.
 *
 * <p>Two sets. <b>The Minecraft set</b>: five real recordings, and Minecraft phrases and hard words
 * in the eight Kitten voices, two takes each. <b>The addressing set</b>: lines that name the AI, a
 * player or an incantation, and everyday talk with none, from a set file (the committed one,
 * {@code stt-addressing.txt}, invented; {@code -PsttSet=<file>} for another), said by Kokoro in four
 * voices as it speaks ("clean") and in four others 1.25 times as fast with pink noise 10 dB under the
 * speech ("hard"). The hotwords are the mod's: "AI", "brain", the set's players and phrases (the
 * Minecraft set's players are Steve, Alex and SteveJM), and the command words.
 *
 * <p>It reports, for each model and way of decoding: word errors; how often "AI", "brain", the
 * players' names, the incantation phrases and the command words come through in the lines that hold
 * them; false hotwords (one said more often than in the reference) per 100 lines with none in them;
 * speech-to-text time per segment, and what beam search with hotwords adds to it over greedy; load
 * time and memory. Measurement only.
 *
 * <p>{@code -PsttTune=<file>}: decoding configurations to try instead of the standard ones, a JSON list
 * of {@link Config}; {@code -PsttMinecraft=false} leaves the Minecraft set out; {@code -PsttJobs=N} runs
 * N models' JVMs at once (times are then not comparable); {@code -PsttMoonshine=false} leaves Moonshine out.
 */
public final class SttCompare {
	private static final int THREADS = 2;
	private static final double GAP_SECONDS = 2.0;
	private static final int TAKES = 2;
	private static final int[] CLEAN_VOICES = {0, 3, 5, 8};
	private static final int[] HARD_VOICES = {1, 4, 6, 10};
	private static final float HARD_SPEED = 1.25f;
	private static final double HARD_SNR_DB = 10;
	private static final List<String> MINECRAFT_PLAYERS = List.of("Steve", "Alex", "SteveJM");
	private static final Gson GSON = new GsonBuilder().setPrettyPrinting().disableHtmlEscaping().create();

	/** One thing said: its set, its category, what was meant (the reference), how it was made, and whose hotwords it hears. */
	record Utterance(String set, String category, String reference, String source, int context) {}

	/** A segment the VAD cut, and the utterance it belongs to. */
	record Seg(int utterance, double seconds) {}

	/**
	 * One way to decode: a model, greedy or with hotwords (the beam's paths; each kind's boost; the
	 * command words; "Brain" beside "brain"). {@code null} boosts, commands or capitals are the mod's.
	 */
	record Config(String id, String label, String model, boolean hotwords, Integer paths, double[] boosts,
			List<String> commands, Boolean capitals, Boolean wholeNames, Double longName, Integer longNamePieces,
			Double oneWordPhrase) {
		Config(String id, String label, String model, boolean hotwords) {
			this(id, label, model, hotwords, null, null, null, null, null, null, null, null);
		}

		/** The model's own settings (the 0.6B's long names), then this configuration's changes. */
		Hotwords.Settings settings() {
			Hotwords.Settings s = model.equals(SttModels.PARAKEET_06B.id()) ? Hotwords.PARAKEET_06B : Hotwords.DEFAULT;
			if (boosts != null) {
				s = s.withBoosts(boosts[0], boosts[1], boosts[2], boosts[3], boosts[4]);
			}
			if (commands != null) {
				s = s.withCommands(commands);
			}
			if (capitals != null) {
				s = s.withCapitalBrain(capitals);
			}
			if (wholeNames != null) {
				s = s.withSplitNames(!wholeNames);
			}
			if (longName != null) {
				s = s.withLongNames(longName, longNamePieces == null ? 5 : longNamePieces);
			}
			if (oneWordPhrase != null) {
				s = s.withOneWordPhrases(oneWordPhrase);
			}
			return s;
		}

		int beam() {
			return paths == null ? SpeechEngine.MAX_ACTIVE_PATHS : paths;
		}
	}

	/** The players and phrases one context's hotwords hold. */
	record Lists(List<String> players, List<String> phrases) {}

	/** What a model's JVM is asked to do. */
	record Job(String model, String segments, List<Lists> lists, List<Integer> segmentContext, List<Config> configs,
			int threads) {}

	/** What one configuration made of the segments. */
	record Run(String id, String label, String model, boolean hotwords, long downloadBytes, long loadMillis,
			long rssBeforeKb, long rssLoadedKb, long rssPeakKb, List<String> texts, List<Long> sttMillis,
			List<String> hotwordLists, List<String> commands) {}

	/** The addressing set file: players, phrases, and the lines (said, meant). */
	record SetFile(String name, List<String> players, List<String> phrases, List<String[]> lines) {}

	private SttCompare() {}

	/** What a report is made from, kept beside it so it can be made again ({@code --report <dir>}). */
	record Corpus(SetFile set, List<Utterance> utterances, List<Seg> segs, List<Lists> lists, int threads,
			boolean tuning, List<String> runIds) {}

	public static void main(String[] args) throws Exception {
		if (args.length >= 1 && args[0].equals("--one")) {
			one(Path.of(args[1]), Path.of(args[2]));
			return;
		}
		if (args.length >= 2 && args[0].equals("--report")) {
			report(Path.of(args[1]), args.length > 2 && args[2].equals("--full"));
			return;
		}
		Path out = Path.of(System.getProperty("vfg.compareOut", "build/stt-compare")).toAbsolutePath();
		Files.createDirectories(out);
		SetFile set = readSet(System.getProperty("vfg.sttSet", ""));
		boolean withMinecraft = !"false".equals(System.getProperty("vfg.sttMinecraft", "true"));
		boolean withMoonshine = !"false".equals(System.getProperty("vfg.sttMoonshine", "true"));
		int jobs = Integer.parseInt(System.getProperty("vfg.sttJobs", "1"));
		int threads = Integer.parseInt(System.getProperty("vfg.sttThreads", String.valueOf(THREADS)));
		String tune = System.getProperty("vfg.sttTune", "");

		// 1. The audio: both sets, one timeline, through the mic path.
		SpeechEngine vadEngine = SttModels.engine(SttModels.PARAKEET_110M, THREADS);
		List<Utterance> utterances = new ArrayList<>();
		SpeechTimeline timeline = new SpeechTimeline().silence(1.0);
		if (withMinecraft) {
			minecraftSet(timeline, utterances);
		}
		addressingSet(timeline, utterances, set);
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

		// 3. Each model in a JVM of its own, its configurations one after another.
		List<Lists> lists = List.of(new Lists(MINECRAFT_PLAYERS, set.phrases()), new Lists(set.players(), set.phrases()));
		List<Integer> segmentContext = segs.stream().map(s -> utterances.get(s.utterance()).context()).toList();
		List<Config> configs = tune.isEmpty() ? standardConfigs(withMoonshine)
			: GSON.fromJson(Files.readString(Path.of(tune)), new TypeToken<List<Config>>() {}.getType());
		Map<String, List<Config>> byModel = new LinkedHashMap<>();
		for (Config c : configs) {
			byModel.computeIfAbsent(c.model(), k -> new ArrayList<>()).add(c);
		}
		ExecutorService pool = Executors.newFixedThreadPool(Math.max(1, jobs));
		List<Future<List<Run>>> pending = new ArrayList<>();
		int n = 0;
		for (Map.Entry<String, List<Config>> e : byModel.entrySet()) {
			// The standard configurations each in a JVM of their own, so each one's load time and memory are
			// its own; when tuning, with several JVMs at once, a model's configurations are split across them.
			List<List<Config>> parts = new ArrayList<>();
			int per = tune.isEmpty() ? 1
				: jobs > 1 ? Math.max(1, (int) Math.ceil(e.getValue().size() / (double) jobs)) : e.getValue().size();
			for (int i = 0; i < e.getValue().size(); i += per) {
				parts.add(e.getValue().subList(i, Math.min(e.getValue().size(), i + per)));
			}
			for (List<Config> part : parts) {
				Path jobFile = out.resolve("job-" + (n++) + ".json");
				Files.writeString(jobFile, GSON.toJson(new Job(e.getKey(), segmentFile.toString(), lists, segmentContext,
					part, threads)));
				pending.add(pool.submit(() -> child(jobFile, out.resolve(jobFile.getFileName().toString()
					.replace("job-", "runs-")))));
			}
		}
		List<Run> runs = new ArrayList<>();
		for (Future<List<Run>> f : pending) {
			runs.addAll(f.get());
		}
		pool.shutdown();
		List<Run> ordered = new ArrayList<>();
		for (Config c : configs) {
			runs.stream().filter(r -> r.id().equals(c.id())).findFirst().ifPresent(ordered::add);
		}

		// 4. The scores and the report.
		Files.writeString(out.resolve("corpus.json"), GSON.toJson(new Corpus(set, utterances, segs, lists, threads,
			!tune.isEmpty(), ordered.stream().map(Run::id).toList())));
		SttScores.report(out, set, utterances, segs, ordered, threads, lists, !tune.isEmpty());
	}

	/** The report again, from a run's {@code corpus.json} and {@code runs-*.json}; {@code full}: every utterance with errors too. */
	private static void report(Path dir, boolean full) throws Exception {
		Corpus c = GSON.fromJson(Files.readString(dir.resolve("corpus.json")), Corpus.class);
		Map<String, Run> byId = new LinkedHashMap<>();
		try (var files = Files.list(dir)) {
			for (Path f : files.filter(p -> p.getFileName().toString().matches("runs-\\d+\\.json")).sorted().toList()) {
				List<Run> runs = GSON.fromJson(Files.readString(f), new TypeToken<List<Run>>() {}.getType());
				runs.forEach(r -> byId.put(r.id(), r));
			}
		}
		List<Run> ordered = c.runIds().stream().map(byId::get).filter(java.util.Objects::nonNull).toList();
		SttScores.report(dir, c.set(), c.utterances(), c.segs(), ordered, c.threads(), c.lists(), c.tuning() && !full);
	}

	/** Greedy and with hotwords for each Parakeet model, today's model first; Moonshine greedy. */
	static List<Config> standardConfigs(boolean moonshine) {
		List<Config> out = new ArrayList<>();
		if (moonshine) {
			out.add(new Config("moonshine-base", "Moonshine base", "moonshine-base", false));
			out.add(new Config("moonshine-tiny", "Moonshine tiny", "moonshine-tiny", false));
		}
		out.add(new Config("110m", "Parakeet 110M, greedy (0.3.4's default)", "parakeet-110m", false));
		out.add(new Config("110m+hw", "Parakeet 110M + hotwords", "parakeet-110m", true));
		out.add(new Config("0.6b", "Parakeet 0.6B v2, greedy", "parakeet-0.6b", false));
		out.add(new Config("0.6b+hw", "Parakeet 0.6B v2 + hotwords (the mod's default)", "parakeet-0.6b", true));
		return out;
	}

	// --- the sets ---

	private static void minecraftSet(SpeechTimeline timeline, List<Utterance> utterances) throws Exception {
		Path parakeetDir = SttModels.install(SttModels.PARAKEET_06B);
		Path parakeet110Dir = SttModels.install(SttModels.PARAKEET_110M);
		Wav.Audio silero = Wav.read(TestAssets.fetch(TestAssets.SILERO_TEST_WAV));
		addRecording(timeline, utterances, "Kennedy (sherpa-onnx test clip)",
			"ask not what your country can do for you ask what you can do for your country", TestAssets.kennedy());
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
						addKitten(timeline, utterances, tts, "minecraft", p, voice, take);
					}
					for (String[] p : hard) {
						addKitten(timeline, utterances, tts, "hard", p, voice, take);
					}
				}
			}
		}
	}

	/** The committed set, or the file {@code path} names. */
	static SetFile readSet(String path) throws IOException {
		List<String> lines;
		String name;
		if (path == null || path.isBlank()) {
			try (InputStream in = SttCompare.class.getResourceAsStream("stt-addressing.txt")) {
				lines = new String(in.readAllBytes(), StandardCharsets.UTF_8).lines().toList();
			}
			name = "stt-addressing.txt (committed, invented)";
		} else {
			lines = Files.readAllLines(Path.of(path), StandardCharsets.UTF_8);
			name = Path.of(path).getFileName().toString();
		}
		return parseSet(name, lines);
	}

	static SetFile parseSet(String name, List<String> lines) {
		List<String> players = new ArrayList<>();
		List<String> phrases = new ArrayList<>();
		List<String[]> said = new ArrayList<>();
		for (String raw : lines) {
			String line = raw.strip();
			if (line.isEmpty() || line.startsWith("#")) {
				continue;
			}
			if (line.startsWith("players:")) {
				Arrays.stream(line.substring(8).split(",")).map(String::strip).filter(s -> !s.isEmpty()).forEach(players::add);
			} else if (line.startsWith("phrases:")) {
				Arrays.stream(line.substring(8).split(",")).map(String::strip).filter(s -> !s.isEmpty()).forEach(phrases::add);
			} else {
				int arrow = line.indexOf("=>");
				said.add(arrow < 0 ? new String[] {line, line}
					: new String[] {line.substring(0, arrow).strip(), line.substring(arrow + 2).strip()});
			}
		}
		return new SetFile(name, List.copyOf(players), List.copyOf(phrases), List.copyOf(said));
	}

	private static void addressingSet(SpeechTimeline timeline, List<Utterance> utterances, SetFile set) throws Exception {
		try (KokoroTakes kokoro = new KokoroTakes()) {
			int n = 0;
			for (String[] line : set.lines()) {
				for (int v : CLEAN_VOICES) {
					Wav.Audio a = kokoro.say(line[0], v);
					timeline.speech(line[0], List.of(), a).silence(GAP_SECONDS);
					utterances.add(new Utterance("addressing", "clean", line[1], "Kokoro voice " + v + ": " + line[0], 1));
				}
				for (int v : HARD_VOICES) {
					Wav.Audio a = noisy(kokoro.say(line[0], v, HARD_SPEED), HARD_SNR_DB, 1000L * n + v);
					timeline.speechAsIs(line[0], List.of(), a).silence(GAP_SECONDS);
					utterances.add(new Utterance("addressing", "hard", line[1],
						"Kokoro voice " + v + ", x" + HARD_SPEED + ", noise " + (int) HARD_SNR_DB + " dB: " + line[0], 1));
				}
				n++;
			}
		}
	}

	/**
	 * {@code a} with pink noise (Paul Kellet's filter over seeded white noise) {@code snrDb} under
	 * its own level, its speech trimmed first as the timeline trims it, with 0.15 s of noise each side.
	 */
	static Wav.Audio noisy(Wav.Audio a, double snrDb, long seed) {
		float[] s = a.samples();
		int first = 0;
		int last = s.length;
		double floor = Math.pow(10, -34 / 20.0);
		while (first < s.length && Math.abs(s[first]) < floor) {
			first++;
		}
		while (last > first && Math.abs(s[last - 1]) < floor) {
			last--;
		}
		int pad = (int) (0.15 * a.rate());
		float[] out = new float[last - first + 2 * pad];
		double sum = 0;
		for (int i = first; i < last; i++) {
			sum += s[i] * (double) s[i];
		}
		double speechRms = Math.sqrt(sum / Math.max(1, last - first));
		Random r = new Random(seed);
		double b0 = 0;
		double b1 = 0;
		double b2 = 0;
		float[] noise = new float[out.length];
		double nsum = 0;
		for (int i = 0; i < noise.length; i++) {
			double white = r.nextGaussian();
			b0 = 0.99765 * b0 + white * 0.0990460;
			b1 = 0.96300 * b1 + white * 0.2965164;
			b2 = 0.57000 * b2 + white * 1.0526913;
			noise[i] = (float) (b0 + b1 + b2 + white * 0.1848);
			nsum += noise[i] * (double) noise[i];
		}
		double gain = speechRms / Math.pow(10, snrDb / 20.0) / Math.sqrt(nsum / noise.length);
		for (int i = 0; i < out.length; i++) {
			int src = first - pad + i;
			float speech = src >= first && src < last ? s[src] : 0f;
			out[i] = (float) Math.max(-1, Math.min(1, speech + noise[i] * gain));
		}
		return new Wav.Audio(out, a.rate());
	}

	private static Wav.Audio cut(Wav.Audio a, double from, double to) {
		return new Wav.Audio(Arrays.copyOfRange(a.samples(), (int) (from * a.rate()), (int) (to * a.rate())), a.rate());
	}

	private static void addRecording(SpeechTimeline t, List<Utterance> u, String source, String reference,
			Wav.Audio audio) {
		t.speech(source, List.of(), audio).silence(GAP_SECONDS);
		u.add(new Utterance("minecraft", "recordings", reference, source, 0));
	}

	/**
	 * Kitten samples noise, so each call says it a little differently: every take is kept on
	 * disk the first time and reused, so a second run hears exactly the same audio.
	 */
	private static void addKitten(SpeechTimeline t, List<Utterance> u, Tts tts, String category, String[] phrase,
			int voice, int take) throws IOException {
		Path cache = TestAssets.root().resolve("stt-compare-tts")
			.resolve("v" + voice + "-t" + take + "-" + phrase[1].replaceAll("[^a-z0-9]+", "-") + ".wav");
		if (!Files.exists(cache)) {
			Wav.Audio a = tts.say(phrase[0], voice, 1.0f);
			Wav.write(cache, a.samples(), a.rate());
		}
		t.speech(phrase[0], List.of(), Wav.read(cache)).silence(GAP_SECONDS);
		u.add(new Utterance("minecraft", category, phrase[1], "Kitten voice " + voice + " take " + take + ": " + phrase[0],
			0));
	}

	/** What a 48 kHz sound card and the mod make of it: 16-bit samples, then the mod's Resampler. */
	static float[] micPath(float[] at48k) {
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
		try (DataOutputStream d = new DataOutputStream(new java.io.BufferedOutputStream(Files.newOutputStream(file)))) {
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

	private static List<Run> child(Path job, Path result) throws Exception {
		List<String> cmd = new ArrayList<>(List.of(ProcessHandle.current().info().command().orElse("java"),
			"--enable-native-access=ALL-UNNAMED", "-Dvfg.testAssets=" + TestAssets.root(),
			"-cp", System.getProperty("java.class.path"), SttCompare.class.getName(), "--one", job.toString(),
			result.toString()));
		Job j = GSON.fromJson(Files.readString(job), Job.class);
		System.out.printf(Locale.ROOT, "running %s: %s ...%n", j.model(), j.configs().stream().map(Config::id).toList());
		Process p = new ProcessBuilder(cmd).redirectErrorStream(true).start();
		try (InputStream in = p.getInputStream()) {
			for (String line : new String(in.readAllBytes(), StandardCharsets.UTF_8).split("\n")) {
				if (line.contains("Exception") || line.contains("rror") || line.startsWith("\tat ") || line.startsWith("done ")) {
					System.out.println("  " + line);
				}
			}
		}
		if (!p.waitFor(60, TimeUnit.MINUTES) || p.exitValue() != 0) {
			throw new IllegalStateException(j.model() + " failed");
		}
		return GSON.fromJson(Files.readString(result), new TypeToken<List<Run>>() {}.getType());
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

	private static void one(Path jobFile, Path result) throws Exception {
		Job job = GSON.fromJson(Files.readString(jobFile), Job.class);
		SttModels.Model m = SttModels.byId(job.model());
		SttModels.install(m);
		List<float[]> segments = readSegments(Path.of(job.segments()));
		List<Run> runs = new ArrayList<>();
		for (Config c : job.configs()) {
			Hotwords hotwords = c.hotwords() && !m.moonshine() ? new Hotwords(c.settings()) : null;
			System.gc();
			resetPeak();
			long rssBefore = statusKb("VmRSS");
			long t0 = System.nanoTime();
			SpeechEngine engine = SttModels.engine(m, job.threads(), hotwords, c.beam(), 0.5f);
			long loadMillis = (System.nanoTime() - t0) / 1_000_000;
			long rssLoaded = statusKb("VmRSS");
			List<String> lists = new ArrayList<>();
			if (hotwords != null) {
				for (Lists l : job.lists()) {
					hotwords.players(l.players());
					hotwords.phrases(l.phrases(), null);
					lists.add(hotwords.entries().toString());
				}
			}
			engine.transcribe(segments.getFirst()); // warm-up, not timed
			List<String> texts = new ArrayList<>();
			List<Long> millis = new ArrayList<>();
			for (int i = 0; i < segments.size(); i++) {
				if (hotwords != null) {
					Lists l = job.lists().get(job.segmentContext().get(i));
					hotwords.players(l.players());
					hotwords.phrases(l.phrases(), null);
				}
				long t = System.nanoTime();
				texts.add(engine.transcribe(segments.get(i)));
				millis.add((System.nanoTime() - t) / 1_000_000);
			}
			long rssPeak = statusKb("VmHWM");
			engine.close();
			runs.add(new Run(c.id(), c.label(), m.id(), hotwords != null, m.archive().bytes(), loadMillis, rssBefore,
				rssLoaded, rssPeak, texts, millis, lists, c.settings().commands()));
			System.out.println("done " + c.id());
		}
		Files.writeString(result, GSON.toJson(runs));
	}

	static String cpu() {
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
