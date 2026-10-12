package com.voiceforgaming.vfg.tools;

import com.voiceforgaming.vfg.VoicePipeline;
import com.voiceforgaming.vfg.audio.WindowListener.StopReason;
import com.voiceforgaming.vfg.config.VfgConfig;
import com.voiceforgaming.vfg.kws.Incantation;
import com.voiceforgaming.vfg.kws.SpotterWorker;
import com.voiceforgaming.vfg.net.BridgeClient;
import com.voiceforgaming.vfg.net.CastClient;
import com.voiceforgaming.vfg.net.CastPayload;
import com.voiceforgaming.vfg.net.IncantationClient;
import com.voiceforgaming.vfg.net.LinePayload;
import com.voiceforgaming.vfg.speech.LineAssembler;
import com.voiceforgaming.vfg.speech.SpeechEngine;
import com.voiceforgaming.vfg.testing.KokoroTakes;
import com.voiceforgaming.vfg.testing.PlaybackDevice;
import com.voiceforgaming.vfg.testing.SpeechTimeline;
import com.voiceforgaming.vfg.testing.TestAssets;
import com.voiceforgaming.vfg.testing.Wav;
import com.voiceforgaming.vfg.turn.TurnDetector;
import com.voiceforgaming.vfg.turn.TurnDetectors;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

/**
 * {@code ./gradlew castCheck}: a WAV played through the mod's own pipeline and keyword spotter as
 * if from the mic, with no Minecraft, posting casts ({@code POST /v1/casts}) and lines
 * ({@code POST /v1/lines}) to a real bridge, for the end-to-end test. It prints, for each cast and
 * each line, when it was said on the audio clock and when it was heard, posted and answered.
 *
 * <p>The audio clock maps to the wall clock as a mic's does: the WAV plays through a
 * {@link PlaybackDevice}, whose samples become readable as the wall clock runs, from the moment
 * the capture thread opens it, as a sound card's do; {@code MicCapture} stamps each 32 ms read with
 * the wall time it was read at, and the {@code SampleClock} turns samples into instants from those
 * reads. So sample {@code i} of the WAV is at about {@code opened + i / rate}, a real UTC instant
 * the bridge can compare with its own clock and with other players' lines. Times are printed both
 * as those instants and as seconds into the WAV ({@code instant - opened}).
 */
public final class CastCheck {
	private static final String HELP = """
		Usage: ./gradlew castCheck --args="<options>"

		  --wav <file>           a 16-bit WAV (16, 24 or 48 kHz) to play as if from the mic
		  --say <text>           or: Kokoro says this (repeatable; in order, 2.5 s apart), kept in
		                         build/cast-check/said.wav to play again with --wav
		  --voice <n>            Kokoro's voice for --say, 0-10 (default 0)
		  --bridge <url>         the bridge, e.g. http://127.0.0.1:8787 (required)
		  --token <t>            the client token (default: $VFG_CLIENT_TOKEN)
		  --player <name>        player_name for casts, lines and the incantation list (default CastCheck)
		  --incantation <p=a,b>  also listen for phrase p with aliases a and b, beside the bridge's list
		                         (repeatable; for a bridge that lists none)
		  --threshold <x>        the spotter's threshold, 0.05-0.95 (default 0.25, the mod's)
		  --boost <x>            the spotter's boost, 0.5-5 (default 1.5, the mod's)
		  --recheck <s>          how far the spotter's re-check reaches back, 0 or 1-10 s (default 3, the
		                         mod's; 0: the live stream alone)
		""";

	private static final Object OUT = new Object();

	private CastCheck() {}

	private static void say(String format, Object... args) {
		synchronized (OUT) {
			System.out.printf(Locale.ROOT, format + "%n", args);
			System.out.flush();
		}
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
		String token = o.token != null ? o.token : System.getenv("VFG_CLIENT_TOKEN");
		if (token == null || token.isBlank()) {
			throw new IllegalArgumentException("--token or VFG_CLIENT_TOKEN is needed");
		}
		String key = token.trim();

		say("Loading Parakeet TDT 110M, the Silero VAD, Smart Turn and the keyword model...");
		SpeechEngine engine = TestAssets.engine(VfgConfig.DEFAULT_MODEL, VfgConfig.DEFAULT_STT_THREADS, 0.5f);
		TurnDetector turn = TestAssets.turnDetector(TurnDetectors.DEFAULT);
		var model = TestAssets.spotter();
		var spotterVad = TestAssets.spotterVad(0.5);

		Wav.Audio audio = o.wav != null ? Wav.read(Path.of(System.getProperty("vfg.projectDir", ".")).resolve(o.wav))
			: said(o.said, o.voice);
		int rate = audio.rate() == 16000 ? 16000 : 48000;
		float[] samples = audio.rate() == rate ? audio.samples() : SpeechTimeline.to48k(audio);
		PlaybackDevice device = new PlaybackDevice(samples, rate, new float[rate]);

		List<Incantation> list = new ArrayList<>(o.incantations);
		IncantationClient lister = new IncantationClient(
			() -> new IncantationClient.Target(VfgConfig.endpoint(o.bridge, "", "/v1/incantations"), key), 0, ms -> {},
			(version, got) -> {
				say("The bridge lists %d incantation%s for %s (version %d): %s", got.size(), got.size() == 1 ? "" : "s",
					o.player, version, got.stream().map(i -> "\"" + i.phrase() + "\" " + i.aliases()).toList());
				list.addAll(0, got);
			});
		IncantationClient.Poll poll = lister.poll(o.player);
		if (poll != IncantationClient.Poll.UPDATED) {
			say("The bridge did not list the incantations (%s); listening only for --incantation.", poll);
		}
		if (list.isEmpty()) {
			say("No incantations to listen for: lines only.");
		}

		UUID uuid = UUID.nameUUIDFromBytes(("OfflinePlayer:" + o.player).getBytes(StandardCharsets.UTF_8));
		Map<String, Long> posted = new ConcurrentHashMap<>();
		CastClient casts = new CastClient(() -> new CastClient.Target(VfgConfig.endpoint(o.bridge, "", "/v1/casts"), key),
			new CastClient.Listener() {
				@Override
				public void answered(CastPayload cast, int status, String castStatus, long answeredAtMillis) {
					long sent = posted.getOrDefault(cast.clientCastId(), answeredAtMillis);
					say("  cast \"%s\": answered %d %s at %s s (%d ms after posting, %.2f s after the phrase)",
						cast.phrase(), status, castStatus == null ? "" : castStatus, at(device, answeredAtMillis),
						answeredAtMillis - sent, (answeredAtMillis - cast.endedAtMillis()) / 1000.0);
				}

				@Override
				public void dropped(CastPayload cast, String reason) {
					say("  cast \"%s\": dropped, %s", cast.phrase(), reason);
				}
			});
		SpotterWorker spotter = new SpotterWorker(true, o.threshold, o.boost, o.recheck, cast -> {
			CastPayload p = CastPayload.create(uuid, o.player, cast.phrase(), cast.heard(), cast.startedAtMillis(),
				cast.endedAtMillis());
			long now = System.currentTimeMillis();
			posted.put(p.clientCastId(), now);
			say("CAST \"%s\" (heard \"%s\" by %s): said %s -> %s s (%s -> %s), heard at %s s, posted at %s s",
				p.phrase(), p.heard(), cast.via().words(), at(device, cast.startedAtMillis()),
				at(device, cast.endedAtMillis()), LinePayload.timestamp(cast.startedAtMillis()),
				LinePayload.timestamp(cast.endedAtMillis()), at(device, cast.heardAtMillis()), at(device, now));
			casts.post(p, cast.heardAtMillis(), cast.via().words());
		});
		spotter.setModel(model, spotterVad);
		spotter.setIncantations(list);

		Map<String, Long> enqueued = new ConcurrentHashMap<>();
		BridgeClient lines = new BridgeClient(() -> new BridgeClient.Target(VfgConfig.linesUrl(o.bridge, ""), key),
			new BridgeClient.Listener() {
				@Override
				public void attempted(String clientLineId, int attempt, int status, String detail) {
					long now = System.currentTimeMillis();
					say("  line %s: answered %s at %s s (%d ms after posting)", clientLineId.substring(0, 8),
						status == 0 ? "nothing (" + detail + ")" : status + " " + detail, at(device, now),
						now - enqueued.getOrDefault(clientLineId, now));
				}
			}, () -> false);
		lines.start();
		VoicePipeline pipeline = new VoicePipeline(device, () -> engine, () -> engine, () -> turn, null,
			VfgConfig.DEFAULT_SILENCE_SECONDS, System::currentTimeMillis, () -> false, new VoicePipeline.Events() {
				@Override
				public void line(LineAssembler.Line line) {
					LinePayload p = LinePayload.create(uuid, o.player, line.text(), line.startedAtMillis(),
						line.endedAtMillis(), line.peakDbfs(), line.meanDbfs());
					long now = System.currentTimeMillis();
					enqueued.put(p.clientLineId(), now);
					say("LINE %s \"%s\": said %s -> %s s (%s -> %s), closed and posted at %s s", p.clientLineId()
						.substring(0, 8), p.text(), at(device, line.startedAtMillis()), at(device, line.endedAtMillis()),
						LinePayload.timestamp(line.startedAtMillis()), LinePayload.timestamp(line.endedAtMillis()),
						at(device, now));
					lines.enqueue(p, line.closedAtMillis());
				}
			}, spotter);

		double seconds = device.recordingSeconds();
		say("Playing %.1f s as %s's mic (%d Hz) to %s; times are seconds into the WAV.", seconds, o.player, rate,
			o.bridge);
		spotter.start();
		pipeline.start();
		pipeline.startCapture("", true);
		Thread.sleep((long) ((seconds + VfgConfig.DEFAULT_SILENCE_SECONDS + 1.5) * 1000));
		pipeline.stopCapture(StopReason.LEAVE);
		long until = System.currentTimeMillis() + 5000;
		while (System.currentTimeMillis() < until && (pipeline.pendingSegments() > 0 || pipeline.lineOpen())) {
			Thread.sleep(50);
		}
		until = System.currentTimeMillis() + 10_000;
		while (System.currentTimeMillis() < until && lines.queued() > 0) {
			Thread.sleep(100);
		}
		Thread.sleep(2500); // a cast's tries end within 2 s of its hit
		lines.shutdown(2000);
		casts.shutdown(1000);
		spotter.shutdown(1000);
		pipeline.shutdown(2000);
		turn.close();
		engine.close();
		say("Done.");
		System.exit(0);
	}

	/** Seconds into the WAV of a wall-clock instant: the device opened at its first sample. */
	private static String at(PlaybackDevice device, long millis) {
		return String.format(Locale.ROOT, "%.2f", (millis - device.openedAtMillis()) / 1000.0);
	}

	/** Kokoro saying each text, 1 s in and 2.5 s apart, kept as build/cast-check/said.wav. */
	private static Wav.Audio said(List<String> texts, int voice) throws Exception {
		SpeechTimeline t = new SpeechTimeline().silence(1.0);
		try (KokoroTakes takes = new KokoroTakes()) {
			for (String text : texts) {
				t.speech(text, List.of(), takes.say(text, voice)).silence(2.5);
			}
		}
		float[] at48k = t.at48k();
		Wav.write(Path.of("said.wav").toAbsolutePath(), at48k, SpeechTimeline.RATE);
		for (SpeechTimeline.Clip c : t.clips()) {
			say("  said.wav %.2f -> %.2f s: \"%s\"", SpeechTimeline.millisOf(c.start()) / 1000.0,
				SpeechTimeline.millisOf(c.end()) / 1000.0, c.label());
		}
		return new Wav.Audio(at48k, SpeechTimeline.RATE);
	}

	private static final class Options {
		boolean help;
		String wav;
		final List<String> said = new ArrayList<>();
		int voice;
		String bridge;
		String token;
		String player = "CastCheck";
		final List<Incantation> incantations = new ArrayList<>();
		double threshold = VfgConfig.DEFAULT_INCANTATION_THRESHOLD;
		double boost = VfgConfig.DEFAULT_INCANTATION_BOOST;
		double recheck = VfgConfig.DEFAULT_INCANTATION_RECHECK_SECONDS;

		static Options parse(String[] a) {
			Options o = new Options();
			for (int i = 0; i < a.length; i++) {
				String k = a[i];
				switch (k) {
					case "-h", "--help" -> o.help = true;
					case "--wav" -> o.wav = value(a, ++i, k);
					case "--say" -> o.said.add(value(a, ++i, k));
					case "--voice" -> o.voice = (int) number(value(a, ++i, k), k, 0, KokoroTakes.VOICES - 1);
					case "--bridge" -> o.bridge = value(a, ++i, k);
					case "--token" -> o.token = value(a, ++i, k);
					case "--player" -> o.player = value(a, ++i, k);
					case "--incantation" -> {
						String[] pa = value(a, ++i, k).split("=", 2);
						List<String> aliases = pa.length < 2 ? List.of()
							: List.of(pa[1].split("\\s*,\\s*")).stream().filter(s -> !s.isBlank()).toList();
						o.incantations.add(new Incantation(pa[0].trim(), aliases, 3));
					}
					case "--threshold" -> o.threshold = number(value(a, ++i, k), k, 0.05, 0.95);
					case "--boost" -> o.boost = number(value(a, ++i, k), k, 0.5, 5);
					case "--recheck" -> {
						o.recheck = number(value(a, ++i, k), k, 0, VfgConfig.MAX_INCANTATION_RECHECK_SECONDS);
						if (o.recheck > 0 && o.recheck < VfgConfig.MIN_INCANTATION_RECHECK_SECONDS) {
							throw new IllegalArgumentException(k + " must be 0 or 1-10");
						}
					}
					default -> throw new IllegalArgumentException("unknown option " + k);
				}
			}
			if (o.help) {
				return o;
			}
			if (o.bridge == null || !VfgConfig.isValidBridgeUrl(o.bridge) || o.bridge.isBlank()) {
				throw new IllegalArgumentException("--bridge must be an http:// or https:// URL");
			}
			if ((o.wav == null) == o.said.isEmpty()) {
				throw new IllegalArgumentException("give --wav or --say, not both");
			}
			if (!o.player.matches("[A-Za-z0-9_]{1,16}")) {
				throw new IllegalArgumentException("--player must be 1-16 letters, digits or _");
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
					throw new IllegalArgumentException(key + " must be " + min + "-" + max);
				}
				return d;
			} catch (NumberFormatException e) {
				throw new IllegalArgumentException(key + " must be a number");
			}
		}
	}
}
