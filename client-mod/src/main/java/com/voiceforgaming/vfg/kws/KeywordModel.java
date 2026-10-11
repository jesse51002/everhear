package com.voiceforgaming.vfg.kws;

import java.util.Set;

/**
 * The keyword model as the spotter thread uses it: {@link Spotter} is sherpa-onnx's, and the
 * tests have stand-ins. A stream is fed 16 kHz audio, decoded a chunk at a time while one is
 * ready, and asked for a hit after each chunk; after a hit it is reset, as sherpa-onnx requires.
 */
public interface KeywordModel extends AutoCloseable {
	/** One keyword heard: its tag, and each of its tokens' time in seconds, as the model counts them. */
	record Hit(String keyword, float[] timestamps) {}

	/** A stream that listens for the keywords of one {@link Keywords#spec()}. */
	interface Stream extends AutoCloseable {
		void accept(float[] samples16k);

		/** Whether a whole chunk is ready to decode. */
		boolean ready();

		void decode();

		/** The keyword the last chunk ended, or {@code null}. */
		Hit hit();

		/** Starts listening afresh after a hit. */
		void reset();

		@Override
		void close();
	}

	/** A stream for {@code keywords}; {@link IllegalArgumentException} when the model refuses them. */
	Stream open(String keywords);

	/** The model's own SentencePiece encoding, for making keywords. */
	SentencePiece pieces();

	/** The model's tokens, from its {@code tokens.txt}. */
	Set<String> tokens();

	/** The output frames a decoded chunk moves the stream on by. */
	int chunkFrames();

	/** The blank frames after a keyword's last token that make it fire. */
	int trailingBlanks();

	@Override
	void close();
}
