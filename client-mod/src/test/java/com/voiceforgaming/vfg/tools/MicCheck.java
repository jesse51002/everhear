package com.voiceforgaming.vfg.tools;

import com.voiceforgaming.vfg.VoicePipeline;
import com.voiceforgaming.vfg.audio.MicCapture;
import com.voiceforgaming.vfg.audio.WindowListener.StopReason;
import com.voiceforgaming.vfg.config.VfgConfig;
import com.voiceforgaming.vfg.net.BridgeClient;
import com.voiceforgaming.vfg.net.LinePayload;
import com.voiceforgaming.vfg.speech.LineAssembler;
import com.voiceforgaming.vfg.speech.Levels;
import com.voiceforgaming.vfg.speech.Hotwords;
import com.voiceforgaming.vfg.speech.SpeechEngine;
import com.voiceforgaming.vfg.speech.Transcriber;
import com.voiceforgaming.vfg.testing.PlaybackDevice;
import com.voiceforgaming.vfg.testing.SpeechTimeline;
import com.voiceforgaming.vfg.testing.SttModels;
import com.voiceforgaming.vfg.testing.TestAssets;
import com.voiceforgaming.vfg.testing.Wav;
import com.voiceforgaming.vfg.turn.TurnDetector;
import com.voiceforgaming.vfg.turn.TurnDetectors;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Locale;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;

/**
 * {@code ./gradlew micCheck}: the mod's own speech pipeline on a real microphone, with no
 * Minecraft — {@link MicCapture} (OpenAL) → the Silero VAD → speech-to-text and the turn
 * model → the line assembler, the same {@link VoicePipeline} the mod runs. It shows a level
 * meter and what the VAD thinks, every segment it transcribes, and every line exactly as
 * the mod would send it; with {@code --bridge} it posts each line (docs/CONTRACTS.md §2)
 * and shows the answer. The mod's log comes out here too, with each decision on a line's
 * end as it is made ({@code turn 0.93 in 31 ms: closed}). Nothing here is in the mod's jar.
 */
public final class MicCheck {
	private static final String HELP = """
		Usage: ./gradlew micCheck [--args="<options>"]

		  --list                 list the microphones and exit
		  --device <n|name>      a number from the list, or a name (or a unique part of one);
		                         default 0, the system default, as in the mod
		  --seconds <n>          stop after n seconds (default: run until Enter or Ctrl-C)
		  --wav <file>           play a 16-bit WAV (16, 24 or 48 kHz) into the pipeline in real time
		                         instead of a mic, e.g. build/speech-test/commands-16k.wav
		  --bridge <url>         also POST each line to <url>/v1/lines, e.g. http://127.0.0.1:8787
		  --token <t>            the client token for --bridge (default: $VFG_CLIENT_TOKEN)
		  --player <name>        player_name in the posted lines (default MicCheck)
		  --model <m>            parakeet (Parakeet TDT 0.6B v2, the mod's default: 482 MB once, about
		                         0.9 GB of memory), parakeet-110m (Parakeet TDT 110M, its fallback),
		                         base (Moonshine base) or tiny (Moonshine tiny)
		  --players <n,n,...>    Parakeet: the players online, whose names are hotwords as in the mod
		                         ("AI", "brain" and the command words always are), e.g. SteveJM,Alex
		  --phrases <p,p,...>    Parakeet: the server's incantation phrases, hotwords too, e.g. fire ball,freeze
		  --no-hotwords          decode greedily with no hotwords (hotwords: false in the mod)
		  --turn <m>             the turn model: smart-turn-v3.2 (the mod's default), turn-1-mini, or off
		                         (lines end on silence alone)
		  --turn-threshold <x>   0.05-0.95: the score at which a line is finished (default: the model's own)
		  --silence <s>          seconds of silence that end a line the turn model keeps waiting
		                         (default 3, as in the mod)
		  --vad-threshold <x>    0.1-0.9 (default 0.5)
		  --threads <n>          speech-to-text threads (default 2)
		""";

	private static final Object OUT = new Object();
	private static final long START = System.currentTimeMillis();

	private MicCheck() {}

	private static void say(String format, Object... args) {
		synchronized (OUT) {
			System.out.printf(Locale.ROOT, format + "%n", args);
			System.out.flush();
		}
	}

	private static double since(long millis) {
		return (millis - START) / 1000.0;
	}

	public static void main(String[] argv) throws Exception {
		Options o;
		try {
			o = Options.parse(argv);
		} catch (IllegalArgumentException e) {
			System.err.println(e.getMessage());
			System.err.print(HELP);
			System.exit(2);
			return;
		}
		if (o.help) {
			System.out.print(HELP);
			return;
		}

		List<String> devices = MicCapture.listDevices();
		String systemDefault = MicCapture.defaultDeviceName();
		say("VFG mic check: the mod's own pipeline (OpenAL mic -> Silero VAD -> speech-to-text -> turn model -> lines),"
			+ " no Minecraft.");
		say("");
		say("Microphones:");
		say("  %2d  System default%s   (the mod's default)", 0, systemDefault == null ? "" : " -> " + systemDefault);
		for (int i = 0; i < devices.size(); i++) {
			say("  %2d  %s", i + 1, devices.get(i));
		}
		if (o.list) {
			return;
		}
		String device = pick(o.device, devices);
		String deviceLabel = device.isEmpty() ? "System default" : device;
		MicCapture.DeviceOpener opener = MicCapture.OPENAL;
		if (o.wav != null) {
			Path wav = Path.of(System.getProperty("vfg.projectDir", ".")).resolve(o.wav).normalize();
			Wav.Audio audio = Wav.read(wav);
			int rate = audio.rate() == 16000 ? 16000 : 48000;
			float[] samples = audio.rate() == rate ? audio.samples() : SpeechTimeline.to48k(audio);
			PlaybackDevice playback = new PlaybackDevice(samples, rate, new float[rate]);
			opener = playback;
			device = "";
			deviceLabel = wav.getFileName() + " (played in real time, " + rate + " Hz, no mic)";
			if (o.seconds == 0) {
				o.seconds = (int) Math.ceil(playback.recordingSeconds() + o.silence + 2);
			}
		}

		BridgeClient bridge = null;
		String linesUrl = null;
		if (o.bridge != null) {
			if (!VfgConfig.isValidBridgeUrl(o.bridge)) {
				throw new IllegalArgumentException("--bridge must be an http:// or https:// URL");
			}
			linesUrl = VfgConfig.linesUrl(o.bridge, "");
			String token = o.token != null ? o.token : System.getenv("VFG_CLIENT_TOKEN");
			if (token == null || token.isBlank()) {
				throw new IllegalArgumentException("--bridge needs --token or VFG_CLIENT_TOKEN");
			}
			BridgeClient.Target target = new BridgeClient.Target(linesUrl, token.trim());
			String url = linesUrl;
			bridge = new BridgeClient(() -> target, new BridgeClient.Listener() {
				@Override
				public void attempted(String clientLineId, int attempt, int status, String detail) {
					say("  POST %s (try %d) -> %s %s", url, attempt, status == 0 ? "no answer:" : status, detail);
				}

				@Override
				public void dropped(String clientLineId, String reason) {
					say("  line %s dropped: %s", clientLineId, reason);
				}
			}, () -> false);
			bridge.start();
		}

		SttModels.Model model = switch (o.model) {
			case "tiny" -> SttModels.MOONSHINE_TINY;
			case "parakeet-110m" -> SttModels.PARAKEET_110M;
			case "base" -> SttModels.MOONSHINE_BASE;
			default -> SttModels.PARAKEET_06B;
		};
		Hotwords hotwords = null;
		if (!model.moonshine() && !o.noHotwords) {
			hotwords = new Hotwords(model == SttModels.PARAKEET_06B ? Hotwords.PARAKEET_06B : Hotwords.DEFAULT);
			hotwords.players(o.players == null ? List.of() : List.of(o.players.split("\\s*,\\s*")));
			hotwords.phrases(o.phrases == null ? List.of() : List.of(o.phrases.split("\\s*,\\s*")), null);
			say("Hotwords: %s", hotwords.entries().stream().map(Hotwords.Entry::text).toList());
		}
		say("");
		say("Loading %s and the Silero VAD (downloaded once into %s)...", model.label(), TestAssets.root());
		long t0 = System.nanoTime();
		SpeechEngine engine = SttModels.engine(model, o.threads, hotwords, SpeechEngine.MAX_ACTIVE_PATHS,
			(float) o.vadThreshold);
		say("Loaded in %.2f s.", (System.nanoTime() - t0) / 1e9);
		TurnDetector turn = loadTurn(o.turn);

		UUID playerUuid = UUID.nameUUIDFromBytes(("OfflinePlayer:" + o.player).getBytes(StandardCharsets.UTF_8));
		AtomicInteger lineCount = new AtomicInteger();
		AtomicInteger segmentCount = new AtomicInteger();
		AtomicLong sttTotal = new AtomicLong();
		BridgeClient sender = bridge;
		Transcriber timed = samples -> {
			long t = System.nanoTime();
			String text = engine.transcribe(samples);
			long ms = (System.nanoTime() - t) / 1_000_000;
			segmentCount.incrementAndGet();
			sttTotal.addAndGet(ms);
			say("  segment: %.2f s of speech -> \"%s\"  (speech-to-text %d ms)", samples.length / 16000.0, text.trim(), ms);
			return text;
		};

		Meter meter = new Meter(o.silence);
		VoicePipeline pipeline = new VoicePipeline(opener, () -> engine, () -> timed, () -> turn, o.turnThreshold,
			o.silence, System::currentTimeMillis, () -> false, new VoicePipeline.Events() {
				@Override
				public void line(LineAssembler.Line line) {
					long ready = System.currentTimeMillis();
					LinePayload p = LinePayload.create(playerUuid, o.player, line.text(), line.startedAtMillis(),
						line.endedAtMillis(), line.peakDbfs(), line.meanDbfs());
					int n = lineCount.incrementAndGet();
					say("");
					say("== LINE %d == %d segment%s, ready %.2f s after the end of speech", n, line.segments(),
						line.segments() == 1 ? "" : "s", (ready - line.endedAtMillis()) / 1000.0);
					say("  text:    %s", p.text());
					say("  spoken:  %s -> %s (%.2f s)", LinePayload.timestamp(line.startedAtMillis()),
						LinePayload.timestamp(line.endedAtMillis()), (line.endedAtMillis() - line.startedAtMillis()) / 1000.0);
					say("  level:   peak %.1f dBFS, mean %.1f dBFS", line.peakDbfs(), line.meanDbfs());
					say("  body:    %s", p.toJson());
					if (sender != null) {
						sender.enqueue(p, ready);
					} else {
						say("  (not sent: add --bridge <url> to POST it)");
					}
					say("");
				}

				@Override
				public void live(boolean speaking, float livePeak, boolean lineOpen) {
					meter.update(livePeak);
				}

				@Override
				public void micError(String error) {
					if (error != null) {
						say("  %s", error);
					}
				}
			});

		CountDownLatch stop = new CountDownLatch(1);
		AtomicBoolean finished = new AtomicBoolean();
		Runnable finish = () -> {
			if (!finished.compareAndSet(false, true)) {
				return;
			}
			say("");
			say("Stopping: the mic closes and an open line is sent now, as when a player leaves the server.");
			pipeline.stopCapture(StopReason.LEAVE);
			long until = System.currentTimeMillis() + 5000;
			while (System.currentTimeMillis() < until && (pipeline.pendingSegments() > 0 || pipeline.lineOpen())) {
				sleep(50);
			}
			sleep(300);
			if (sender != null) {
				long wait = System.currentTimeMillis() + 15_000;
				while (System.currentTimeMillis() < wait && sender.queued() > 0) {
					sleep(100);
				}
				sleep(500);
				sender.shutdown(2000);
			}
			pipeline.shutdown(2000);
			int segments = segmentCount.get();
			say("Done: %d line%s from %d segment%s; speech-to-text took %d ms per segment on average.", lineCount.get(),
				lineCount.get() == 1 ? "" : "s", segments, segments == 1 ? "" : "s",
				segments == 0 ? 0 : sttTotal.get() / segments);
			if (turn != null) {
				turn.close();
			}
			engine.close();
		};
		Runtime.getRuntime().addShutdownHook(new Thread(finish, "vfg-mic-check-stop"));

		pipeline.start();
		pipeline.startCapture(device, true);
		say("");
		say("Listening on: %s. A line ends %safter %s s of silence.%s", deviceLabel,
			turn == null ? "" : "when " + o.turn + " hears it finished, or ", fmt(o.silence),
			linesUrl == null ? "" : " Lines go to " + linesUrl + ".");
		if (o.seconds > 0) {
			say("Stopping after %d s (or press Enter).", o.seconds);
		} else {
			say("Press Enter (or Ctrl-C) to stop.");
		}
		say("");

		Thread input = new Thread(() -> {
			try {
				if (new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8)).readLine() != null) {
					stop.countDown(); // Enter; end of input (no terminal) is not a request to stop
				}
			} catch (Exception e) {
				// no console: only --seconds or Ctrl-C stop it
			}
		}, "vfg-mic-check-input");
		input.setDaemon(true);
		input.start();

		Thread printer = new Thread(() -> meter.run(pipeline, stop), "vfg-mic-check-meter");
		printer.setDaemon(true);
		printer.start();

		if (o.seconds > 0) {
			stop.await(o.seconds, TimeUnit.SECONDS);
		} else {
			stop.await();
		}
		stop.countDown();
		finish.run();
		System.exit(0);
	}

	/** The level meter and the VAD's state: a line a second, and a line at once when speech starts or stops. */
	private static final class Meter {
		private static final int WIDTH = 30;
		private final double silence;
		private volatile float peak;

		Meter(double silence) {
			this.silence = silence;
		}

		void update(float livePeak) {
			if (livePeak > peak) {
				peak = livePeak;
			}
		}

		void run(VoicePipeline pipeline, CountDownLatch stop) {
			boolean wasSpeaking = false;
			long lastPrint = 0;
			try {
				while (!stop.await(100, TimeUnit.MILLISECONDS)) {
					long now = System.currentTimeMillis();
					boolean speaking = pipeline.speaking();
					if (speaking == wasSpeaking && now - lastPrint < 1000) {
						continue;
					}
					wasSpeaking = speaking;
					lastPrint = now;
					float p = peak;
					peak = 0f;
					double db = Levels.toDbfs(p);
					int filled = (int) Math.round(Math.max(0, Math.min(1, (db + 60) / 60.0)) * WIDTH);
					String bar = "#".repeat(filled) + "-".repeat(WIDTH - filled);
					String state;
					if (speaking) {
						state = "SPEECH";
					} else if (pipeline.lineOpen()) {
						double left = (pipeline.openLineEndedAt() + silence * 1000 + 500 - now) / 1000.0;
						state = left > 0 ? String.format(Locale.ROOT, "silence, line open: closes in %.1f s", left)
							: "silence, line closing";
					} else {
						state = "listening";
					}
					say("[%6.1f s] %6.1f dBFS |%s| %s", since(now), db, bar, state);
				}
			} catch (InterruptedException e) {
				// stopping
			}
		}
	}

	/**
	 * The turn model, installed once into the test assets and loaded as the mod loads it, on
	 * the runtime the engine loaded; {@code null} for {@code off}, or when it will not load.
	 */
	private static TurnDetector loadTurn(String turnModel) {
		if (turnModel.equals(TurnDetectors.OFF)) {
			return null;
		}
		try {
			long t0 = System.nanoTime();
			TurnDetector loaded = TestAssets.turnDetector(turnModel);
			say("Loaded the turn model, %s, in %.2f s.", turnModel, (System.nanoTime() - t0) / 1e9);
			return loaded;
		} catch (Exception | LinkageError e) {
			say("The turn model %s did not load (%s): lines end on silence alone, as in the mod.", turnModel,
				e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage());
			return null;
		}
	}

	private static String pick(String wanted, List<String> devices) {
		if (wanted == null || wanted.isBlank() || wanted.equals("0")) {
			return "";
		}
		if (wanted.matches("\\d+")) {
			int i = Integer.parseInt(wanted);
			if (i < 1 || i > devices.size()) {
				throw new IllegalArgumentException("no microphone number " + i + " (see --list)");
			}
			return devices.get(i - 1);
		}
		for (String d : devices) {
			if (d.equals(wanted)) {
				return d;
			}
		}
		List<String> matches = devices.stream()
			.filter(d -> d.toLowerCase(Locale.ROOT).contains(wanted.toLowerCase(Locale.ROOT))).toList();
		if (matches.size() == 1) {
			return matches.getFirst();
		}
		throw new IllegalArgumentException(matches.isEmpty() ? "no microphone matches \"" + wanted + "\""
			: "\"" + wanted + "\" matches " + matches.size() + " microphones; use its number");
	}

	private static String fmt(double v) {
		return v == Math.rint(v) ? Long.toString((long) v) : Double.toString(v);
	}

	private static void sleep(long ms) {
		try {
			Thread.sleep(ms);
		} catch (InterruptedException e) {
			Thread.currentThread().interrupt();
		}
	}

	private static final class Options {
		boolean help;
		boolean list;
		String device;
		int seconds;
		String bridge;
		String token;
		String wav;
		String players;
		String phrases;
		boolean noHotwords;
		String player = "MicCheck";
		String model = "parakeet";
		String turn = TurnDetectors.DEFAULT;
		Double turnThreshold;
		double silence = VfgConfig.DEFAULT_SILENCE_SECONDS;
		double vadThreshold = VfgConfig.DEFAULT_VAD_THRESHOLD;
		int threads = VfgConfig.DEFAULT_STT_THREADS;

		static Options parse(String[] a) {
			Options o = new Options();
			for (int i = 0; i < a.length; i++) {
				String k = a[i];
				switch (k) {
					case "-h", "--help" -> o.help = true;
					case "--list" -> o.list = true;
					case "--device" -> o.device = value(a, ++i, k);
					case "--seconds" -> o.seconds = (int) number(value(a, ++i, k), k, 1, 86400);
					case "--bridge" -> o.bridge = value(a, ++i, k);
					case "--wav" -> o.wav = value(a, ++i, k);
					case "--token" -> o.token = value(a, ++i, k);
					case "--player" -> o.player = value(a, ++i, k);
					case "--model" -> {
						o.model = value(a, ++i, k);
						if (!List.of("base", "tiny", "parakeet", "parakeet-110m").contains(o.model)) {
							throw new IllegalArgumentException("--model is base, tiny, parakeet-110m or parakeet");
						}
					}
					case "--players", "--hotwords" -> o.players = value(a, ++i, k);
					case "--phrases" -> o.phrases = value(a, ++i, k);
					case "--no-hotwords" -> o.noHotwords = true;
					case "--turn" -> {
						o.turn = value(a, ++i, k);
						if (!TurnDetectors.MODELS.contains(o.turn)) {
							throw new IllegalArgumentException("--turn is one of "
								+ String.join(", ", TurnDetectors.MODELS));
						}
					}
					case "--turn-threshold" -> o.turnThreshold = number(value(a, ++i, k), k, 0.05, 0.95);
					case "--silence" -> o.silence = number(value(a, ++i, k), k, 2, 30);
					case "--vad-threshold" -> o.vadThreshold = number(value(a, ++i, k), k, 0.1, 0.9);
					case "--threads" -> o.threads = (int) number(value(a, ++i, k), k, 1, 8);
					default -> throw new IllegalArgumentException("unknown option " + k);
				}
			}
			return o;
		}

		private static String value(String[] a, int i, String key) {
			if (i >= a.length) {
				throw new IllegalArgumentException(key + " needs a value");
			}
			return a[i];
		}

		private static double number(String v, String key, double min, double max) {
			try {
				double d = Double.parseDouble(v);
				if (d < min || d > max) {
					throw new IllegalArgumentException(key + " must be " + fmt(min) + "-" + fmt(max));
				}
				return d;
			} catch (NumberFormatException e) {
				throw new IllegalArgumentException(key + " must be a number");
			}
		}
	}
}
