# Turn model comparison

Smart Turn v3.2 is the mod's default turn model; turn-1-mini is measured against it. Written by `./gradlew turnCompare` on 2026-10-09, on AMD Ryzen AI 5 340 w/ Radeon 840M.

## Method

Each clip is given to each model as the mod gives it a line at a pause: from 0.5 s before the first speech the mod's own VAD finds, to 0.2 s after the last, with a quiet -60 dBFS floor wherever the clip has no audio of its own. A score at or above the threshold means finished. **Public set:** the 789 English clips (388 finished, 401 not; 538 of real people, 251 TTS) of shard `data/train-00000-of-00010.parquet` of [`pipecat-ai/smart-turn-data-v3.2-test`](https://huggingface.co/datasets/pipecat-ai/smart-turn-data-v3.2-test) (CC-BY-4.0), revision `dfc179241fee65b0efe81ca37786f41ce26f38ec`, prepared by `tools/turn_data.py`. It is Smart Turn's own test set, held out from speech like what it was trained on, so it favours Smart Turn. **Game set:** the 118 direct asks of 4 to 12 words that players said in the 2026-10-08 playtest, as the mod's speech-to-text heard them, spoken by Kokoro TTS (int8, English) in all 11 voices: whole (finished); cut at a gap between words near the middle, the first half only (not finished); and said again with "uh" or "um" in that gap, cut just after the filler (not finished). Gaps come from Parakeet's word timestamps, and every cut is checked by transcribing both halves. The same asks spoken by Kitten, the speech test's TTS, are scored too: Smart Turn hears most of Kitten's finished sentences as unfinished (below), so the game set is Kokoro's. turn-1-mini's threshold, **0.35**, is the one at which it cuts off the public set's unfinished clips as often as Smart Turn does at 0.5. Time per score is wall time on this PC, one score at a time, with both models loaded as the mod loads one.

## Results

| Model | Threshold | Set | Clips | Accuracy | Cut off: unfinished scored finished | Kept waiting: finished scored unfinished | Time per score, median / p95 |
|---|---|---|---|---|---|---|---|
| Smart Turn v3.2 | 0.50 | public | 789 | 92.3% (728 of 789) | 10.0% (40 of 401) | 5.4% (21 of 388) | 37.2 / 45.2 ms |
| turn-1-mini | 0.35 (picked) | public | 789 | 71.6% (565 of 789) | 10.0% (40 of 401) | 47.4% (184 of 388) | 19.6 / 28.9 ms |
| turn-1-mini | 0.50 | public | 789 | 62.5% (493 of 789) | 3.2% (13 of 401) | 72.9% (283 of 388) | 19.6 / 28.9 ms |
| Smart Turn v3.2 | 0.50 | game, Kokoro | 3548 | 74.2% (2631 of 3548) | 29.8% (670 of 2250) | 19.0% (247 of 1298) | 37.0 / 40.9 ms |
| turn-1-mini | 0.35 (picked) | game, Kokoro | 3548 | 68.4% (2426 of 3548) | 12.8% (288 of 2250) | 64.3% (834 of 1298) | 18.3 / 22.6 ms |
| turn-1-mini | 0.50 | game, Kokoro | 3548 | 65.5% (2324 of 3548) | 6.8% (153 of 2250) | 82.5% (1071 of 1298) | 18.3 / 22.6 ms |
| Smart Turn v3.2 | 0.50 | game, Kitten | 2564 | 65.0% (1666 of 2564) | 8.4% (136 of 1620) | 80.7% (762 of 944) | 38.0 / 46.6 ms |
| turn-1-mini | 0.35 (picked) | game, Kitten | 2564 | 62.5% (1603 of 2564) | 2.5% (40 of 1620) | 97.6% (921 of 944) | 20.0 / 28.0 ms |
| turn-1-mini | 0.50 | game, Kitten | 2564 | 62.8% (1611 of 2564) | 1.4% (23 of 1620) | 98.5% (930 of 944) | 20.0 / 28.0 ms |

## The game sets by form

| TTS | Model | Threshold | Whole: kept waiting | Cut: cut off | With "uh": cut off | With "um": cut off |
|---|---|---|---|---|---|---|
| Kokoro | Smart Turn v3.2 | 0.50 | 19.0% (247 of 1298) | 39.9% (481 of 1205) | 9.9% (52 of 523) | 26.2% (137 of 522) |
| Kokoro | turn-1-mini | 0.35 | 64.3% (834 of 1298) | 16.8% (202 of 1205) | 6.1% (32 of 523) | 10.3% (54 of 522) |
| Kokoro | turn-1-mini | 0.50 | 82.5% (1071 of 1298) | 9.6% (116 of 1205) | 2.3% (12 of 523) | 4.8% (25 of 522) |
| Kitten | Smart Turn v3.2 | 0.50 | 80.7% (762 of 944) | 12.7% (116 of 916) | 1.4% (5 of 353) | 4.3% (15 of 351) |
| Kitten | turn-1-mini | 0.35 | 97.6% (921 of 944) | 3.9% (36 of 916) | 0.6% (2 of 353) | 0.6% (2 of 351) |
| Kitten | turn-1-mini | 0.50 | 98.5% (930 of 944) | 2.4% (22 of 916) | 0.3% (1 of 353) | 0.0% (0 of 351) |

## Smart Turn around 0.5

| Threshold | Public: cut off | Public: kept waiting | Game, Kokoro: cut off | Game, Kokoro: kept waiting |
|---|---|---|---|---|
| 0.3 | 10.7% (43 of 401) | 3.4% (13 of 388) | 34.8% (784 of 2250) | 15.1% (196 of 1298) |
| 0.4 | 10.2% (41 of 401) | 3.6% (14 of 388) | 32.0% (720 of 2250) | 16.6% (215 of 1298) |
| 0.5 | 10.0% (40 of 401) | 5.4% (21 of 388) | 29.8% (670 of 2250) | 19.0% (247 of 1298) |
| 0.6 | 9.7% (39 of 401) | 6.7% (26 of 388) | 27.5% (619 of 2250) | 20.6% (267 of 1298) |
| 0.7 | 8.0% (32 of 401) | 7.5% (29 of 388) | 25.2% (567 of 2250) | 22.4% (291 of 1298) |

## Asked later in the pause: the public set with 0.6 s after the speech

As if the model were asked when the VAD ends the segment, 0.6 s into the pause, rather than 0.2 s in.

| Model | Threshold | Silence after the speech | Accuracy | Cut off | Kept waiting |
|---|---|---|---|---|---|
| Smart Turn v3.2 | 0.50 | 0.2 s | 92.3% (728 of 789) | 10.0% (40 of 401) | 5.4% (21 of 388) |
| Smart Turn v3.2 | 0.50 | 0.6 s | 91.9% (725 of 789) | 14.5% (58 of 401) | 1.5% (6 of 388) |
| turn-1-mini | 0.35 | 0.2 s | 71.6% (565 of 789) | 10.0% (40 of 401) | 47.4% (184 of 388) |
| turn-1-mini | 0.35 | 0.6 s | 73.8% (582 of 789) | 13.7% (55 of 401) | 39.2% (152 of 388) |
| turn-1-mini | 0.44 (picked for 0.6 s) | 0.6 s | 68.9% (544 of 789) | 10.2% (41 of 401) | 52.6% (204 of 388) |

## The public set by kind

| Model | Threshold | Real people: accuracy | TTS: accuracy | Unfinished, ending on a filler: cut off | As published, not framed: accuracy / cut off / kept waiting |
|---|---|---|---|---|---|
| Smart Turn v3.2 | 0.50 | 92.9% (500 of 538) | 90.8% (228 of 251) | 3.8% (2 of 52) | 95.1% (750 of 789) / 7.0% (28 of 401) / 2.8% (11 of 388) |
| turn-1-mini | 0.35 | 71.9% (387 of 538) | 70.9% (178 of 251) | 1.9% (1 of 52) | 73.5% (580 of 789) / 9.7% (39 of 401) / 43.8% (170 of 388) |
| turn-1-mini | 0.50 | 62.1% (334 of 538) | 63.3% (159 of 251) | 0.0% (0 of 52) | 63.8% (503 of 789) / 4.2% (17 of 401) / 69.3% (269 of 388) |

## Notes on the data

- **Silence after the last speech in the public clips as published**, by the mod's VAD: median 0.76 s in finished clips and 0.42 s in unfinished ones. 509 of 786 clips have at least 0.2 s of their own; the rest are given the quiet floor after their audio runs out. 3 clips in which the VAD found no speech are given to the models as published.
- **Kokoro cuts:** of 1298 ask-and-voice pairs, 542 were cut at the gap nearest the middle and 663 at another gap that both halves confirmed; 93 had no clean gap and are in the set whole only. 1045 filler takes were cut cleanly just after the filler; 160 were not and are left out.
- **Kitten cuts:** of 944 ask-and-voice pairs, 528 were cut at the gap nearest the middle and 388 at another gap that both halves confirmed; 28 had no clean gap and are in the set whole only. 704 filler takes were cut cleanly just after the filler; 212 were not and are left out.
- **Cut halves by length:** Smart Turn at 0.5 cuts off 64.8% (81 of 125) of Kokoro's cut halves of one or two words, 47.0% (238 of 506) of those of three or four, and 28.2% (162 of 574) of five or more.
- **Where some asks were cut**, in Kokoro's first voice: "I'd like | to counter that command."; "Yes. Air that cloud, | he said yes."; "I just | say yes to?"; "give me one shot | protection.".
- Every clip's scores are in `build/turn-compare/scores.tsv`, and every cut tried, with what Parakeet heard in each half, in `build/turn-compare/cuts.tsv`.
