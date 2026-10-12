package com.voiceforgaming.vfg.kws;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * The keyword spotter's keywords for a player's incantations: every phrase and every alias is
 * a keyword of its own, in the form sherpa-onnx's {@code KeywordSpotter.createStream(String)}
 * takes, keywords separated by {@code /}:
 *
 * <pre>▁FI RE ▁BA LL :1.5 #0.25 @fire_ball/▁FI RE B AL L :1.5 #0.25 @fireball</pre>
 *
 * The model's tokens come first ({@link SentencePiece}), then {@code :boost}, the score added for
 * each token of a keyword while decoding, {@code #threshold}, the mean token probability at which
 * it fires, and {@code @tag}, which the spotter answers with. The tag is the keyword itself, normalised,
 * with {@code _} for its spaces (sherpa-onnx splits on whitespace), and it maps back to the
 * incantation's phrase: a hit on an alias casts the phrase.
 *
 * <p>A keyword is left out, and listed in {@link #skipped()}, when it has no tokens or a token the
 * model does not have (a digit, an accented letter), since sherpa-onnx refuses the whole string
 * for one unknown token. A keyword that two incantations share goes to the first, which is the
 * player's own before an {@code @a} one in the bridge's list.
 *
 * <p>A keyword whose tokens start another keyword's ("fire" and "fire ball", or "fire" and the
 * alias "fireball") is {@link #startsAnother}: said so far, it may be the start of the longer one,
 * and {@link #specStartingNone()} leaves it out.
 */
public final class Keywords {
	/** What a keyword casts: the incantation's phrase, and the keyword heard (the phrase or an alias). */
	public record Target(String phrase, String heard) {}

	public static final Keywords NONE = new Keywords("", "", Map.of(), List.of(), Set.of());

	private final String spec;
	private final String specStartingNone;
	private final Map<String, Target> byTag;
	private final List<String> skipped;
	private final Set<String> startsAnother;

	private Keywords(String spec, String specStartingNone, Map<String, Target> byTag, List<String> skipped,
			Set<String> startsAnother) {
		this.spec = spec;
		this.specStartingNone = specStartingNone;
		this.byTag = byTag;
		this.skipped = skipped;
		this.startsAnother = startsAnother;
	}

	/**
	 * The keywords for {@code incantations}, split by {@code pieces}, keeping only those whose tokens
	 * are all in {@code tokens} (the model's {@code tokens.txt}).
	 */
	public static Keywords build(List<Incantation> incantations, SentencePiece pieces, Set<String> tokens,
			double threshold, double boost) {
		List<String> spec = new ArrayList<>();
		Map<String, Target> byTag = new LinkedHashMap<>();
		Map<String, List<String>> split = new LinkedHashMap<>();
		List<String> skipped = new ArrayList<>();
		String tail = " :" + number(boost) + " #" + number(threshold) + " @";
		for (Incantation inc : incantations) {
			String phrase = words(inc.phrase());
			List<String> targets = new ArrayList<>();
			targets.add(inc.phrase());
			targets.addAll(inc.aliases());
			for (String target : targets) {
				String heard = words(target);
				String tag = heard.replace(' ', '_');
				if (heard.isEmpty() || byTag.containsKey(tag)) {
					continue;
				}
				List<String> pieced = pieces.encode(target);
				if (pieced.isEmpty() || !tokens.containsAll(pieced)) {
					skipped.add(heard);
					continue;
				}
				spec.add(String.join(" ", pieced) + tail + tag);
				byTag.put(tag, new Target(phrase.isEmpty() ? heard : phrase, heard));
				split.put(tag, pieced);
			}
		}
		Set<String> startsAnother = new HashSet<>();
		List<String> startingNone = new ArrayList<>();
		int i = 0;
		for (Map.Entry<String, List<String>> a : split.entrySet()) {
			for (List<String> b : split.values()) {
				if (b.size() > a.getValue().size() && b.subList(0, a.getValue().size()).equals(a.getValue())) {
					startsAnother.add(a.getKey());
				}
			}
			if (!startsAnother.contains(a.getKey())) {
				startingNone.add(spec.get(i));
			}
			i++;
		}
		return new Keywords(String.join("/", spec), String.join("/", startingNone), Map.copyOf(byTag),
			List.copyOf(skipped), Set.copyOf(startsAnother));
	}

	/** The string for {@code createStream} without the keywords that start another: what a re-check listens for. */
	public String specStartingNone() {
		return specStartingNone;
	}

	/** Whether some keyword starts another. */
	public boolean anyStartsAnother() {
		return !startsAnother.isEmpty();
	}

	/** Whether the keyword with this tag starts another keyword: its tokens are the first of the other's. */
	public boolean startsAnother(String keyword) {
		return keyword != null && startsAnother.contains(keyword);
	}

	/** Lower case, the normalised words with single spaces: how a keyword is shown and sent as {@code heard}. */
	static String words(String text) {
		return SentencePiece.normalize(text).toLowerCase(Locale.ROOT);
	}

	private static String number(double v) {
		return BigDecimal.valueOf(v).stripTrailingZeros().toPlainString();
	}

	/** The string for {@code createStream}; empty when there are no keywords. */
	public String spec() {
		return spec;
	}

	public boolean isEmpty() {
		return byTag.isEmpty();
	}

	/** How many keywords there are. */
	public int size() {
		return byTag.size();
	}

	/** What the spotter's answer ({@code KeywordSpotterResult.getKeyword()}, the tag) casts, or {@code null}. */
	public Target target(String keyword) {
		return keyword == null ? null : byTag.get(keyword);
	}

	/** The keywords left out, as words. */
	public List<String> skipped() {
		return skipped;
	}

	@Override
	public String toString() {
		return "Keywords[" + byTag.size() + (skipped.isEmpty() ? "" : ", skipped " + skipped) + "]";
	}
}
