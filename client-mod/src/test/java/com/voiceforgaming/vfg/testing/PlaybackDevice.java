package com.voiceforgaming.vfg.testing;

import com.voiceforgaming.vfg.audio.MicCapture;

/**
 * A capture device that "hears" a recording in real time: once opened, samples become
 * available at the device's rate as the wall clock runs, exactly as a sound card's would,
 * and after the recording it keeps delivering its noise floor. It opens only at its own
 * rate, so a 48 kHz device makes {@link MicCapture} take its 48 kHz-and-resample path.
 */
public final class PlaybackDevice implements MicCapture.DeviceOpener {
	private final short[] pcm;
	private final int rate;
	private final short[] floor;
	private volatile long openedAtMillis = -1;

	/** {@code floor} loops after the recording ends (silence or a room's noise). */
	public PlaybackDevice(float[] samples, int rate, float[] floor) {
		this.pcm = new short[samples.length];
		for (int i = 0; i < samples.length; i++) {
			pcm[i] = Wav.toShort(samples[i]);
		}
		this.rate = rate;
		this.floor = new short[Math.max(1, floor.length)];
		for (int i = 0; i < floor.length; i++) {
			this.floor[i] = Wav.toShort(floor[i]);
		}
	}

	public int rate() {
		return rate;
	}

	/** When the capture thread opened it, or -1. */
	public long openedAtMillis() {
		return openedAtMillis;
	}

	/** The wall-clock instant sample {@code index} of the recording was "spoken". */
	public long wallMillisOf(long index) {
		return openedAtMillis + index * 1000 / rate;
	}

	public double recordingSeconds() {
		return pcm.length / (double) rate;
	}

	@Override
	public MicCapture.Device open(String name, int wantedRate) {
		if (wantedRate != rate) {
			return null;
		}
		long opened = System.currentTimeMillis();
		openedAtMillis = opened;
		return new MicCapture.Device() {
			private long consumed;
			private boolean closed;

			@Override
			public int rate() {
				return rate;
			}

			@Override
			public int available() {
				long arrived = (System.currentTimeMillis() - opened) * rate / 1000;
				return (int) Math.max(0, Math.min(Integer.MAX_VALUE, arrived - consumed));
			}

			@Override
			public void read(short[] into, int count) {
				for (int i = 0; i < count; i++) {
					long k = consumed + i;
					into[i] = k < pcm.length ? pcm[(int) k] : floor[(int) ((k - pcm.length) % floor.length)];
				}
				consumed += count;
			}

			@Override
			public boolean connected() {
				return !closed;
			}

			@Override
			public void close() {
				closed = true;
			}
		};
	}
}
