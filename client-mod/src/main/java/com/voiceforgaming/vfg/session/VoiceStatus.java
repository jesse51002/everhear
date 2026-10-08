package com.voiceforgaming.vfg.session;

/**
 * One immutable snapshot of everything the HUD icon and the settings screen show.
 * Published by {@link StatusBoard} behind a volatile field; never mutated.
 */
public record VoiceStatus(
	Setup setup,
	int setupPercent,
	String setupError,
	String micError,
	boolean tokenRejected,
	boolean deliveryProblem,
	boolean onServer,
	boolean listening,
	boolean muted,
	boolean noToken,
	boolean speaking,
	boolean lineOpen,
	float livePeak,
	String lastHeard
) {
	public enum Setup {
		RUNNING,
		READY,
		/** Downloading or unpacking failed; the settings screen offers Retry. */
		SETUP_FAILED,
		/** The natives or the model would not load. */
		ENGINE_FAILED,
		UNSUPPORTED
	}

	/** The icon's look, in the order the table in the spec gives them; the first that applies wins. */
	public enum Icon { HIDDEN, SETUP, ERROR, MUTED, NO_TOKEN, DELIVERY_PROBLEM, HEARING, LISTENING }

	public static final VoiceStatus INITIAL = new VoiceStatus(Setup.RUNNING, 0, null, null, false, false, false,
		false, false, false, false, false, 0f, "");

	public boolean setupFailed() {
		return setup == Setup.SETUP_FAILED || setup == Setup.ENGINE_FAILED || setup == Setup.UNSUPPORTED;
	}

	/** The icon, drawn only while connected to the matching server. */
	public Icon icon() {
		if (!onServer) {
			return Icon.HIDDEN;
		}
		if (setup == Setup.RUNNING) {
			return Icon.SETUP;
		}
		if (micError != null || setupFailed() || tokenRejected) {
			return Icon.ERROR;
		}
		if (muted) {
			return Icon.MUTED;
		}
		if (noToken) {
			return Icon.NO_TOKEN;
		}
		if (deliveryProblem) {
			return Icon.DELIVERY_PROBLEM;
		}
		if (listening && (speaking || lineOpen)) {
			return Icon.HEARING;
		}
		return Icon.LISTENING;
	}
}
