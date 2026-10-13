package com.voiceforgaming.vfg.speech;

import com.voiceforgaming.vfg.kws.Incantation;
import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.atomic.AtomicLong;

/**
 * The hotwords speech-to-text favours, a list for each stretch of speech: the AI's names, "AI" and
 * "brain"; the name of every player online (the client's tab list); every incantation phrase on the
 * server (the bridge's {@code server_phrases}, or the player's own phrases from a bridge that does
 * not send them); and a few command words. Not the incantations' aliases: they are other spellings
 * of a phrase for the keyword spotter, and a hotword boosts the real word.
 *
 * <p>Each kind of word has a boost of its own ({@link Settings}): sherpa-onnx adds it for each of the
 * word's pieces the decoder takes, and takes it all back if the word is not finished. "AI" is two
 * pieces, {@code ▁A I}, the first of them the word "A".
 *
 * <p>The sources change on the client thread (the tab list) and on {@code vfg-incantations} (the
 * phrases), each by replacing an immutable list; {@code vfg-stt} reads them through {@link #forModel},
 * which builds the hotwords string once for each change and keeps it.
 */
public final class Hotwords {
	/** What a hotword is; its kind sets its boost. */
	public enum Kind { AI, BRAIN, NAME, PHRASE, COMMAND }

	/** One hotword: the text written for the model, and its kind. */
	public record Entry(String text, Kind kind) {}

	/**
	 * How the list is built and boosted: the boost of each kind (the score sherpa-onnx adds for each
	 * piece), the command words, whether "brain" is also listed as "Brain", as the model writes it at
	 * the start of a sentence, the most names and phrases kept, and whether a name is cut into the
	 * words the model would write ({@link #nameWords}) or kept whole.
	 */
	public record Settings(double ai, double brain, double name, double phrase, double command, List<String> commands,
			boolean capitalBrain, int maxNames, int maxPhrases, boolean splitNames, double longName,
			int longNamePieces, double oneWordPhrase) {
		public Settings {
			commands = List.copyOf(commands);
		}

		/** Names cut into words, a long name boosted as any other. */
		public Settings(double ai, double brain, double name, double phrase, double command, List<String> commands,
				boolean capitalBrain, int maxNames, int maxPhrases) {
			this(ai, brain, name, phrase, command, commands, capitalBrain, maxNames, maxPhrases, true, name,
				Integer.MAX_VALUE, phrase);
		}

		/** A phrase's boost: {@link #oneWordPhrase} for a phrase of one word. */
		public double phraseBoost(String phraseText) {
			return phraseText.indexOf(' ') < 0 ? oneWordPhrase : phrase;
		}

		/** A name's boost: {@link #longName} for a name of {@link #longNamePieces} pieces or more. */
		public double nameBoost(int pieces) {
			return pieces >= longNamePieces ? longName : name;
		}

		public double boost(Kind kind) {
			return switch (kind) {
				case AI -> ai;
				case BRAIN -> brain;
				case NAME -> name;
				case PHRASE -> phrase;
				case COMMAND -> command;
			};
		}

		public Settings withBoosts(double ai, double brain, double name, double phrase, double command) {
			return new Settings(ai, brain, name, phrase, command, commands, capitalBrain, maxNames, maxPhrases,
				splitNames, longNamePieces == Integer.MAX_VALUE ? name : longName, longNamePieces, phrase);
		}

		public Settings withCommands(List<String> commands) {
			return new Settings(ai, brain, name, phrase, command, commands, capitalBrain, maxNames, maxPhrases,
				splitNames, longName, longNamePieces, oneWordPhrase);
		}

		public Settings withCapitalBrain(boolean on) {
			return new Settings(ai, brain, name, phrase, command, commands, on, maxNames, maxPhrases, splitNames,
				longName, longNamePieces, oneWordPhrase);
		}

		public Settings withSplitNames(boolean on) {
			return new Settings(ai, brain, name, phrase, command, commands, capitalBrain, maxNames, maxPhrases, on,
				longName, longNamePieces, oneWordPhrase);
		}

		/** A name of {@code pieces} pieces or more boosted by {@code boost} a piece. */
		public Settings withLongNames(double boost, int pieces) {
			return new Settings(ai, brain, name, phrase, command, commands, capitalBrain, maxNames, maxPhrases,
				splitNames, boost, pieces, oneWordPhrase);
		}

		/** A phrase of one word boosted by {@code boost} a piece. */
		public Settings withOneWordPhrases(double boost) {
			return new Settings(ai, brain, name, phrase, command, commands, capitalBrain, maxNames, maxPhrases,
				splitNames, longName, longNamePieces, boost);
		}
	}

	/** The AI's two names (Jesse, 2026-10-12). */
	public static final String AI = "AI";
	public static final String BRAIN = "brain";

	/**
	 * The command words, chosen by {@code ./gradlew sttCompare} on the addressing set: each came through
	 * more often boosted ("mobs" 4 of 8 lines unboosted, 7 boosted; "incantations" on the 110M 6 of 24,
	 * then 24) and none made a false word that greedy search did not.
	 */
	public static final List<String> COMMAND_WORDS = List.of("unbind", "incantation", "incantations", "aura",
		"cooldown", "mobs", "spell", "spells");

	/**
	 * The mod's settings, chosen by {@code ./gradlew sttCompare} on the committed addressing and Minecraft
	 * sets (client-mod/README.md): every kind at 1.0 a piece. Above it nothing came through more often on
	 * the 0.6B, and false words began: "brain" for "rain" at 1.5, false names on the 110M at 1.5 ("Steve" for
	 * "steak"), false "AI" at 2.5. "Brain" with a capital is not listed: its {@code ▁B} turned "Spawn" into
	 * "Bawn" in 11 of the Minecraft set's lines.
	 */
	public static final Settings DEFAULT = new Settings(1.0, 1.0, 1.0, 1.0, 1.0, COMMAND_WORDS, false, 200, 300);

	/**
	 * Parakeet 0.6B's: the same, but a name of 4 pieces or more at 2.5 a piece. On the committed sets it
	 * heard more names (65% of the lines to 71%) and no false one; on the playtest's own players, 31% to
	 * 46%. On the 110M the same made false names (6 in 501 lines with none), so it keeps {@link #DEFAULT}.
	 */
	public static final Settings PARAKEET_06B = DEFAULT.withLongNames(2.5, 4);

	private final Settings settings;
	private volatile List<String> players = List.of();
	private volatile List<String> phrases = List.of();
	private final AtomicLong version = new AtomicLong();
	private volatile Built built;

	private record Built(long version, ParakeetPieces pieces, Settings settings, String text, int words) {}

	public Hotwords() {
		this(DEFAULT);
	}

	public Hotwords(Settings settings) {
		this.settings = settings;
	}

	public Settings settings() {
		return settings;
	}

	/** The names of the players online, the client's tab list; on the client thread. Returns whether it changed. */
	public boolean players(Collection<String> names) {
		List<String> next = names == null ? List.of() : names.stream().filter(n -> n != null && !n.isBlank())
			.map(String::strip).distinct().sorted().toList();
		if (next.equals(players)) {
			return false;
		}
		players = next;
		version.incrementAndGet();
		return true;
	}

	/**
	 * Every incantation phrase on the server, from the bridge's {@code server_phrases}; when the
	 * bridge sends none ({@code null}: an older bridge), the player's own phrases. On {@code vfg-incantations}.
	 */
	public boolean phrases(List<String> serverPhrases, List<Incantation> own) {
		List<String> source = serverPhrases != null ? serverPhrases
			: own == null ? List.of() : own.stream().map(Incantation::phrase).toList();
		List<String> next = source.stream().filter(p -> p != null && !p.isBlank())
			.map(p -> p.strip().toLowerCase(Locale.ROOT)).distinct().toList();
		if (next.equals(phrases)) {
			return false;
		}
		phrases = next;
		version.incrementAndGet();
		return true;
	}

	/** Forgets the players and phrases (the player left the server). */
	public void clear() {
		players(List.of());
		phrases(null, List.of());
	}

	public List<String> players() {
		return players;
	}

	public List<String> phrases() {
		return phrases;
	}

	/**
	 * Every hotword in order (the AI's names, the players', the phrases, the command words), each
	 * written as the model writes it, without repeats; a name or phrase with nothing the model can
	 * spell is left out.
	 */
	public List<Entry> entries() {
		Map<String, Entry> out = new LinkedHashMap<>();
		add(out, AI, Kind.AI);
		add(out, BRAIN, Kind.BRAIN);
		if (settings.capitalBrain()) {
			add(out, "Brain", Kind.BRAIN);
		}
		int names = 0;
		for (String player : players) {
			String written = settings.splitNames() ? nameWords(player) : wholeName(player);
			if (written != null && names < settings.maxNames()) {
				add(out, written, Kind.NAME);
				names++;
			}
		}
		int count = 0;
		for (String phrase : phrases) {
			String written = phraseWords(phrase);
			if (written == null || count >= settings.maxPhrases()) {
				continue;
			}
			add(out, written, Kind.PHRASE);
			count++;
		}
		for (String command : settings.commands()) {
			add(out, command, Kind.COMMAND);
		}
		return List.copyOf(out.values());
	}

	/** The same text twice keeps the higher boost. */
	private void add(Map<String, Entry> out, String text, Kind kind) {
		Entry had = out.get(text);
		if (had == null || settings.boost(kind) > settings.boost(had.kind())) {
			out.put(text, new Entry(text, kind));
		}
	}

	/**
	 * A player name as the model would write it: cut where the name changes from lowercase to a
	 * capital ({@code KorbinJM} → {@code Korbin JM}), and at anything but a letter (an underscore,
	 * a digit, a dot: {@code Steve_123} → {@code Steve}), so each part is a word the model can
	 * end; digits are dropped, as players seldom say them. {@code null} when fewer than two letters are left.
	 */
	public static String nameWords(String name) {
		if (name == null) {
			return null;
		}
		StringBuilder b = new StringBuilder();
		int prev = -1;
		for (int i = 0; i < name.length(); ) {
			int cp = name.codePointAt(i);
			i += Character.charCount(cp);
			if (!Character.isLetter(cp)) {
				b.append(' ');
				prev = -1;
				continue;
			}
			if (prev != -1 && Character.isLowerCase(prev) && Character.isUpperCase(cp)) {
				b.append(' ');
			}
			b.appendCodePoint(cp);
			prev = cp;
		}
		String out = b.toString().trim().replaceAll("\\s+", " ");
		return out.codePoints().filter(Character::isLetter).count() >= 2 ? out : null;
	}

	/** A name kept whole, as the tab list spells it, but for anything not a letter or digit; {@code null} if nothing is left. */
	static String wholeName(String name) {
		String out = name.replaceAll("[^\\p{L}\\p{N}]+", " ").trim();
		return out.isEmpty() ? null : out;
	}

	/** An incantation phrase as a hotword: lowercase words of letters, digits and apostrophes; {@code null} if none. */
	public static String phraseWords(String phrase) {
		if (phrase == null) {
			return null;
		}
		String out = phrase.toLowerCase(Locale.ROOT).replaceAll("[^\\p{L}\\p{N}']+", " ").trim();
		return out.isEmpty() ? null : out;
	}

	/**
	 * The hotwords string for one stream of a Parakeet model, as {@code OfflineRecognizer.createStream}
	 * takes it: one hotword after another, separated by {@code /}, each its pieces' code names
	 * ({@link ParakeetPieces#coded}) and {@code :<boost>}. A word holding a character the model
	 * cannot spell loses that character; one left with nothing is left out. Built once for each
	 * change and kept; on {@code vfg-stt}.
	 */
	public String forModel(ParakeetPieces pieces) {
		return forModel(pieces, settings);
	}

	/** The same with another model's {@code boosts} ({@link #PARAKEET_06B}); the lists, the command words and the caps are this one's. */
	public String forModel(ParakeetPieces pieces, Settings boosts) {
		long v = version.get();
		Built b = built;
		if (b != null && b.version() == v && b.pieces() == pieces && b.settings() == boosts) {
			return b.text();
		}
		StringBuilder out = new StringBuilder();
		int words = 0;
		for (Entry e : entries()) {
			String coded = pieces.coded(spellable(e.text(), pieces));
			if (coded == null) {
				continue;
			}
			double boost = switch (e.kind()) {
				case NAME -> boosts.nameBoost(coded.split(" ").length);
				case PHRASE -> boosts.phraseBoost(e.text());
				default -> boosts.boost(e.kind());
			};
			out.append(out.isEmpty() ? "" : "/").append(coded).append(" :")
				.append(String.format(Locale.ROOT, "%.2f", boost));
			words++;
		}
		Built next = new Built(v, pieces, boosts, out.toString(), words);
		built = next;
		return next.text();
	}

	/** How many hotwords the last {@link #forModel} string holds. */
	public int lastWordCount() {
		Built b = built;
		return b == null ? 0 : b.words();
	}

	/** {@code text} with each character the model cannot spell replaced by a space. */
	static String spellable(String text, ParakeetPieces pieces) {
		StringBuilder b = new StringBuilder();
		text.codePoints().forEach(cp -> {
			if (cp == ' ' || pieces.hasCharacter(cp)) {
				b.appendCodePoint(cp);
			} else {
				b.append(' ');
			}
		});
		return b.toString().trim();
	}
}
