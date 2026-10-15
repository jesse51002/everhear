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
	String lastHeard,
	TurnModel turnModel,
	String turnError,
	Stt stt
) {
	/**
	 * The speech model in use ({@code null} before one is loaded) and a note about it for Voice
	 * settings: the bigger model downloading while the smaller one listens, or the smaller one
	 * standing in because the bigger one would not load.
	 */
	public record Stt(String model, Note note, int percent, String detail) {
		public enum Note {
			NONE,
			/** The chosen model downloads in the background ({@code percent}); {@code model} listens meanwhile. */
			UPGRADING,
			/** Its download failed ({@code detail}); tried again in the background. */
			UPGRADE_RETRYING,
			/** It would not load ({@code detail}); {@code model} stands in until the next setup. */
			FALLBACK,
			/** Its last two loads did not finish (the game closed or crashed): {@code model} is chosen now. */
			CRASHED
		}

		public static final Stt NONE = new Stt(null, Note.NONE, 0, null);
	}

	public enum Setup {
		RUNNING,
		READY,
		/** Downloading or unpacking failed; the settings screen offers Retry. */
		SETUP_FAILED,
		/** The natives or the model would not load. */
		ENGINE_FAILED,
		UNSUPPORTED
	}

	/**
	 * The turn model, which only makes lines end sooner: without it they end on silence alone,
	 * so none of these is an error for the icon.
	 */
	public enum TurnModel {
		/** {@code turn_model: off}. */
		OFF,
		LOADING,
		READY,
		/** The download failed; it is tried again in the background. */
		RETRYING,
		/** It downloaded but would not load; not tried again until the next setup. */
		FAILED
	}

	/** The icon's look, in the order the table in the spec gives them; the first that applies wins. */
	public enum Icon { HIDDEN, SETUP, ERROR, MUTED, NO_TOKEN, DELIVERY_PROBLEM, HEARING, LISTENING }

	public static final VoiceStatus INITIAL = new VoiceStatus(Setup.RUNNING, 0, null, null, false, false, false,
		false, false, false, false, false, 0f, "", TurnModel.LOADING, null, Stt.NONE);

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
