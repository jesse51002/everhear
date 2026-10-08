package com.voiceforgaming.vfg.session;

import java.util.Locale;

/**
 * Normalising and comparing server addresses, as typed in the server list.
 *
 * <p>Both sides are trimmed and lowercased, a trailing dot on the host is dropped, and
 * {@code :25565} is added when there is no port (an IPv6 host is in brackets). Equal
 * strings match. A blank address matches nothing.
 */
public final class ServerAddressMatch {
	public static final int DEFAULT_PORT = 25565;

	private ServerAddressMatch() {}

	public static boolean matches(String configured, String joined) {
		String a = normalize(configured);
		String b = normalize(joined);
		return a != null && a.equals(b);
	}

	/** The normalised {@code host:port}, or {@code null} for a blank or unusable address. */
	public static String normalize(String address) {
		HostPort hp = parse(address);
		return hp == null ? null : hp.host + ":" + hp.port;
	}

	/**
	 * The host part as it goes in a URL: lowercased, no trailing dot, an IPv6 host in
	 * brackets. {@code null} for a blank address.
	 */
	public static String host(String address) {
		HostPort hp = parse(address);
		return hp == null ? null : hp.host;
	}

	private record HostPort(String host, String port) {}

	private static HostPort parse(String address) {
		if (address == null) {
			return null;
		}
		String s = address.trim().toLowerCase(Locale.ROOT);
		if (s.isEmpty()) {
			return null;
		}
		String host;
		String port = null;
		if (s.startsWith("[")) {
			int close = s.indexOf(']');
			if (close < 0) {
				return null;
			}
			host = s.substring(0, close + 1);
			String rest = s.substring(close + 1);
			if (rest.startsWith(":") && rest.length() > 1) {
				port = rest.substring(1);
			} else if (!rest.isEmpty()) {
				return null;
			}
		} else {
			int first = s.indexOf(':');
			int last = s.lastIndexOf(':');
			if (first >= 0 && first != last) {
				host = "[" + s + "]"; // a bare IPv6 address has no port
			} else if (first >= 0) {
				host = s.substring(0, first);
				port = s.substring(first + 1);
			} else {
				host = s;
			}
		}
		while (host.endsWith(".")) {
			host = host.substring(0, host.length() - 1);
		}
		if (host.isEmpty() || host.equals("[]")) {
			return null;
		}
		if (port == null || port.isEmpty()) {
			port = Integer.toString(DEFAULT_PORT);
		} else {
			try {
				port = Integer.toString(Integer.parseInt(port));
			} catch (NumberFormatException e) {
				return null;
			}
		}
		return new HostPort(host, port);
	}
}
