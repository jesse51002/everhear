package com.voiceforgaming.vfg;

import com.voiceforgaming.vfg.audio.MicCapture;
import com.voiceforgaming.vfg.audio.WindowListener;
import com.voiceforgaming.vfg.audio.WindowListener.StopReason;
import com.voiceforgaming.vfg.speech.CaptureProcessor;
import com.voiceforgaming.vfg.speech.LineAssembler;
import com.voiceforgaming.vfg.speech.SttWorker;
import com.voiceforgaming.vfg.speech.Transcriber;
import com.voiceforgaming.vfg.speech.VoiceDetector;
import com.voiceforgaming.vfg.turn.TurnDetector;
import java.util.function.BooleanSupplier;
import java.util.function.LongSupplier;
import java.util.function.Supplier;

/**
 * Speech in, lines out: the mic ({@code vfg-capture}) → the VAD, the sample clock and the
 * last 9 s of audio → speech-to-text and the turn model ({@code vfg-stt}) → the line
 * assembler. The mod runs exactly this, and so do {@code ./gradlew micCheck} and
 * {@code ./gradlew speechTest}; nothing here knows about Minecraft, the session or the bridge.
 *
 * <p>A {@link Tap} hears every window too, after the VAD: the keyword spotter, a pipeline of
 * its own that the sentence pipeline never waits on.
 */
public final class VoicePipeline {
	/** What the pipeline reports. {@code live} is called on {@code vfg-capture}, the rest on {@code vfg-stt}. */
	public interface Events {
		/**
		 * A line closed: the turn model heard it finished, silence ran out, it hit a cap, or
		 * capture stopped for leaving.
		 */
		default void line(LineAssembler.Line line) {}

		/**
		 * A decision on a line's end, in the words it is logged with, just before the line it
		 * closes: the turn model's score and whether the line closed or waits, or the silence
		 * that closed it. Never the text.
		 */
		default void decided(String decision) {}

		/** A segment's text, cleaned, whether or not it went into a line (the mic test shows it). */
		default void heard(String text) {}

		/** After every 32 ms window: speech in progress, the decaying peak, whether a line is open. */
		default void live(boolean speaking, float livePeak, boolean lineOpen) {}

		/** The mic's error, or {@code null} once it is fine again. */
		default void micError(String error) {}
	}

	/**
	 * Hears what the capture thread does, on {@code vfg-capture}: each window once the VAD has
	 * had it, with whether it hears speech. It must return at once and never block.
	 */
	public interface Tap {
		void onStarted(boolean forLine);

		void onWindow(float[] window, long readWallMillis, boolean speaking);

		void onStopped(StopReason reason);
	}

	private final CaptureProcessor capture;
	private final SttWorker stt;
	private final MicCapture mic;
	private Thread sttThread;

	/**
	 * @param vad the VAD, or {@code null} while the engine is not loaded
	 * @param transcriber speech-to-text, or {@code null} while the engine is not loaded
	 * @param turn the turn model, or {@code null} while none is loaded; lines then end on silence alone
	 * @param turnThreshold the score at or above which a line is finished, or {@code null} for the model's own
	 * @param clock wall-clock milliseconds, for the silence timer
	 */
	public VoicePipeline(MicCapture.DeviceOpener devices, Supplier<VoiceDetector> vad, Supplier<Transcriber> transcriber,
			Supplier<TurnDetector> turn, Double turnThreshold, double silenceSeconds, LongSupplier clock,
			BooleanSupplier logText, Events events) {
		this(devices, vad, transcriber, turn, turnThreshold, silenceSeconds, clock, logText, events, null);
	}

	/** With {@code tap}, the keyword spotter, hearing every window after the VAD; {@code null} for none. */
	public VoicePipeline(MicCapture.DeviceOpener devices, Supplier<VoiceDetector> vad, Supplier<Transcriber> transcriber,
			Supplier<TurnDetector> turn, Double turnThreshold, double silenceSeconds, LongSupplier clock,
			BooleanSupplier logText, Events events, Tap tap) {
		LineAssembler assembler = new LineAssembler(silenceSeconds, clock, events::line, events::decided);
		CaptureProcessor[] cp = new CaptureProcessor[1];
		this.stt = new SttWorker(transcriber, turn, turnThreshold, assembler, () -> cp[0].speaking(), events::heard,
			logText);
		this.capture = new CaptureProcessor(vad, () -> turn.get() != null, stt::submit);
		cp[0] = capture;
		WindowListener publishing = new WindowListener() {
			@Override
			public void onStarted(boolean forLine) {
				capture.onStarted(forLine);
				if (tap != null) {
					tap.onStarted(forLine);
				}
			}

			@Override
			public void onWindow(float[] window, long readWallMillis) {
				capture.onWindow(window, readWallMillis);
				if (tap != null) {
					tap.onWindow(window, readWallMillis, capture.speaking());
				}
				events.live(capture.speaking(), capture.livePeak(), stt.lineOpen());
			}

			@Override
			public void onStopped(StopReason reason) {
				capture.onStopped(reason);
				if (tap != null) {
					tap.onStopped(reason);
				}
				events.live(false, 0f, stt.lineOpen());
			}
		};
		this.mic = new MicCapture(devices, publishing, events::micError);
	}

	/** Starts {@code vfg-stt}; the capture thread starts with the first {@link #startCapture}. */
	public synchronized void start() {
		if (sttThread == null) {
			sttThread = new Thread(stt, "vfg-stt");
			sttThread.setDaemon(true);
			sttThread.start();
		}
	}

	/** Opens the device (empty name: the system default). {@code forLine} is false for the mic test. */
	public void startCapture(String device, boolean forLine) {
		mic.start(device, forLine);
	}

	/** Closes the device at once; {@code LEAVE} turns speech in progress into a last segment and closes the line. */
	public void stopCapture(StopReason reason) {
		mic.stop(reason);
	}

	/** Switches device without stopping. */
	public void switchDevice(String device) {
		mic.reopen(device);
	}

	public boolean speaking() {
		return capture.speaking();
	}

	public float livePeak() {
		return capture.livePeak();
	}

	public boolean lineOpen() {
		return stt.lineOpen();
	}

	/** The end of the open line's last speech (wall-clock ms); meaningful while {@link #lineOpen()}. */
	public long openLineEndedAt() {
		return stt.openLineEndedAt();
	}

	/** Segments waiting to be transcribed. */
	public int pendingSegments() {
		return stt.pendingSegments();
	}

	/** Closes the mic and stops {@code vfg-stt}, waiting up to {@code joinMillis} for it. */
	public void shutdown(long joinMillis) {
		mic.shutdown();
		stt.stop();
		Thread t;
		synchronized (this) {
			t = sttThread;
		}
		if (t != null) {
			try {
				t.join(joinMillis);
			} catch (InterruptedException e) {
				Thread.currentThread().interrupt();
			}
		}
	}
}
