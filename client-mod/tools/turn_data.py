#!/usr/bin/env python3
"""The public set for ./gradlew turnCompare: the English clips of one pinned shard of
pipecat-ai/smart-turn-data-v3.2-test (CC-BY-4.0), as 16 kHz mono WAVs and one labels file.

    uv run --with pyarrow --with soundfile tools/turn_data.py [--root build/vfg-test-assets]

Downloads the shard (486 MB) once into <root>/turn-data/downloads/, checks its size and SHA-256,
writes <root>/turn-data/public/<id>.wav and <root>/turn-data/public/labels.tsv, and then deletes
the shard. Run again, it does nothing: labels.tsv is written last, with a marker beside it that
holds the shard's SHA-256. The clips are written as they are; turnCompare frames them.

labels.tsv has one row per clip: file, finished (1 when the speaker has finished), midfiller and
endfiller (1, 0, or empty where the set leaves them unset), synthetic (1 for TTS), dataset (the
set's own source name) and seconds.
"""

import argparse
import hashlib
import io
import shutil
import sys
import urllib.request
from pathlib import Path

REPO = "pipecat-ai/smart-turn-data-v3.2-test"
REVISION = "dfc179241fee65b0efe81ca37786f41ce26f38ec"
SHARD = "data/train-00000-of-00010.parquet"
SHARD_BYTES = 486502678
SHARD_SHA256 = "a87c75806b814ee7379998b6f9dc65a6433c01bfec2875e62c5d1ccd2b37257a"
URL = "https://huggingface.co/datasets/%s/resolve/%s/%s" % (REPO, REVISION, SHARD)
LANGUAGE = "eng"
RATE = 16000
COLUMNS = ("file", "finished", "midfiller", "endfiller", "synthetic", "dataset", "seconds")


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(downloads):
    """The shard, downloaded once and checked as it streams."""
    target = downloads / Path(SHARD).name
    if target.is_file() and target.stat().st_size == SHARD_BYTES and sha256_of(target) == SHARD_SHA256:
        return target
    downloads.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    print("downloading %s (%d MB)" % (URL, SHARD_BYTES // 1_000_000), flush=True)
    digest = hashlib.sha256()
    done = 0
    tenth = -1
    with urllib.request.urlopen(URL, timeout=60) as response, open(part, "wb") as out:
        for block in iter(lambda: response.read(1 << 20), b""):
            out.write(block)
            digest.update(block)
            done += len(block)
            if done * 10 // SHARD_BYTES != tenth:
                tenth = done * 10 // SHARD_BYTES
                print("  %d%%" % (tenth * 10), flush=True)
    if done != SHARD_BYTES or digest.hexdigest() != SHARD_SHA256:
        part.unlink()
        sys.exit("%s: %d bytes, SHA-256 %s; expected %d bytes, %s"
                 % (SHARD, done, digest.hexdigest(), SHARD_BYTES, SHARD_SHA256))
    part.replace(target)
    return target


def flag(value):
    return "" if value is None else "1" if value else "0"


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", default="build/vfg-test-assets", help="the test assets folder")
    args = parser.parse_args()
    data = Path(args.root) / "turn-data"
    public = data / "public"
    labels = public / "labels.tsv"
    marker = public / "labels.ok"
    if labels.is_file() and marker.is_file() and marker.read_text().strip() == SHARD_SHA256:
        rows = sum(1 for _ in labels.open()) - 1
        print("public set: %d clips already in %s" % (rows, public))
        return

    # Imported here, so the run above that finds everything in place needs neither.
    import numpy as np
    import pyarrow.parquet as pq
    import soundfile as sf

    shard = fetch(data / "downloads")
    if public.exists():
        shutil.rmtree(public)
    public.mkdir(parents=True)
    rows = []
    parquet = pq.ParquetFile(shard)
    for group in range(parquet.num_row_groups):
        table = parquet.read_row_group(group, columns=[
            "audio", "id", "language", "endpoint_bool", "midfiller", "endfiller", "synthetic", "dataset"])
        for r in table.to_pylist():
            if r["language"] != LANGUAGE:
                continue
            audio, rate = sf.read(io.BytesIO(r["audio"]["bytes"]), dtype="float32", always_2d=True)
            if rate != RATE:
                sys.exit("%s is %d Hz; every clip of this shard is %d Hz" % (r["id"], rate, RATE))
            audio = audio.mean(axis=1)
            name = r["id"] + ".wav"
            sf.write(public / name, np.clip(audio, -1.0, 1.0), RATE, subtype="PCM_16")
            rows.append((name, flag(r["endpoint_bool"]), flag(r["midfiller"]), flag(r["endfiller"]),
                         flag(r["synthetic"]), r["dataset"], "%.3f" % (len(audio) / RATE)))
    part = labels.with_name(labels.name + ".part")
    with open(part, "w", newline="\n") as f:
        f.write("\t".join(COLUMNS) + "\n")
        for row in sorted(rows):
            f.write("\t".join(row) + "\n")
    part.replace(labels)
    marker.write_text(SHARD_SHA256 + "\n")
    shard.unlink()
    finished = sum(1 for row in rows if row[1] == "1")
    print("public set: %d English clips (%d finished, %d not) in %s"
          % (len(rows), finished, len(rows) - finished, public))


if __name__ == "__main__":
    main()
