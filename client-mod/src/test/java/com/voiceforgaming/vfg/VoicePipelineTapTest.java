package com.voiceforgaming.vfg;

import static org.junit.jupiter.api.Assertions.assertEquals;

import com.voiceforgaming.vfg.audio.WindowListener.StopReason;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;

/** The keyword spotter and Vosk both hear every window, through one tap. */
class VoicePipelineTapTest {
	@Test
	void everyTapHearsEveryCallInOrder() {
		List<String> heard = new ArrayList<>();
		VoicePipeline.Tap a = recorder("a", heard);
		VoicePipeline.Tap b = recorder("b", heard);
		VoicePipeline.Tap both = VoicePipeline.Tap.of(a, b);
		both.onStarted(true);
		both.onWindow(new float[] {0.5f}, 1_000, true);
		both.onStopped(StopReason.LEAVE);
		assertEquals(List.of("a started true", "b started true", "a window 0.5 1000 true", "b window 0.5 1000 true",
			"a stopped LEAVE", "b stopped LEAVE"), heard);
	}

	private static VoicePipeline.Tap recorder(String name, List<String> heard) {
		return new VoicePipeline.Tap() {
			@Override
			public void onStarted(boolean forLine) {
				heard.add(name + " started " + forLine);
			}

			@Override
			public void onWindow(float[] window, long readWallMillis, boolean speaking) {
				heard.add(name + " window " + window[0] + " " + readWallMillis + " " + speaking);
			}

			@Override
			public void onStopped(StopReason reason) {
				heard.add(name + " stopped " + reason);
			}
		};
	}
}
