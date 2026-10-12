package com.voiceforgaming.vfg.kws;

import java.util.List;

/**
 * Vosk as {@code vfg-vosk} uses it: {@link Vosk} is the real one, and the tests have stand-ins. A
 * recognizer listens for the words of one grammar, is fed 16 kHz 16-bit audio, and is asked for
 * its partial text after each piece; when Vosk ends an utterance in a piece (its own endpointing,
 * after a pause), the piece's answer is the utterance's final words instead, with their times,
 * and the next piece starts a new utterance.
 */
public interface VoskModel extends AutoCloseable {
	/** A word of a final result, from {@code start} to {@code end} seconds into all the audio its recognizer was fed. */
	record Word(String word, double start, double end) {}

	/** A recognizer for one grammar. */
	interface Recognizer extends AutoCloseable {
		/** Feeds samples; {@code true} when Vosk ended an utterance in them: then read {@link #result()}. */
		boolean accept(short[] samples);

		/** The utterance's words so far, as Vosk has them now, lower case and separated by spaces. */
		String partial();

		/** The utterance's final words with their times, once {@link #accept} said it ended. */
		List<Word> result();

		/**
		 * Forgets the utterance, for audio that does not follow on. Its times still count every
		 * sample it was ever fed, those before the reset too.
		 */
		void reset();

		@Override
		void close();
	}

	/** A recognizer for {@code grammarJson}, a JSON list of phrases, every word of which the model knows. */
	Recognizer recognizer(String grammarJson);

	/** Whether the model knows {@code word} (lower case). */
	boolean knows(String word);

	@Override
	void close();
}
