package com.voiceforgaming.vfg;

import com.voiceforgaming.vfg.audio.MicCapture;
import com.voiceforgaming.vfg.audio.WindowListener.StopReason;
import com.voiceforgaming.vfg.config.VfgConfig;
import com.voiceforgaming.vfg.kws.CastGate;
import com.voiceforgaming.vfg.kws.SpeechTap;
import com.voiceforgaming.vfg.kws.Spotter;
import com.voiceforgaming.vfg.kws.SpotterVad;
import com.voiceforgaming.vfg.kws.SpotterWorker;
import com.voiceforgaming.vfg.kws.Vosk;
import com.voiceforgaming.vfg.kws.VoskGrammar;
import com.voiceforgaming.vfg.kws.VoskWorker;
import com.voiceforgaming.vfg.net.BridgeClient;
import com.voiceforgaming.vfg.net.CastClient;
import com.voiceforgaming.vfg.net.CastPayload;
import com.voiceforgaming.vfg.net.IncantationClient;
import com.voiceforgaming.vfg.net.LinePayload;
import com.voiceforgaming.vfg.session.SessionGate;
import com.voiceforgaming.vfg.session.StatusBoard;
import com.voiceforgaming.vfg.session.VoiceStatus;
import com.voiceforgaming.vfg.setup.AssetInstaller;
import com.voiceforgaming.vfg.setup.Assets;
import com.voiceforgaming.vfg.setup.EngineSlot;
import com.voiceforgaming.vfg.setup.ModelChoice;
import com.voiceforgaming.vfg.setup.TurnSetup;
import com.voiceforgaming.vfg.speech.Hotwords;
import com.voiceforgaming.vfg.speech.LineAssembler;
import com.voiceforgaming.vfg.speech.SpeechEngine;
import com.voiceforgaming.vfg.speech.Transcriber;
import com.voiceforgaming.vfg.speech.VoiceDetector;
import com.voiceforgaming.vfg.turn.TurnDetector;
import com.voiceforgaming.vfg.turn.TurnDetectors;
import java.io.IOException;
import java.nio.file.Path;
import java.util.Collection;
import java.util.List;
import java.util.Locale;
import java.util.Objects;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.atomic.AtomicBoolean;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Everything the mod does, wired together, without a Minecraft class: the config, the
 * setup thread, the speech engine and the turn model, the mic, the STT thread, the sender,
 * the session gate and the status board. {@link VfgClient} feeds it Fabric's events and
 * draws what it publishes.
 *
 * <p>The turn model is installed and loaded once the engine is ready, on the setup thread,
 * and swapped in when it is ({@link TurnSetup}, {@link EngineSlot}): voice never waits for it,
 * and until then lines end on silence alone. A download that fails is tried again in the
 * background; a model that will not load is not. Either costs only speed, and Voice settings
 * says which under the status.
 *
 * <p>The keyword spotter ({@link SpotterWorker}) is the incantations' pipeline beside the
 * sentences': once the engine has loaded the natives, its model is installed and loaded on a
 * thread of its own, {@code vfg-kws-setup}, outside the setup lock, so neither the turn model nor
 * a later setup waits for its download; while the mod listens it has the player's incantations
 * from the bridge ({@link IncantationClient}), and each cast it hears is posted on its own
 * ({@link CastClient}). A model that does not install or load leaves the spotter off until the
 * next setup; lines are not affected.
 *
 * <p>Vosk ({@link VoskWorker}) listens for the same incantations beside the spotter, on its own
 * thread: its model is installed and loaded on {@code vfg-kws-setup} after the spotter's, and one
 * that does not install or load leaves the spotter alone, with one log line. The two share a
 * {@link CastGate}, so a phrase either hears casts once.
 *
 * <p>The speech model is chosen by {@link ModelChoice}: Parakeet TDT 0.6B v2 by default, which falls
 * back to 110M when it will not load; while it downloads (482 MB), the 110M listens, and the 0.6B
 * is swapped into the running engine on {@code vfg-stt-upgrade} once it is in. A Parakeet model
 * decodes with the {@link Hotwords} unless {@code hotwords} is off: the players in the tab list
 * ({@link #onPlayers}) and the server's incantation phrases, from the bridge.
 */
public final class VoiceService {
	private static final Logger LOG = LoggerFactory.getLogger("vfg");

	private enum MicMode { OFF, LISTEN, TEST }

	private final Path configFile;
	private final Path vfgRoot;
	private final UUID playerUuid;
	private final String playerName;
	private volatile VfgConfig config;

	private final StatusBoard status = new StatusBoard();
	private final EngineSlot<SpeechEngine, TurnDetector> slot = new EngineSlot<>();
	private final TurnSetup turnSetup = new TurnSetup(status, Thread::sleep);
	private final Hotwords hotwords = new Hotwords();
	private final ModelChoice modelChoice;
	private final Object upgradeLock = new Object();
	private Thread upgradeThread;
	private volatile String requestedModel;
	private volatile boolean requestedHotwords;
	private final AtomicBoolean spotterSetup = new AtomicBoolean();
	private final AtomicBoolean voskWarned = new AtomicBoolean();
	private volatile boolean stopping;
	private Thread setupThread;
	private final Object setupLock = new Object();

	private final VoicePipeline pipeline;
	private final BridgeClient sender;
	private final SessionGate gate;
	private final SpotterWorker spotter;
	private final VoskWorker vosk;
	private final SpeechTap speechTap;
	private final CastClient casts;
	private final IncantationClient incantations;

	// What the mic is doing, decided from the gate and the settings screen's mic test.
	private MicMode micMode = MicMode.OFF;
	private String micDevice = "";
	private boolean gateListening;
	private StopReason pendingStop = StopReason.DISCARD;
	private boolean testRequested;
	private String testDevice = "";

	public VoiceService(Path gameDir, Path configDir, UUID playerUuid, String playerName, SessionGate.Notices notices) {
		this.configFile = configDir.resolve(VfgConfig.FILE_NAME);
		this.vfgRoot = Assets.root(gameDir);
		this.modelChoice = new ModelChoice(vfgRoot);
		this.playerUuid = playerUuid;
		this.playerName = playerName;
		this.config = VfgConfig.load(configFile);
		LOG.info("config: {}", config);

		CastGate castGate = new CastGate();
		this.spotter = new SpotterWorker(config.incantations, config.incantationThreshold, config.incantationBoost,
			config.incantationRecheckSeconds, castGate, this::castHeard);
		spotter.maxAliases(config.incantationSpotterAliases);
		this.vosk = new VoskWorker(config.incantations && config.vosk, castGate, this::castHeard);
		vosk.confirmSayings(config.voskConfirm);
		this.speechTap = new SpeechTap(() -> spotter.listening() || vosk.listening(), spotter, vosk);
		this.pipeline = new VoicePipeline(MicCapture.OPENAL, () -> (VoiceDetector) slot.engine(),
			() -> (Transcriber) slot.engine(), slot::turn, config.turnThreshold, config.silenceSeconds,
			System::currentTimeMillis, () -> config.logText, new VoicePipeline.Events() {
				@Override
				public void line(LineAssembler.Line line) {
					lineClosed(line);
				}

				@Override
				public void heard(String text) {
					status.heard(text);
				}

				@Override
				public void live(boolean speaking, float livePeak, boolean lineOpen) {
					status.live(speaking, livePeak, lineOpen);
				}

				@Override
				public void micError(String error) {
					status.micError(error);
				}
			}, speechTap);
		this.sender = new BridgeClient(this::target, new BridgeClient.Listener() {
			@Override
			public void delivered(String clientLineId, String id) {
				status.tokenRejected(false);
			}

			@Override
			public void tokenRejected() {
				status.tokenRejected(true);
				gate.onTokenRejected();
			}

			@Override
			public void deliveryProblem(boolean failing) {
				status.deliveryProblem(failing);
			}
		}, () -> config.logText);
		this.gate = new SessionGate(() -> config, () -> status.get().setup() == VoiceStatus.Setup.READY,
			new SessionGate.Listening() {
				@Override
				public void start() {
					setGateListening(true, StopReason.DISCARD);
				}

				@Override
				public void stop(StopReason reason) {
					setGateListening(false, reason);
				}
			}, notices, status);
		this.casts = new CastClient(() -> new CastClient.Target(config.castsUrl(), config.effectiveClientToken()),
			new CastClient.Listener() {
				@Override
				public void tokenRejected() {
					status.tokenRejected(true);
					gate.onTokenRejected();
				}
			});
		this.incantations = new IncantationClient(
			() -> new IncantationClient.Target(config.incantationsUrl(), config.effectiveClientToken()),
			(version, list, serverPhrases) -> {
				LOG.info("incantations: {} for {} (version {}); hotwords: {}", list.size(), this.playerName, version,
					serverPhrases == null ? "the player's own phrases (the bridge sends no server_phrases)"
						: serverPhrases.size() + " phrases on the server");
				spotter.setIncantations(list);
				vosk.setIncantations(list);
				hotwords.phrases(serverPhrases, list);
			});
	}

	/** {@code CLIENT_STARTED}: start the worker threads and setup. */
	public void start() {
		pipeline.start();
		spotter.start();
		vosk.start();
		sender.start();
		startSetup(config.model);
	}

	/** {@code CLIENT_STOPPING}: close the mic, stop the threads (the sender gets 2 s), release the engine. */
	public void stop() {
		stopping = true;
		pipeline.shutdown(1000);
		incantations.stop();
		spotter.shutdown(1000);
		vosk.shutdown(1000);
		speechTap.close();
		casts.shutdown(500);
		sender.shutdown(2000);
		Thread s;
		Thread u;
		synchronized (this) {
			s = setupThread;
			u = upgradeThread;
		}
		if (s != null) {
			s.interrupt();
		}
		if (u != null) {
			u.interrupt();
		}
		modelChoice.end(); // a clean stop: whatever was loading did not crash the game
		slot.release();
	}

	// --- reading ---

	public VoiceStatus status() {
		return status.get();
	}

	public VfgConfig config() {
		return config;
	}

	public boolean listening() {
		return gate.listening();
	}

	/** The joined server's address while on a multiplayer server; {@code null} otherwise. */
	public String joinedAddress() {
		return gate.joinedAddress();
	}

	public List<String> devices() {
		return MicCapture.listDevices();
	}

	// --- Minecraft events ---

	public void onJoin(String address) {
		gate.onJoin(address);
	}

	public void onLeave() {
		gate.onLeave();
		hotwords.players(List.of());
	}

	/**
	 * The names in the client's tab list, the players online, for the hotwords; on the client thread,
	 * as often as it likes: an unchanged list costs a comparison.
	 */
	public void onPlayers(Collection<String> names) {
		if (hotwords.players(names)) {
			LOG.debug("hotwords: {} players online", hotwords.players().size());
		}
	}

	/** The hotwords as they are now (Voice settings, tests). */
	public Hotwords hotwords() {
		return hotwords;
	}

	public void toggleMute() {
		VfgConfig c = config.copy();
		c.muted = !c.muted;
		c.saveQuietly(configFile);
		config = c;
		gate.onMuteToggled();
	}

	// --- settings screen ---

	/** Saves and applies new settings (Done). */
	public void applySettings(VfgConfig next) throws IOException {
		VfgConfig before = config;
		next.save(configFile);
		config = next;
		if (!Objects.equals(before.effectiveClientToken(), next.effectiveClientToken())) {
			status.tokenRejected(false);
		}
		if (!Objects.equals(before.micDevice, next.micDevice)) {
			changeDevice(next.micDevice);
		}
		spotter.configure(next.incantations, next.incantationThreshold, next.incantationBoost,
			next.incantationRecheckSeconds);
		spotter.maxAliases(next.incantationSpotterAliases);
		vosk.configure(next.incantations && next.vosk);
		vosk.confirmSayings(next.voskConfirm);
		if ((!Objects.equals(next.model, requestedModel) || next.hotwords != requestedHotwords)
				&& status.get().setup() != VoiceStatus.Setup.UNSUPPORTED) {
			startSetup(next.model);
		}
		gate.recheck();
	}

	/** The status line's Retry. */
	public void retrySetup() {
		startSetup(config.model);
	}

	/** The settings screen's Test mic: on only while the screen is open. Sends nothing. */
	public synchronized void setTestMic(boolean on, String device) {
		testRequested = on;
		testDevice = device == null ? "" : device;
		updateMic();
	}

	/** Test connection with the screen's values, before they are saved. */
	public CompletableFuture<BridgeClient.ConnectionResult> testConnection(String bridgeUrl, String serverAddress,
			String token) {
		String url = VfgConfig.linesUrl(bridgeUrl, serverAddress);
		if (url == null) {
			return CompletableFuture.completedFuture(
				new BridgeClient.ConnectionResult(BridgeClient.ConnectionCheck.UNREACHABLE, 0));
		}
		return BridgeClient.testConnection(url, token);
	}

	// --- internals ---

	private BridgeClient.Target target() {
		VfgConfig c = config;
		return new BridgeClient.Target(c.linesUrl(), c.effectiveClientToken());
	}

	/** On {@code vfg-kws}: a cast heard; posted on its own thread. */
	private void castHeard(SpotterWorker.Cast cast) {
		CastPayload payload = CastPayload.create(playerUuid, playerName, cast.phrase(), cast.heard(),
			cast.startedAtMillis(), cast.endedAtMillis());
		casts.post(payload, cast.heardAtMillis(), cast.via().words());
	}

	private void lineClosed(LineAssembler.Line line) {
		LinePayload payload = LinePayload.create(playerUuid, playerName, line.text(), line.startedAtMillis(),
			line.endedAtMillis(), line.peakDbfs(), line.meanDbfs());
		LOG.debug("line {} closed ({} characters)", payload.clientLineId(), payload.text().length());
		sender.enqueue(payload, line.closedAtMillis());
	}

	private synchronized void setGateListening(boolean on, StopReason reason) {
		gateListening = on;
		if (!on) {
			pendingStop = reason;
		}
		updateMic();
		if (on) {
			incantations.start(playerName);
		} else {
			incantations.stop();
			spotter.setIncantations(List.of());
			vosk.setIncantations(List.of());
			hotwords.phrases(null, List.of());
		}
	}

	private synchronized void changeDevice(String device) {
		if (micMode == MicMode.LISTEN && !Objects.equals(micDevice, device)) {
			micDevice = device;
			pipeline.switchDevice(device);
		}
	}

	/** Opens, closes or switches the mic to match the gate and the mic test. */
	private synchronized void updateMic() {
		MicMode want = gateListening ? MicMode.LISTEN : testRequested ? MicMode.TEST : MicMode.OFF;
		String device = want == MicMode.TEST ? testDevice : config.micDevice;
		if (want == micMode) {
			if (want == MicMode.TEST && !Objects.equals(device, micDevice)) {
				micDevice = device;
				pipeline.switchDevice(device);
			}
			return;
		}
		if (micMode == MicMode.LISTEN) {
			pipeline.stopCapture(pendingStop);
		} else if (micMode == MicMode.TEST) {
			pipeline.stopCapture(StopReason.TEST_ENDED);
		}
		micMode = want;
		micDevice = device;
		if (want == MicMode.LISTEN) {
			pipeline.startCapture(device, true);
		} else if (want == MicMode.TEST) {
			pipeline.startCapture(device, false);
		}
	}

	private void startSetup(String model) {
		requestedModel = model;
		requestedHotwords = config.hotwords;
		status.setupRunning(0);
		gate.recheck(); // stops listening until the engine is ready again; never called holding this lock
		synchronized (this) {
			if (setupThread != null && setupThread.isAlive()) {
				setupThread.interrupt();
			}
			if (upgradeThread != null) {
				upgradeThread.interrupt();
				upgradeThread = null;
			}
			Thread t = new Thread(() -> runSetup(model), "vfg-setup");
			t.setDaemon(true);
			setupThread = t;
			t.start();
		}
	}

	private void runSetup(String model) {
		synchronized (setupLock) { // a replaced setup finishes or gives up before the next one starts
			if (!Thread.currentThread().isInterrupted()) {
				runSetupLocked(model);
			}
		}
	}

	private void runSetupLocked(String model) {
		String platform = Assets.currentPlatform();
		if (platform == null) {
			LOG.warn("unsupported platform: {} {}", System.getProperty("os.name"), System.getProperty("os.arch"));
			finishSetup(VoiceStatus.Setup.UNSUPPORTED, "unsupported platform");
			return;
		}
		VfgConfig chosen = config;
		Assets.Asset natives = Assets.natives(platform);
		Assets.Asset vad = Assets.vad();
		ModelChoice.Plan plan = modelChoice.plan(model);
		if (plan.crashed()) {
			modelChoice.end();
			LOG.warn("speech engine: {} began loading {} times in a row and never finished (the game closed, or ran "
				+ "out of memory); {} is chosen now, in Voice settings: pick {} again to try it", model, plan.unfinished(),
				plan.loadNow(), model);
			VfgConfig next = config.copy();
			next.model = plan.loadNow();
			next.saveQuietly(configFile);
			config = next;
			requestedModel = plan.loadNow();
			chosen = next;
		}
		VfgConfig c = chosen;
		Assets.Asset modelAsset = Assets.model(plan.loadNow());
		AssetInstaller installer = new AssetInstaller(vfgRoot,
			(done, total) -> status.setupRunning(total <= 0 ? 0 : (int) (done * 100 / total)));
		try {
			installer.installAll(List.of(natives, vad, modelAsset));
		} catch (AssetInstaller.SetupException e) {
			LOG.warn("setup failed: {}", e.getMessage());
			finishSetup(VoiceStatus.Setup.SETUP_FAILED, e.getMessage());
			return;
		} catch (InterruptedException e) {
			return;
		}
		if (Thread.currentThread().isInterrupted()) {
			return;
		}
		slot.release();
		SpeechEngine loaded;
		VoiceStatus.Stt stt = new VoiceStatus.Stt(plan.loadNow(), plan.crashed() ? VoiceStatus.Stt.Note.CRASHED
			: plan.upgradeTo() != null ? VoiceStatus.Stt.Note.UPGRADING : VoiceStatus.Stt.Note.NONE, 0, null);
		try {
			loaded = loadEngine(natives, vad, plan.loadNow(), c);
		} catch (Throwable t) {
			String message = message(t);
			if (!plan.loadNow().equals(Assets.PARAKEET_06B)) {
				engineFailed(message, t);
				return;
			}
			// The 0.6B would not load (out of memory, a broken file): the 110M stands in.
			LOG.warn("speech engine: {} failed to load ({}); falling back to {}", plan.loadNow(), message,
				Assets.PARAKEET_110M);
			try {
				new AssetInstaller(vfgRoot, (done, total) -> status.setupRunning(total <= 0 ? 0 : (int) (done * 100 / total)))
					.install(Assets.model(Assets.PARAKEET_110M));
				loaded = loadEngine(natives, vad, Assets.PARAKEET_110M, c);
				stt = new VoiceStatus.Stt(Assets.PARAKEET_110M, VoiceStatus.Stt.Note.FALLBACK, 0, message);
			} catch (AssetInstaller.SetupException e) {
				LOG.warn("setup failed: {}", e.getMessage());
				finishSetup(VoiceStatus.Setup.SETUP_FAILED, e.getMessage());
				return;
			} catch (InterruptedException e) {
				return;
			} catch (Throwable t2) {
				engineFailed(message(t2), t2);
				return;
			}
		}
		slot.put(loaded);
		status.stt(stt);
		status.turnModel(c.turnModel.equals(TurnDetectors.OFF) ? VoiceStatus.TurnModel.OFF
			: VoiceStatus.TurnModel.LOADING, null);
		finishSetup(VoiceStatus.Setup.READY, null);
		if (plan.upgradeTo() != null) {
			startUpgrade(plan.upgradeTo(), plan.loadNow(), loaded, c);
		}
		boolean wantVosk = c.vosk && !vosk.hasModel();
		if (c.incantations && (!spotter.hasModel() || wantVosk) && spotterSetup.compareAndSet(false, true)) {
			Path vadFile = Assets.folder(vfgRoot, vad).resolve(Assets.VAD_FILE);
			Thread t = new Thread(() -> {
				try {
					if (!speechTap.hasVad()) {
						setUpSpeechVad(natives, vadFile, c.vadThreshold);
					}
					if (!spotter.hasModel()) {
						setUpSpotter(natives, c.incantationPaths);
					}
					if (wantVosk && !stopping) {
						setUpVosk(natives);
					}
				} finally {
					spotterSetup.set(false);
				}
			}, "vfg-kws-setup");
			t.setDaemon(true);
			t.start();
		}
		setUpTurn(c.turnModel, natives, loaded);
	}

	/**
	 * Loads the engine for {@code model} (with the hotwords, for Parakeet, unless they are off),
	 * marking the 0.6B's load as begun until it returns or throws ({@link ModelChoice}).
	 */
	private SpeechEngine loadEngine(Assets.Asset natives, Assets.Asset vad, String model, VfgConfig c) {
		Assets.Asset asset = Assets.model(model);
		boolean mark = ModelChoice.marked(model);
		if (mark) {
			modelChoice.begin(model);
		}
		try {
			long t0 = System.nanoTime();
			SpeechEngine e = SpeechEngine.load(Assets.folder(vfgRoot, natives), Assets.isParakeet(asset),
				Assets.folder(vfgRoot, asset), Assets.folder(vfgRoot, vad).resolve(Assets.VAD_FILE), c.sttThreads,
				(float) c.vadThreshold, c.hotwords && Assets.isParakeet(asset) ? hotwords : null, boosts(model));
			LOG.info("speech engine loaded ({}, {}, {} ms)", model, e.hotwords() ? "hotwords" : "greedy",
				(System.nanoTime() - t0) / 1_000_000);
			return e;
		} finally {
			if (mark) {
				modelChoice.end();
			}
		}
	}

	/** A model's own hotword boosts: the 0.6B's ({@link Hotwords#PARAKEET_06B}), else the hotwords' own. */
	static Hotwords.Settings boosts(String model) {
		return Assets.PARAKEET_06B.equals(model) ? Hotwords.PARAKEET_06B : null;
	}

	private static String message(Throwable t) {
		return t.getMessage() == null ? t.getClass().getSimpleName() : t.getMessage();
	}

	private void engineFailed(String message, Throwable t) {
		if (Assets.isWindows()) {
			message += "; install the Microsoft Visual C++ Redistributable (x64)";
		}
		LOG.error("engine failed: {}", message, t);
		finishSetup(VoiceStatus.Setup.ENGINE_FAILED, message);
	}

	private void startUpgrade(String model, String meanwhile, SpeechEngine engine, VfgConfig c) {
		Thread t = new Thread(() -> upgrade(model, meanwhile, engine, c), "vfg-stt-upgrade");
		t.setDaemon(true);
		synchronized (this) {
			if (upgradeThread != null) {
				upgradeThread.interrupt();
			}
			upgradeThread = t;
		}
		t.start();
	}

	/**
	 * On {@code vfg-stt-upgrade}: downloads {@code model} while {@code meanwhile} listens (a failed
	 * download is tried again after 30 s, 2 min, then every 10 min, carrying on where it stopped),
	 * loads its recognizer and swaps it into {@code engine}; one that will not load leaves
	 * {@code meanwhile}, with a log line and a note. A replaced or stopped setup ends it.
	 */
	private void upgrade(String model, String meanwhile, SpeechEngine engine, VfgConfig c) {
		synchronized (upgradeLock) { // a replaced upgrade finishes its download first; the next finds it there
			Assets.Asset asset = Assets.model(model);
			AssetInstaller installer = new AssetInstaller(vfgRoot, (done, total) -> {
				if (!Thread.currentThread().isInterrupted()) {
					status.stt(new VoiceStatus.Stt(meanwhile, VoiceStatus.Stt.Note.UPGRADING,
						total <= 0 ? 0 : (int) (done * 100 / total), null));
				}
			});
			LOG.info("speech engine: {} listens while {} downloads ({} MB)", meanwhile, model, asset.bytes() / 1_000_000);
			try {
				for (int failures = 0; ; failures++) {
					if (Thread.currentThread().isInterrupted() || stopping) {
						return;
					}
					try {
						installer.install(asset);
						break;
					} catch (AssetInstaller.SetupException e) {
						long wait = TurnSetup.RETRY_WAITS_MILLIS[Math.min(failures, TurnSetup.RETRY_WAITS_MILLIS.length - 1)];
						LOG.warn("speech engine: {} not downloaded ({}); {} listens, trying again in {} s", model,
							e.getMessage(), meanwhile, wait / 1000);
						status.stt(new VoiceStatus.Stt(meanwhile, VoiceStatus.Stt.Note.UPGRADE_RETRYING, 0, e.getMessage()));
						Thread.sleep(wait);
					}
				}
			} catch (InterruptedException e) {
				return;
			}
			if (Thread.currentThread().isInterrupted() || stopping) {
				return;
			}
			Path dir = Assets.folder(vfgRoot, asset);
			modelChoice.begin(model);
			try {
				long t0 = System.nanoTime();
				SpeechEngine.Recognition r = SpeechEngine.recognition(dir, true, c.sttThreads, c.hotwords, boosts(model));
				if (!engine.replaceRecognizer(r)) {
					return; // the engine was released meanwhile
				}
				LOG.info("speech engine: {} in, in place of {} ({}, {} ms)", model, meanwhile,
					r.hotwords() ? "hotwords" : "greedy", (System.nanoTime() - t0) / 1_000_000);
				status.stt(new VoiceStatus.Stt(model, VoiceStatus.Stt.Note.NONE, 0, null));
			} catch (Throwable t) {
				LOG.warn("speech engine: {} failed to load ({}); {} stays", model, message(t), meanwhile);
				status.stt(new VoiceStatus.Stt(meanwhile, VoiceStatus.Stt.Note.FALLBACK, 0, message(t)));
			} finally {
				modelChoice.end();
			}
		}
	}

	/**
	 * Loads the VAD that says for the spotter and Vosk when someone speaks ({@link SpeechTap}), on
	 * {@code vfg-kws-setup}, from {@code vadFile}, the file the engine's VAD loaded; one that will not
	 * load leaves them on the lines' VAD, with a log line.
	 */
	private void setUpSpeechVad(Assets.Asset natives, Path vadFile, double vadThreshold) {
		try {
			SpotterVad loaded = Spotter.vad(Assets.folder(vfgRoot, natives), vadFile, vadThreshold);
			if (stopping) {
				loaded.close();
				return;
			}
			speechTap.setVad(loaded);
		} catch (Throwable e) { // an Error from the natives too: the capture's VAD will do
			LOG.warn("keyword spotter's VAD failed to load ({}); it hears speech end with the lines' VAD", e.toString());
		}
	}

	/**
	 * Installs and loads the keyword model on {@code vfg-kws-setup}, once the engine has loaded the
	 * natives it runs on; a model that will not install or load leaves the spotter off, with a log
	 * line. It touches only its own folder and the worker, so it takes no setup lock.
	 */
	private void setUpSpotter(Assets.Asset natives, int maxActivePaths) {
		Assets.Asset asset = Assets.kws();
		try {
			new AssetInstaller(vfgRoot, null).install(asset);
			long t0 = System.nanoTime();
			Spotter loaded = Spotter.load(Assets.folder(vfgRoot, natives), Assets.folder(vfgRoot, asset),
				Spotter.TRAILING_BLANKS, maxActivePaths);
			if (stopping) {
				loaded.close();
				return;
			}
			spotter.setModel(loaded); // speech comes from the SpeechTap's VAD
			LOG.info("keyword spotter loaded ({} ms, max_active_paths {})", (System.nanoTime() - t0) / 1_000_000,
				maxActivePaths);
		} catch (InterruptedException e) {
			Thread.currentThread().interrupt();
		} catch (AssetInstaller.SetupException e) {
			LOG.warn("keyword spotter not installed ({}); incantations are heard in lines only", e.getMessage());
		} catch (Throwable t) {
			LOG.warn("keyword spotter failed to load ({}); incantations are heard in lines only", t.toString());
		}
	}

	/**
	 * Installs and loads Vosk's library for this PC and its model on {@code vfg-kws-setup}, after the
	 * spotter's; speech comes from the {@link SpeechTap}'s VAD, as the spotter's does. A library or
	 * model that will not install or load leaves the spotter alone, said once a game.
	 */
	private void setUpVosk(Assets.Asset natives) {
		Assets.Asset voskNatives = Assets.voskNatives(natives.variant());
		Assets.Asset asset = Assets.vosk();
		try {
			AssetInstaller installer = new AssetInstaller(vfgRoot, null);
			installer.install(voskNatives);
			installer.install(asset);
			long t0 = System.nanoTime();
			Vosk loaded = Vosk.load(Assets.folder(vfgRoot, voskNatives), Assets.folder(vfgRoot, asset));
			if (stopping) {
				loaded.close();
				return;
			}
			vosk.setModel(loaded, null, VoskGrammar.fillers());
			LOG.info("vosk loaded ({} ms)", (System.nanoTime() - t0) / 1_000_000);
		} catch (InterruptedException e) {
			Thread.currentThread().interrupt();
		} catch (AssetInstaller.SetupException e) {
			if (voskWarned.compareAndSet(false, true)) {
				LOG.warn("vosk not installed ({}); incantations are heard by the keyword spotter alone", e.getMessage());
			}
		} catch (Throwable t) { // an UnsatisfiedLinkError or ExceptionInInitializerError from the natives too
			if (voskWarned.compareAndSet(false, true)) {
				LOG.warn("vosk failed to load ({}); incantations are heard by the keyword spotter alone", t.toString());
			}
		}
	}

	/**
	 * Installs and loads the turn model once {@code beside} is loaded and setup is READY, still
	 * on the setup thread and under its lock, then swaps it in; a failed download is tried again
	 * here until it works or this setup is replaced or stopped.
	 */
	private void setUpTurn(String turnModel, Assets.Asset natives, SpeechEngine beside) {
		if (turnModel.equals(TurnDetectors.OFF)) {
			return;
		}
		Assets.Asset asset = Assets.turn(turnModel);
		AssetInstaller installer = new AssetInstaller(vfgRoot, null); // no progress: setup already says READY
		turnSetup.run(turnModel, () -> installer.install(asset),
			() -> TurnDetectors.load(turnModel, Assets.folder(vfgRoot, natives), Assets.turnFile(vfgRoot, asset)), slot,
			beside);
	}

	private void finishSetup(VoiceStatus.Setup state, String error) {
		status.setup(state, error);
		gate.recheck();
	}
}
