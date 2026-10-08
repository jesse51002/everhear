package com.voiceforgaming.vfg.session;

import com.voiceforgaming.vfg.audio.WindowListener.StopReason;
import com.voiceforgaming.vfg.config.VfgConfig;
import java.util.HashSet;
import java.util.Set;
import java.util.function.BooleanSupplier;
import java.util.function.Supplier;

/**
 * Whether to listen, and the chat notices that go with it.
 *
 * <p>The mod listens when it is connected, the joined address matches
 * {@code server_address}, the token is set, setup is ready and it is not muted —
 * re-checked on join, leave, settings saved, mute, and setup finished. Singleplayer and
 * LAN worlds never listen: they join with a {@code null} address.
 */
public final class SessionGate {
	/** Opens and closes the mic for listening. */
	public interface Listening {
		void start();

		void stop(StopReason reason);
	}

	/** A local chat notice, by translation key. Never sent as chat. */
	@FunctionalInterface
	public interface Notices {
		void notice(String key);
	}

	public static final String NOTICE_LISTENING_INTRO = "vfg.chat.listening_intro";
	public static final String NOTICE_NO_TOKEN = "vfg.chat.no_token";
	public static final String NOTICE_NOT_SET_UP = "vfg.chat.not_set_up";
	public static final String NOTICE_TOKEN_REJECTED = "vfg.chat.token_rejected";
	public static final String NOTICE_MUTED = "vfg.chat.muted";
	public static final String NOTICE_LISTENING = "vfg.chat.listening";
	public static final String NOTICE_UNMUTED = "vfg.chat.unmuted";

	private final Supplier<VfgConfig> config;
	private final BooleanSupplier setupReady;
	private final Listening listening;
	private final Notices notices;
	private final StatusBoard status;

	private boolean connected;
	private String joinedAddress;
	private boolean active;
	private final Set<String> greeted = new HashSet<>();
	private final Set<String> warnedNoToken = new HashSet<>();
	private boolean warnedNotSetUp;
	private boolean warnedTokenRejected;

	public SessionGate(Supplier<VfgConfig> config, BooleanSupplier setupReady, Listening listening, Notices notices,
			StatusBoard status) {
		this.config = config;
		this.setupReady = setupReady;
		this.listening = listening;
		this.notices = notices;
		this.status = status;
	}

	public synchronized boolean listening() {
		return active;
	}

	public synchronized boolean connected() {
		return connected;
	}

	/** The joined server's address as typed in the server list; {@code null} for singleplayer and LAN. */
	public synchronized String joinedAddress() {
		return joinedAddress;
	}

	/** Joined a world. {@code address} is {@code null} in singleplayer and on LAN worlds. */
	public synchronized void onJoin(String address) {
		if (connected) {
			stopNow(StopReason.LEAVE);
		}
		connected = true;
		joinedAddress = address;
		warnedTokenRejected = false;
		evaluate(false);
	}

	/** Left the world: stop capturing at once; what was captured is still delivered. */
	public synchronized void onLeave() {
		connected = false;
		joinedAddress = null;
		stopNow(StopReason.LEAVE);
		publish(false);
	}

	/** Settings saved, setup finished or failed, or anything else that may change the answer. */
	public synchronized void recheck() {
		evaluate(false);
	}

	/** M was pressed and {@code muted} has already been flipped and saved. */
	public synchronized void onMuteToggled() {
		boolean muted = config.get().muted;
		evaluate(true);
		if (muted) {
			notices.notice(NOTICE_MUTED);
		} else if (!active) {
			notices.notice(NOTICE_UNMUTED);
		}
	}

	/** The bridge answered 401: once per connection, tell the player. */
	public synchronized void onTokenRejected() {
		if (connected && !warnedTokenRejected) {
			warnedTokenRejected = true;
			notices.notice(NOTICE_TOKEN_REJECTED);
		}
	}

	private boolean matching(VfgConfig c) {
		return connected && joinedAddress != null
			&& ServerAddressMatch.matches(c.effectiveServerAddress(), joinedAddress);
	}

	private void evaluate(boolean fromMute) {
		VfgConfig c = config.get();
		boolean matching = matching(c);
		boolean want = matching && c.hasToken() && setupReady.getAsBoolean() && !c.muted;
		if (want && !active) {
			active = true;
			listening.start();
			String key = ServerAddressMatch.normalize(joinedAddress);
			if (greeted.add(key)) {
				notices.notice(NOTICE_LISTENING_INTRO);
			} else if (fromMute) {
				notices.notice(NOTICE_LISTENING);
			}
		} else if (!want && active) {
			stopNow(StopReason.DISCARD);
		}
		if (matching && !c.hasToken() && warnedNoToken.add(ServerAddressMatch.normalize(joinedAddress))) {
			notices.notice(NOTICE_NO_TOKEN);
		}
		if (connected && joinedAddress != null && isBlank(c.effectiveServerAddress()) && !warnedNotSetUp) {
			warnedNotSetUp = true;
			notices.notice(NOTICE_NOT_SET_UP);
		}
		publish(matching);
	}

	private void stopNow(StopReason reason) {
		if (active) {
			active = false;
			listening.stop(reason);
		}
	}

	private void publish(boolean matching) {
		VfgConfig c = config.get();
		status.session(matching, active, c.muted, !c.hasToken());
	}

	private static boolean isBlank(String s) {
		return s == null || s.isBlank();
	}
}
