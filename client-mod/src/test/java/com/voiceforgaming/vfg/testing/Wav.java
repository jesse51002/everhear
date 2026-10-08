package com.voiceforgaming.vfg.testing;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.file.Files;
import java.nio.file.Path;

/** 16-bit PCM WAV files, read as floats (first channel) and written mono. */
public final class Wav {
	/** Samples (full scale 1.0) and their rate. */
	public record Audio(float[] samples, int rate) {
		public double seconds() {
			return samples.length / (double) rate;
		}
	}

	private Wav() {}

	public static Audio read(Path wav) throws IOException {
		ByteBuffer b = ByteBuffer.wrap(Files.readAllBytes(wav)).order(ByteOrder.LITTLE_ENDIAN);
		b.position(12);
		int channels = 1;
		int rate = 0;
		int bits = 16;
		while (b.remaining() >= 8) {
			int id = b.getInt();
			int size = b.getInt();
			if (id == 0x20746d66) { // "fmt "
				int start = b.position();
				b.getShort();
				channels = b.getShort();
				rate = b.getInt();
				b.getInt();
				b.getShort();
				bits = b.getShort();
				b.position(start + size);
			} else if (id == 0x61746164) { // "data"
				if (bits != 16) {
					throw new IOException(wav + ": " + bits + "-bit, 16-bit PCM expected");
				}
				int frames = Math.min(size, b.remaining()) / (2 * channels);
				float[] out = new float[frames];
				int base = b.position();
				for (int i = 0; i < frames; i++) {
					out[i] = b.getShort(base + i * 2 * channels) / 32768f;
				}
				return new Audio(out, rate);
			} else {
				b.position(b.position() + size + (size & 1));
			}
		}
		throw new IOException("no data chunk in " + wav);
	}

	public static void write(Path wav, float[] samples, int rate) throws IOException {
		ByteBuffer b = ByteBuffer.allocate(44 + samples.length * 2).order(ByteOrder.LITTLE_ENDIAN);
		b.putInt(0x46464952).putInt(36 + samples.length * 2).putInt(0x45564157); // RIFF, WAVE
		b.putInt(0x20746d66).putInt(16).putShort((short) 1).putShort((short) 1).putInt(rate).putInt(rate * 2)
			.putShort((short) 2).putShort((short) 16);
		b.putInt(0x61746164).putInt(samples.length * 2);
		for (float s : samples) {
			b.putShort(toShort(s));
		}
		Files.createDirectories(wav.toAbsolutePath().getParent());
		Files.write(wav, b.array());
	}

	/** As a 16-bit sample, the way a sound card delivers it. */
	public static short toShort(float s) {
		return (short) Math.max(-32768, Math.min(32767, Math.round(s * 32768f)));
	}
}
