package com.voiceforgaming.vfg.audio;

/** What the capture thread hands each 512-sample, 16 kHz window to. Called on {@code vfg-capture} only. */
public interface WindowListener {
	/** Why capture stopped. */
	enum StopReason {
		/** Muted, or settings stopped it: the VAD's state and the open line are discarded. */
		DISCARD,
		/** The player left the server: speech in progress becomes a last segment and the open line closes now. */
		LEAVE,
		/** The settings screen's mic test ended. */
		TEST_ENDED
	}

	/** Capture is starting: reset the VAD and the sample counter together. */
	void onStarted(boolean forLine);

	/** One window, read from the device at {@code readWallMillis}. */
	void onWindow(float[] window, long readWallMillis);

	/** Capture stopped and the device is closed. */
	void onStopped(StopReason reason);
}
