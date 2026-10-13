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
import com.voiceforgaming.vfg.setup.TurnSetup;
import com.voiceforgaming.vfg.speech.LineAssembler;
import com.voiceforgaming.vfg.speech.SpeechEngine;
import com.voiceforgaming.vfg.speech.Transcriber;
import com.voiceforgaming.vfg.speech.VoiceDetector;
import com.voiceforgaming.vfg.turn.TurnDetector;
import com.voiceforgaming.vfg.turn.TurnDetectors;
import java.io.IOException;
import java.nio.file.Path;
import java.util.List;
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
	private volatile String requestedModel;
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
			(version, list) -> {
				LOG.info("incantations: {} for {} (version {})", list.size(), this.playerName, version);
				spotter.setIncantations(list);
				vosk.setIncantations(list);
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
		synchronized (this) {
			s = setupThread;
		}
		if (s != null) {
			s.interrupt();
		}
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
		if (!Objects.equals(next.model, requestedModel) && status.get().setup() != VoiceStatus.Setup.UNSUPPORTED) {
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
		status.setupRunning(0);
		gate.recheck(); // stops listening until the engine is ready again; never called holding this lock
		synchronized (this) {
			if (setupThread != null && setupThread.isAlive()) {
				setupThread.interrupt();
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
		VfgConfig c = config;
		Assets.Asset natives = Assets.natives(platform);
		Assets.Asset vad = Assets.vad();
		Assets.Asset modelAsset = Assets.model(model);
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
		try {
			long t0 = System.nanoTime();
			loaded = SpeechEngine.load(Assets.folder(vfgRoot, natives), Assets.isParakeet(modelAsset),
				Assets.folder(vfgRoot, modelAsset), Assets.folder(vfgRoot, vad).resolve(Assets.VAD_FILE), c.sttThreads,
				(float) c.vadThreshold);
			slot.put(loaded);
			LOG.info("speech engine loaded ({}, {} ms)", model, (System.nanoTime() - t0) / 1_000_000);
			status.turnModel(c.turnModel.equals(TurnDetectors.OFF) ? VoiceStatus.TurnModel.OFF
				: VoiceStatus.TurnModel.LOADING, null);
			finishSetup(VoiceStatus.Setup.READY, null);
		} catch (Throwable t) {
			String message = t.getMessage() == null ? t.getClass().getSimpleName() : t.getMessage();
			if (Assets.isWindows()) {
				message += "; install the Microsoft Visual C++ Redistributable (x64)";
			}
			LOG.error("engine failed: {}", message, t);
			finishSetup(VoiceStatus.Setup.ENGINE_FAILED, message);
			return;
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
