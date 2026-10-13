package com.voiceforgaming.vfg.session;

/**
 * Where every thread reports and the HUD and settings screen read: each change publishes
 * a new immutable {@link VoiceStatus} behind a volatile field, so a reader never waits
 * and never sees half an update.
 */
public final class StatusBoard {
	private volatile VoiceStatus current = VoiceStatus.INITIAL;

	public VoiceStatus get() {
		return current;
	}

	public synchronized void setupRunning(int percent) {
		VoiceStatus s = current;
		current = new VoiceStatus(VoiceStatus.Setup.RUNNING, Math.max(0, Math.min(100, percent)), null, s.micError(),
			s.tokenRejected(), s.deliveryProblem(), s.onServer(), s.listening(), s.muted(), s.noToken(), s.speaking(),
			s.lineOpen(), s.livePeak(), s.lastHeard(), s.turnModel(), s.turnError(), s.stt());
	}

	public synchronized void setup(VoiceStatus.Setup setup, String error) {
		VoiceStatus s = current;
		current = new VoiceStatus(setup, setup == VoiceStatus.Setup.READY ? 100 : s.setupPercent(), error,
			s.micError(), s.tokenRejected(), s.deliveryProblem(), s.onServer(), s.listening(), s.muted(), s.noToken(),
			s.speaking(), s.lineOpen(), s.livePeak(), s.lastHeard(), s.turnModel(), s.turnError(), s.stt());
	}

	public synchronized void micError(String error) {
		VoiceStatus s = current;
		if (java.util.Objects.equals(error, s.micError())) {
			return;
		}
		current = new VoiceStatus(s.setup(), s.setupPercent(), s.setupError(), error, s.tokenRejected(),
			s.deliveryProblem(), s.onServer(), s.listening(), s.muted(), s.noToken(), s.speaking(), s.lineOpen(),
			s.livePeak(), s.lastHeard(), s.turnModel(), s.turnError(), s.stt());
	}

	public synchronized void tokenRejected(boolean rejected) {
		VoiceStatus s = current;
		if (s.tokenRejected() == rejected) {
			return;
		}
		current = new VoiceStatus(s.setup(), s.setupPercent(), s.setupError(), s.micError(), rejected,
			s.deliveryProblem(), s.onServer(), s.listening(), s.muted(), s.noToken(), s.speaking(), s.lineOpen(),
			s.livePeak(), s.lastHeard(), s.turnModel(), s.turnError(), s.stt());
	}

	public synchronized void deliveryProblem(boolean problem) {
		VoiceStatus s = current;
		if (s.deliveryProblem() == problem) {
			return;
		}
		current = new VoiceStatus(s.setup(), s.setupPercent(), s.setupError(), s.micError(), s.tokenRejected(),
			problem, s.onServer(), s.listening(), s.muted(), s.noToken(), s.speaking(), s.lineOpen(), s.livePeak(),
			s.lastHeard(), s.turnModel(), s.turnError(), s.stt());
	}

	public synchronized void session(boolean onServer, boolean listening, boolean muted, boolean noToken) {
		VoiceStatus s = current;
		current = new VoiceStatus(s.setup(), s.setupPercent(), s.setupError(), s.micError(), s.tokenRejected(),
			s.deliveryProblem(), onServer, listening, muted, noToken, listening && s.speaking(), s.lineOpen(),
			s.livePeak(), s.lastHeard(), s.turnModel(), s.turnError(), s.stt());
	}

	public synchronized void live(boolean speaking, float livePeak, boolean lineOpen) {
		VoiceStatus s = current;
		current = new VoiceStatus(s.setup(), s.setupPercent(), s.setupError(), s.micError(), s.tokenRejected(),
			s.deliveryProblem(), s.onServer(), s.listening(), s.muted(), s.noToken(), speaking, lineOpen, livePeak,
			s.lastHeard(), s.turnModel(), s.turnError(), s.stt());
	}

	public synchronized void lineOpen(boolean lineOpen) {
		VoiceStatus s = current;
		if (s.lineOpen() == lineOpen) {
			return;
		}
		current = new VoiceStatus(s.setup(), s.setupPercent(), s.setupError(), s.micError(), s.tokenRejected(),
			s.deliveryProblem(), s.onServer(), s.listening(), s.muted(), s.noToken(), s.speaking(), lineOpen,
			s.livePeak(), s.lastHeard(), s.turnModel(), s.turnError(), s.stt());
	}

	public synchronized void turnModel(VoiceStatus.TurnModel turnModel, String error) {
		VoiceStatus s = current;
		current = new VoiceStatus(s.setup(), s.setupPercent(), s.setupError(), s.micError(), s.tokenRejected(),
			s.deliveryProblem(), s.onServer(), s.listening(), s.muted(), s.noToken(), s.speaking(), s.lineOpen(),
			s.livePeak(), s.lastHeard(), turnModel, error, s.stt());
	}

	public synchronized void stt(VoiceStatus.Stt stt) {
		VoiceStatus s = current;
		current = new VoiceStatus(s.setup(), s.setupPercent(), s.setupError(), s.micError(), s.tokenRejected(),
			s.deliveryProblem(), s.onServer(), s.listening(), s.muted(), s.noToken(), s.speaking(), s.lineOpen(),
			s.livePeak(), s.lastHeard(), s.turnModel(), s.turnError(), stt == null ? VoiceStatus.Stt.NONE : stt);
	}

	public synchronized void heard(String text) {
		VoiceStatus s = current;
		current = new VoiceStatus(s.setup(), s.setupPercent(), s.setupError(), s.micError(), s.tokenRejected(),
			s.deliveryProblem(), s.onServer(), s.listening(), s.muted(), s.noToken(), s.speaking(), s.lineOpen(),
			s.livePeak(), text == null ? "" : text, s.turnModel(), s.turnError(), s.stt());
	}
}
