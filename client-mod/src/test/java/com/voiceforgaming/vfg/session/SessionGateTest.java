package com.voiceforgaming.vfg.session;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.voiceforgaming.vfg.audio.WindowListener.StopReason;
import com.voiceforgaming.vfg.config.VfgConfig;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/** When the mic is open: only while connected to the matching server, with a token, set up, and not muted. */
class SessionGateTest {
	private VfgConfig config;
	private boolean ready;
	private final List<String> calls = new ArrayList<>();
	private final List<String> notices = new ArrayList<>();
	private final StatusBoard status = new StatusBoard();
	private SessionGate gate;

	@BeforeEach
	void setUp() {
		config = new VfgConfig().withEnv(Map.of());
		config.serverAddress = "play.example.com";
		config.clientToken = "token";
		ready = true;
		status.setup(VoiceStatus.Setup.READY, null);
		gate = new SessionGate(() -> config, () -> ready, new SessionGate.Listening() {
			@Override
			public void start() {
				calls.add("start");
			}

			@Override
			public void stop(StopReason reason) {
				calls.add("stop " + reason);
			}
		}, notices::add, status);
	}

	private boolean micOpen() {
		int open = 0;
		for (String c : calls) {
			open += c.equals("start") ? 1 : -1;
		}
		return open == 1;
	}

	@Test
	void listensOnTheMatchingServerAndGreetsOnce() {
		gate.onJoin("Play.Example.com:25565");
		assertTrue(micOpen());
		assertEquals(List.of(SessionGate.NOTICE_LISTENING_INTRO), notices);
		assertEquals(VoiceStatus.Icon.LISTENING, status.get().icon());
		gate.onLeave();
		assertEquals(List.of("start", "stop LEAVE"), calls);
		assertFalse(micOpen());
		assertEquals(VoiceStatus.Icon.HIDDEN, status.get().icon());
		gate.onJoin("play.example.com");
		assertTrue(micOpen());
		assertEquals(1, notices.size(), "once per server per game session");
	}

	@Test
	void anotherServerNeverListensAndShowsNoIcon() {
		gate.onJoin("other.example.com");
		assertTrue(calls.isEmpty());
		assertEquals(VoiceStatus.Icon.HIDDEN, status.get().icon());
	}

	@Test
	void singleplayerAndLanNeverListen() {
		gate.onJoin(null);
		assertTrue(calls.isEmpty());
		assertTrue(notices.isEmpty());
	}

	@Test
	void muteClosesTheMicAndDiscardsAndUnmuteReopens() {
		gate.onJoin("play.example.com");
		config.muted = true;
		gate.onMuteToggled();
		assertEquals(List.of("start", "stop DISCARD"), calls);
		assertEquals(VoiceStatus.Icon.MUTED, status.get().icon());
		config.muted = false;
		gate.onMuteToggled();
		assertTrue(micOpen());
		assertEquals(List.of(SessionGate.NOTICE_LISTENING_INTRO, SessionGate.NOTICE_MUTED, SessionGate.NOTICE_LISTENING),
			notices);
	}

	@Test
	void joiningMutedDoesNotOpenTheMic() {
		config.muted = true;
		gate.onJoin("play.example.com");
		assertTrue(calls.isEmpty());
		assertEquals(VoiceStatus.Icon.MUTED, status.get().icon());
	}

	@Test
	void noTokenSaysSoOnceAndDoesNotListen() {
		config.clientToken = "";
		gate.onJoin("play.example.com");
		gate.recheck();
		assertTrue(calls.isEmpty());
		assertEquals(List.of(SessionGate.NOTICE_NO_TOKEN), notices);
		assertEquals(VoiceStatus.Icon.NO_TOKEN, status.get().icon());
		config.clientToken = "now set";
		gate.recheck(); // settings saved
		assertTrue(micOpen());
	}

	@Test
	void aBlankServerAddressSaysSoOncePerGameSession() {
		config.serverAddress = "";
		gate.onJoin("play.example.com");
		gate.onLeave();
		gate.onJoin("other.example.com");
		assertEquals(List.of(SessionGate.NOTICE_NOT_SET_UP), notices);
		assertTrue(calls.isEmpty());
	}

	@Test
	void listeningWaitsForSetup() {
		ready = false;
		status.setupRunning(42);
		gate.onJoin("play.example.com");
		assertTrue(calls.isEmpty());
		assertEquals(VoiceStatus.Icon.SETUP, status.get().icon());
		ready = true;
		status.setup(VoiceStatus.Setup.READY, null);
		gate.recheck(); // setup finished
		assertTrue(micOpen());
	}

	@Test
	void settingsThatStopItDiscard() {
		gate.onJoin("play.example.com");
		config.serverAddress = "elsewhere.example.com";
		gate.recheck();
		assertEquals(List.of("start", "stop DISCARD"), calls);
	}

	@Test
	void theTokenRejectionIsAnnouncedOncePerConnection() {
		gate.onJoin("play.example.com");
		gate.onTokenRejected();
		gate.onTokenRejected();
		assertEquals(1, notices.stream().filter(SessionGate.NOTICE_TOKEN_REJECTED::equals).count());
		gate.onLeave();
		gate.onTokenRejected(); // a line delivered after leaving: no chat
		assertEquals(1, notices.stream().filter(SessionGate.NOTICE_TOKEN_REJECTED::equals).count());
	}

	@Test
	void theIconShowsEveryStateInOrder() {
		gate.onJoin("play.example.com");
		assertEquals(VoiceStatus.Icon.LISTENING, status.get().icon());
		status.live(true, 0.3f, false);
		assertEquals(VoiceStatus.Icon.HEARING, status.get().icon());
		status.live(false, 0.0f, true);
		assertEquals(VoiceStatus.Icon.HEARING, status.get().icon(), "a line is open");
		status.deliveryProblem(true);
		assertEquals(VoiceStatus.Icon.DELIVERY_PROBLEM, status.get().icon());
		status.tokenRejected(true);
		assertEquals(VoiceStatus.Icon.ERROR, status.get().icon());
		status.tokenRejected(false);
		status.micError("mic: could not open");
		assertEquals(VoiceStatus.Icon.ERROR, status.get().icon());
		status.micError(null);
		status.setup(VoiceStatus.Setup.ENGINE_FAILED, "boom");
		assertEquals(VoiceStatus.Icon.ERROR, status.get().icon());
		status.setup(VoiceStatus.Setup.UNSUPPORTED, "unsupported platform");
		assertEquals(VoiceStatus.Icon.ERROR, status.get().icon());
		status.setupRunning(10);
		assertEquals(VoiceStatus.Icon.SETUP, status.get().icon());
	}
}
