#!/usr/bin/env python3
"""The fixture the mod's SentencePiece encoder is checked against: how Python's sentencepiece
splits the keyword spotter's phrases into the model's tokens.

    uv run --with sentencepiece tools/kws_bpe_fixture.py [--model <bpe.model>]

<bpe.model> is the keyword model's own (default: where the engine test installs it,
build/vfg-test-assets/models/kws/<model>/bpe.model). It writes two test resources:

- src/test/resources/kws/bpe-fixture.tsv: one row per phrase, the phrase as the mod gets it,
  then its pieces, space-separated, as sentencepiece encodes the phrase normalised as the mod
  normalises it (upper case; letters, digits and apostrophes kept; anything else a space).
- src/test/resources/kws/bpe-pieces.model: the model's pieces and its trainer spec only. It is the
  model's own protobuf with every record but its pieces (field 1) and trainer spec (field 2, which
  says it is a unigram model, despite the file's name) left out: the normaliser's tables are most of
  the 245 KB, and the mod does its own normalising, so the test needs only these.
"""

import argparse
import re
import sys
from pathlib import Path

import sentencepiece

HERE = Path(__file__).resolve().parent.parent
MODEL = "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01"
DEFAULT_MODEL = HERE / "build/vfg-test-assets/models/kws" / MODEL / "bpe.model"
OUT = HERE / "src/test/resources/kws"

PHRASES = [
    # the spec's game words, their aliases and the phrase run together or split
    "fire ball", "fireball", "fire bowl", "kamehameha", "kah meh hah meh hah", "kameha meha",
    "kame hame ha", "come a hammer ha", "kami hami ha", "ice wall", "icewall", "galick gun",
    "garlic gun", "final flash", "big bang attack", "spirit bomb", "masenko", "special beam cannon",
    "hadouken", "shoryuken", "fus ro dah", "expecto patronum", "avada kedavra", "wingardium leviosa",
    "abracadabra", "lightning bolt", "thunder strike", "summon golem", "heal me now",
    # ordinary words and sentences
    "build me a castle by the cave", "the quick brown fox jumps over the lazy dog", "hello world",
    "light up", "merry christmas", "creeper", "netherite", "redstone", "ender dragon", "diamonds",
    "a", "i",
    # what the mod normalises: case, spacing, punctuation, apostrophes, digits
    "Fire  Ball!", "  ice-wall  ", "don't cast fire ball", "level 3",
]


def normalize(text):
    """The mod's own normalising: upper case; A-Z, 0-9 and ' kept; anything else a space."""
    return " ".join(re.sub(r"[^A-Z0-9']", " ", text.upper()).split())


def varint(data, i):
    value = shift = 0
    while True:
        b = data[i]
        i += 1
        value |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            return value, i


def pieces_only(data):
    """The ModelProto's pieces (field 1) and trainer spec (field 2), copied byte for byte; the rest dropped."""
    out = bytearray()
    i = 0
    while i < len(data):
        start = i
        key, i = varint(data, i)
        field, wire = key >> 3, key & 7
        if wire == 0:
            _, i = varint(data, i)
        elif wire == 1:
            i += 8
        elif wire == 2:
            size, i = varint(data, i)
            i += size
        elif wire == 5:
            i += 4
        else:
            raise ValueError("wire type %d at byte %d" % (wire, start))
        if field in (1, 2):
            out += data[start:i]
    return bytes(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Writes the SentencePiece encoder's test fixture.")
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    args = parser.parse_args(argv)
    if not args.model.is_file():
        print("no %s: run ./gradlew kwsTest once to install the keyword model" % args.model, file=sys.stderr)
        return 2
    sp = sentencepiece.SentencePieceProcessor(model_file=str(args.model))
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for phrase in PHRASES:
        rows.append("%s\t%s\n" % (phrase, " ".join(sp.encode(normalize(phrase), out_type=str))))
    (OUT / "bpe-fixture.tsv").write_text("".join(rows), encoding="utf-8")
    trimmed = pieces_only(args.model.read_bytes())
    (OUT / "bpe-pieces.model").write_bytes(trimmed)
    print("wrote %d phrases and %d pieces (%d bytes) to %s" % (len(rows), sp.get_piece_size(), len(trimmed), OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
