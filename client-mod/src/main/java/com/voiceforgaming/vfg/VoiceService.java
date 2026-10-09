package com.voiceforgaming.vfg;

import com.voiceforgaming.vfg.audio.MicCapture;
import com.voiceforgaming.vfg.audio.WindowListener.StopReason;
import com.voiceforgaming.vfg.config.VfgConfig;
import com.voiceforgaming.vfg.net.BridgeClient;
import com.voiceforgaming.vfg.net.LinePayload;
import com.voiceforgaming.vfg.session.SessionGate;
import com.voiceforgaming.vfg.session.StatusBoard;
import com.voiceforgaming.vfg.session.VoiceStatus;
import com.voiceforgaming.vfg.setup.AssetInstaller;
import com.voiceforgaming.vfg.setup.Assets;
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
import java.util.concurrent.atomic.AtomicReference;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Everything the mod does, wired together, without a Minecraft class: the config, the
 * setup thread, the speech engine and the turn model, the mic, the STT thread, the sender,
 * the session gate and the status board. {@link VfgClient} feeds it Fabric's events and
 * draws what it publishes.
 *
 * <p>The turn model is installed with the engine, under the same progress, and loaded
 * after it, on the runtime it loaded. One that will not download or load costs only
 * speed: it is logged once, and lines end on silence alone.
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
	private final AtomicReference<SpeechEngine> engine = new AtomicReference<>();
	private final AtomicReference<TurnDetector> turn = new AtomicReference<>();
	private volatile String requestedModel;
	private Thread setupThread;
	private final Object setupLock = new Object();

	private final VoicePipeline pipeline;
	private final BridgeClient sender;
	private final SessionGate gate;

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

		this.pipeline = new VoicePipeline(MicCapture.OPENAL, () -> (VoiceDetector) engine.get(),
			() -> (Transcriber) engine.get(), turn::get, config.turnThreshold, config.silenceSeconds,
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
			});
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
	}

	/** {@code CLIENT_STARTED}: start the worker threads and setup. */
	public void start() {
		pipeline.start();
		sender.start();
		startSetup(config.model);
	}

	/** {@code CLIENT_STOPPING}: close the mic, stop the threads (the sender gets 2 s), release the engine. */
	public void stop() {
		pipeline.shutdown(1000);
		sender.shutdown(2000);
		Thread s;
		synchronized (this) {
			s = setupThread;
		}
		if (s != null) {
			s.interrupt();
		}
		releaseEngine();
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
		Assets.Asset turnAsset = Assets.turn(c.turnModel);
		List<Assets.Asset> speech = List.of(natives, vad, modelAsset);
		long speechBytes = speech.stream().mapToLong(Assets.Asset::bytes).sum();
		long allBytes = speechBytes + (turnAsset == null ? 0 : turnAsset.bytes());
		long[] doneBefore = {0};
		AssetInstaller installer = new AssetInstaller(vfgRoot,
			(done, total) -> status.setupRunning((int) ((doneBefore[0] + done) * 100 / allBytes)));
		try {
			installer.installAll(speech);
		} catch (AssetInstaller.SetupException e) {
			LOG.warn("setup failed: {}", e.getMessage());
			finishSetup(VoiceStatus.Setup.SETUP_FAILED, e.getMessage());
			return;
		} catch (InterruptedException e) {
			return;
		}
		boolean turnInstalled = false;
		if (turnAsset != null) {
			doneBefore[0] = speechBytes;
			try {
				installer.installAll(List.of(turnAsset));
				turnInstalled = true;
			} catch (AssetInstaller.SetupException | RuntimeException e) {
				LOG.warn("turn model {} not installed ({}); lines end on silence alone", c.turnModel, e.getMessage());
			} catch (InterruptedException e) {
				return;
			}
		} else if (!c.turnModel.equals(TurnDetectors.OFF)) {
			LOG.warn("turn model {} has no download; lines end on silence alone", c.turnModel);
		}
		if (Thread.currentThread().isInterrupted()) {
			return;
		}
		releaseEngine();
		try {
			long t0 = System.nanoTime();
			SpeechEngine loaded = SpeechEngine.load(Assets.folder(vfgRoot, natives), Assets.isParakeet(modelAsset),
				Assets.folder(vfgRoot, modelAsset), Assets.folder(vfgRoot, vad).resolve(Assets.VAD_FILE), c.sttThreads,
				(float) c.vadThreshold);
			LOG.info("speech engine loaded ({}, {} ms)", model, (System.nanoTime() - t0) / 1_000_000);
			turn.set(turnInstalled ? loadTurn(c.turnModel, natives, turnAsset) : null);
			engine.set(loaded);
			finishSetup(VoiceStatus.Setup.READY, null);
		} catch (Throwable t) {
			String message = t.getMessage() == null ? t.getClass().getSimpleName() : t.getMessage();
			if (Assets.isWindows()) {
				message += "; install the Microsoft Visual C++ Redistributable (x64)";
			}
			LOG.error("engine failed: {}", message, t);
			finishSetup(VoiceStatus.Setup.ENGINE_FAILED, message);
		}
	}

	/**
	 * Loads the turn model on the ONNX Runtime the engine has just loaded; {@code null}, with
	 * one warning, when it will not load.
	 */
	private TurnDetector loadTurn(String turnModel, Assets.Asset natives, Assets.Asset turnAsset) {
		try {
			long t0 = System.nanoTime();
			TurnDetector loaded = TurnDetectors.load(turnModel, Assets.folder(vfgRoot, natives),
				Assets.turnFile(vfgRoot, turnAsset));
			LOG.info("turn model loaded ({}, {} ms)", turnModel, (System.nanoTime() - t0) / 1_000_000);
			return loaded;
		} catch (Throwable t) {
			LOG.warn("turn model {} failed to load ({}); lines end on silence alone", turnModel,
				t.getMessage() == null ? t.getClass().getSimpleName() : t.getMessage());
			return null;
		}
	}

	/** Releases the turn model, then the engine whose runtime it runs on. */
	private void releaseEngine() {
		TurnDetector oldTurn = turn.getAndSet(null);
		if (oldTurn != null) {
			try {
				oldTurn.close();
			} catch (RuntimeException e) {
				LOG.warn("closing the turn model: {}", e.toString());
			}
		}
		SpeechEngine old = engine.getAndSet(null);
		if (old != null) {
			old.close();
		}
	}

	private void finishSetup(VoiceStatus.Setup state, String error) {
		status.setup(state, error);
		gate.recheck();
	}
}
