package com.voiceforgaming.vfg.audio;

import com.voiceforgaming.vfg.audio.WindowListener.StopReason;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;
import org.lwjgl.openal.AL10;
import org.lwjgl.openal.ALC10;
import org.lwjgl.openal.ALC11;
import org.lwjgl.openal.ALUtil;
import org.lwjgl.openal.EXTDisconnect;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * The {@code vfg-capture} thread: OpenAL capture through LWJGL, already in Minecraft.
 *
 * <p>Opens the device at 16 kHz mono 16-bit; if that fails, at 48 kHz through
 * {@link Resampler}; if that fails too, the mic is in error and it tries again every 5 s.
 * Every 32 ms it reads whole 512-sample windows, converts them to floats and hands them to
 * the {@link WindowListener}. Once a second it checks {@code ALC_CONNECTED}; on a lost
 * device it closes and tries to reopen every 5 s. The device is closed whenever nothing
 * asked for capture. All OpenAL capture calls happen on this thread.
 */
public final class MicCapture {
	private static final Logger LOG = LoggerFactory.getLogger("vfg");
	public static final int RATE = 16000;
	public static final int WINDOW = 512;
	private static final int FALLBACK_RATE = 48000;
	private static final long READ_EVERY_MILLIS = 32;
	private static final long CHECK_EVERY_MILLIS = 1000;
	private static final long REOPEN_EVERY_MILLIS = 5000;

	private sealed interface Command permits Start, Stop, Reopen, Shutdown {}

	private record Start(String device, boolean forLine) implements Command {}

	private record Stop(StopReason reason) implements Command {}

	private record Reopen(String device) implements Command {}

	private record Shutdown() implements Command {}

	private final WindowListener listener;
	private final Consumer<String> errors;
	private final LinkedBlockingQueue<Command> commands = new LinkedBlockingQueue<>();
	private Thread thread;

	// capture-thread state
	private boolean wanted;
	private String device = "";
	private Device dev;
	private int rate;
	private Resampler resampler;
	private long lastCheck;
	private long nextOpenAt;
	private boolean failing;
	private short[] shorts = new short[FALLBACK_RATE / RATE * WINDOW];
	private final float[] floats = new float[FALLBACK_RATE / RATE * WINDOW];

	/** An open capture device, mono 16-bit, as the capture thread uses it. */
	public interface Device {
		int rate();

		/** Samples captured and not yet read. */
		int available();

		/** Reads the oldest {@code count} captured samples. */
		void read(short[] into, int count);

		/** False once the device is gone (unplugged). */
		boolean connected();

		void close();
	}

	/** Opens a capture device by name ({@code null} for the default) at a rate; {@code null} when it cannot. */
	@FunctionalInterface
	public interface DeviceOpener {
		Device open(String name, int rate);
	}

	/** The real microphones, through OpenAL. */
	public static final DeviceOpener OPENAL = OpenAlDevice::open;

	private final DeviceOpener opener;

	public MicCapture(WindowListener listener, Consumer<String> errors) {
		this(OPENAL, listener, errors);
	}

	/** With another source of audio (the speech test plays recordings through it in real time). */
	public MicCapture(DeviceOpener opener, WindowListener listener, Consumer<String> errors) {
		this.opener = opener;
		this.listener = listener;
		this.errors = errors;
	}

	/** The OS's default capture device, as OpenAL names it, or {@code null}. */
	public static String defaultDeviceName() {
		try {
			return ALC10.alcGetString(0L, ALC11.ALC_CAPTURE_DEFAULT_DEVICE_SPECIFIER);
		} catch (Throwable t) {
			return null;
		}
	}

	private record OpenAlDevice(long handle, int rate, boolean canCheckConnected) implements Device {
		static Device open(String name, int rate) {
			long d = ALC11.alcCaptureOpenDevice(name, rate, AL10.AL_FORMAT_MONO16, rate);
			if (d == 0) {
				return null;
			}
			ALC11.alcCaptureStart(d);
			return new OpenAlDevice(d, rate, ALC10.alcIsExtensionPresent(d, "ALC_EXT_disconnect"));
		}

		@Override
		public int available() {
			return ALC10.alcGetInteger(handle, ALC11.ALC_CAPTURE_SAMPLES);
		}

		@Override
		public void read(short[] into, int count) {
			ALC11.alcCaptureSamples(handle, into, count);
		}

		@Override
		public boolean connected() {
			return !canCheckConnected || ALC10.alcGetInteger(handle, EXTDisconnect.ALC_CONNECTED) != 0;
		}

		@Override
		public void close() {
			ALC11.alcCaptureStop(handle);
			ALC11.alcCaptureCloseDevice(handle);
		}
	}

	/** {@code System default} is not in this list; it is the empty device name. */
	public static List<String> listDevices() {
		try {
			List<String> names = ALUtil.getStringList(0L, ALC11.ALC_CAPTURE_DEVICE_SPECIFIER);
			return names == null ? List.of() : new ArrayList<>(names);
		} catch (Throwable t) {
			LOG.warn("could not list capture devices: {}", t.toString());
			return List.of();
		}
	}

	private synchronized void ensureThread() {
		if (thread == null) {
			thread = new Thread(this::run, "vfg-capture");
			thread.setDaemon(true);
			thread.start();
		}
	}

	/** Starts capture from {@code device} (empty for the system default). */
	public void start(String device, boolean forLine) {
		ensureThread();
		commands.add(new Start(device == null ? "" : device, forLine));
	}

	/** Stops capture and closes the device at once. */
	public void stop(StopReason reason) {
		ensureThread();
		commands.add(new Stop(reason));
	}

	/** Switches to another device without stopping (a settings change while capturing). */
	public void reopen(String device) {
		ensureThread();
		commands.add(new Reopen(device == null ? "" : device));
	}

	public void shutdown() {
		Thread t;
		synchronized (this) {
			t = thread;
		}
		if (t == null) {
			return;
		}
		commands.add(new Shutdown());
		try {
			t.join(2000);
		} catch (InterruptedException e) {
			Thread.currentThread().interrupt();
		}
	}

	private void run() {
		try {
			while (true) {
				Command c;
				if (dev != null) {
					c = commands.poll(READ_EVERY_MILLIS, TimeUnit.MILLISECONDS);
				} else if (wanted) {
					c = commands.poll(Math.max(1, nextOpenAt - System.currentTimeMillis()), TimeUnit.MILLISECONDS);
				} else {
					c = commands.take();
				}
				if (c != null && !handle(c)) {
					return;
				}
				long now = System.currentTimeMillis();
				if (dev != null) {
					read();
					if (now - lastCheck >= CHECK_EVERY_MILLIS) {
						lastCheck = now;
						checkConnected();
					}
				} else if (wanted && now >= nextOpenAt) {
					open();
				}
			}
		} catch (InterruptedException e) {
			// shutting down
		} catch (Throwable t) {
			LOG.error("capture thread failed", t);
			errors.accept("mic: " + t.getMessage());
		} finally {
			close();
		}
	}

	/** Returns false to end the thread. */
	private boolean handle(Command c) {
		switch (c) {
			case Start(String name, boolean forLine) -> {
				if (wanted) {
					close();
					wanted = false;
					listener.onStopped(StopReason.TEST_ENDED);
				}
				device = name;
				wanted = true;
				listener.onStarted(forLine);
				open();
			}
			case Stop(StopReason reason) -> {
				if (wanted) {
					close();
					wanted = false;
					failing = false;
					errors.accept(null);
					listener.onStopped(reason);
				}
			}
			case Reopen(String name) -> {
				device = name;
				if (wanted) {
					close();
					open();
				}
			}
			case Shutdown() -> {
				close();
				wanted = false;
				return false;
			}
		}
		return true;
	}

	private void open() {
		close();
		String name = device.isEmpty() ? null : device;
		try {
			Device d = opener.open(name, RATE);
			if (d == null) {
				d = opener.open(name, FALLBACK_RATE);
			}
			if (d == null) {
				fail("could not open " + (name == null ? "the default microphone" : "\"" + name + "\""));
				return;
			}
			dev = d;
			rate = d.rate();
			resampler = rate == RATE ? null : new Resampler();
			lastCheck = System.currentTimeMillis();
			errors.accept(null);
			failing = false;
			LOG.info("microphone open: {} at {} Hz", name == null ? "system default" : name, rate);
		} catch (Throwable t) {
			fail(t.toString());
		}
	}

	private void fail(String why) {
		if (!failing) {
			LOG.warn("microphone: {}; trying again every 5 s", why);
			failing = true;
		} else {
			LOG.debug("microphone: {}", why);
		}
		errors.accept("mic: " + why);
		nextOpenAt = System.currentTimeMillis() + REOPEN_EVERY_MILLIS;
	}

	private void close() {
		if (dev != null) {
			try {
				dev.close();
			} catch (Throwable t) {
				LOG.debug("closing the microphone: {}", t.toString());
			}
			dev = null;
			LOG.info("microphone closed");
		}
	}

	private void checkConnected() {
		if (!dev.connected()) {
			close();
			fail("the microphone was disconnected");
		}
	}

	private void read() {
		int chunk = rate / RATE * WINDOW;
		int available = dev.available();
		int windows = available / chunk;
		if (windows <= 0) {
			return;
		}
		// The samples read end where the leftover (newer) ones begin.
		long readEnd = System.currentTimeMillis() - (available - windows * (long) chunk) * 1000L / rate;
		for (int i = 0; i < windows; i++) {
			if (shorts.length < chunk) {
				shorts = new short[chunk];
			}
			dev.read(shorts, chunk);
			for (int k = 0; k < chunk; k++) {
				floats[k] = shorts[k] / 32768f;
			}
			float[] window;
			if (resampler == null) {
				window = java.util.Arrays.copyOf(floats, WINDOW);
			} else {
				window = resampler.process(floats, chunk);
			}
			long windowEnd = readEnd - (windows - 1 - i) * (WINDOW * 1000L / RATE);
			listener.onWindow(window, windowEnd);
		}
	}
}
