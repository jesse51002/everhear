package com.voiceforgaming.vfg.speech;

/** Speech-to-text over 16 kHz mono samples. Implemented over Moonshine by {@link SpeechEngine}. */
@FunctionalInterface
public interface Transcriber {
	String transcribe(float[] samples16k);
}
