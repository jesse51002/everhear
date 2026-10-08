package com.voiceforgaming.vfg.audio;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.voiceforgaming.vfg.testing.PlaybackDevice;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import org.junit.jupiter.api.Test;

/** The capture thread against a device that delivers samples in real time, as a sound card does. No OpenAL. */
class MicCaptureTest {
	private static float[] sine(int rate, double seconds, double hz) {
		float[] s = new float[(int) (rate * seconds)];
		for (int i = 0; i < s.length; i++) {
			s[i] = (float) (0.5 * Math.sin(2 * Math.PI * hz * i / rate));
		}
		return s;
	}

	private record Got(float[] window, long at) {}

	private static List<Got> capture(PlaybackDevice device, List<String> events) throws InterruptedException {
		List<Got> got = Collections.synchronizedList(new ArrayList<>());
		MicCapture mic = new MicCapture(device, new WindowListener() {
			@Override
			public void onStarted(boolean forLine) {
				events.add("started " + forLine);
			}

			@Override
			public void onWindow(float[] window, long readWallMillis) {
				got.add(new Got(window, readWallMillis));
			}

			@Override
			public void onStopped(StopReason reason) {
				events.add("stopped " + reason);
			}
		}, error -> events.add("error " + error));
		mic.start("", true);
		Thread.sleep(1100);
		mic.stop(WindowListener.StopReason.LEAVE);
		Thread.sleep(100);
		mic.shutdown();
		return got;
	}

	@Test
	void aSixteenKilohertzDeviceGivesWholeWindowsOnTime() throws Exception {
		List<String> events = Collections.synchronizedList(new ArrayList<>());
		PlaybackDevice device = new PlaybackDevice(sine(16000, 2, 440), 16000, new float[16000]);
		List<Got> got = capture(device, events);
		assertTrue(got.size() >= 28 && got.size() <= 36, "windows in 1.1 s: " + got.size());
		for (Got g : got) {
			assertEquals(512, g.window().length);
		}
		long first = got.getFirst().at() - device.openedAtMillis();
		assertTrue(first >= 20 && first <= 120, "first window stamped " + first + " ms after opening");
		for (int i = 1; i < got.size(); i++) {
			assertTrue(got.get(i).at() >= got.get(i - 1).at(), "timestamps go forward");
		}
		assertEquals(0.5f, max(got.get(3).window()), 0.01f);
		assertTrue(events.contains("started true") && events.contains("stopped LEAVE"), events.toString());
	}

	@Test
	void aDeviceThatOnlyOpensAt48kHzIsResampledTo16kHzWindows() throws Exception {
		List<String> events = Collections.synchronizedList(new ArrayList<>());
		PlaybackDevice device = new PlaybackDevice(sine(48000, 2, 1000), 48000, new float[48000]);
		List<Got> got = capture(device, events);
		assertTrue(got.size() >= 28 && got.size() <= 36, "windows in 1.1 s: " + got.size());
		int crossings = 0;
		float[] w = got.get(5).window();
		for (int i = 1; i < w.length; i++) {
			if ((w[i - 1] < 0) != (w[i] < 0)) {
				crossings++;
			}
		}
		assertEquals(512, w.length);
		assertEquals(64, crossings, 2, "1 kHz over 32 ms at 16 kHz");
	}

	private static float max(float[] w) {
		float m = 0;
		for (float x : w) {
			m = Math.max(m, Math.abs(x));
		}
		return m;
	}
}
