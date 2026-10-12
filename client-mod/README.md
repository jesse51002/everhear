# VFG Voice — the client mod

A client-only Fabric mod for Minecraft Java 26.3. While you are connected to the one
server it is set up for, it turns what you say into text **on your own PC** and posts
each line — with its time and its volume — to that server's bridge (`POST /v1/lines`,
docs/CONTRACTS.md §2) as soon as you have finished saying it. Beside that, a keyword
spotter listens for your incantations and posts each one the moment you say it (below). No
audio ever leaves your PC, and it never uses Minecraft's own networking, so it works on a
vanilla server.

Speech-to-text is [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) 1.13.8 with the
Silero VAD and NVIDIA's Parakeet TDT 110M (English, int8; CC-BY-4.0). Moonshine base and
tiny can be chosen instead. A line ends when a small turn model,
[Smart Turn v3.2](https://huggingface.co/pipecat-ai/smart-turn-v3) (Pipecat, BSD-2-Clause),
hears at a pause that you have finished, or after 3 s of silence (below). The jar is
small; the engine and models (about 150 MB) download once on first start, pinned by SHA-256.

## For players: install

Windows, and the same elsewhere:

1. Install **Fabric Loader 0.19.5** for **Minecraft 26.3** with the Fabric installer
   (<https://fabricmc.net/use/installer/>). It adds a launcher profile.
2. Put **Fabric API `0.162.0+26.3`** and **`vfg-client-<version>.jar`** (the server's owner
   sends it) in your `.minecraft/mods/` folder.
3. Start that profile. The first start downloads about 150 MB in the background;
   Voice settings shows the progress.
4. **Multiplayer → `Voice…`** (top right): the server address exactly as it is in your
   server list, the client token the owner gave you, your microphone. Press `Test mic`,
   say something, and check the `Heard:` text. Then `Done`.
5. Join the server. A mic icon appears top-left and the chat says it is listening.

Keys (change them in Options → Controls → Key Binds → Voice):

- **M** mutes and unmutes. Muting closes the microphone.
- **K** opens Voice settings in the game.

The icon, top-left by default:

| Icon | Means |
|---|---|
| amber mic and `NN%` | setting up (downloading the engine and model) |
| red mic with a slash | an error: the mic, the engine, a rejected token, or an unsupported PC — open Voice settings (K) to see which |
| grey mic with a slash | muted |
| grey mic and `?` | no client token set |
| white mic with an amber dot | lines are not reaching the server right now; they are retried for 60 s |
| green mic with a level bar | hearing you |
| dim green mic | listening |

If the first start cannot download (a firewall), put the files from the table in
**Downloads** below into `.minecraft/vfg/downloads/` by hand and press `Retry` in
Voice settings.

If Voice settings says `engine failed` on Windows, install the **Microsoft Visual C++
Redistributable (x64)** and press `Retry`.

## For the owner: build

Needs JDK 25. On Fedora: `java-25-openjdk-devel`; on Fedora Atomic, where a system
install means a reboot, any JDK 25 unpacked in your home folder works with
`JAVA_HOME` pointing at it. With no JDK at all, Gradle downloads one (the toolchain
in `build.gradle`, through the foojay resolver in `settings.gradle`).

```sh
cd client-mod            # from the repo's root
./gradlew build         # build/libs/vfg-client-<version>.jar, under 1 MB; also runs the tests
./gradlew test          # unit tests: no natives, no internet (the fake-bridge test needs python3)
./gradlew engineTest    # the real VAD and all three models; installs ~260 MB into build/vfg-test-assets/ once
./gradlew speechTest    # real speech through the mod's pipeline in real time, about 4 min (below)
./gradlew kwsTest       # the keyword spotter on Kokoro speech: hits, false casts, latency, CPU (below)
./gradlew micCheck      # your own mic through the mod's pipeline, no Minecraft (below)
./gradlew castCheck     # a WAV as if from the mic, posting casts and lines to a real bridge (below)
./gradlew sttCompare    # the models side by side on the same audio: errors, speed, size, memory (below)
./gradlew turnCompare   # the two turn models on real and game speech: cut-offs, waits, speed (below; needs uv)
```

The version is `mod_version` in `gradle.properties`. **Raise it for every build you hand
out.** Updating is replacing the jar; the downloaded engine and model stay.

`./gradlew build` fetches `libs/sherpa-onnx-jvm-1.13.8.jar` from the sherpa-onnx
release once, checks its SHA-256, and copies its classes into the mod jar.

### Check your mic: `./gradlew micCheck`

Runs the mod's own speech pipeline — the same `VoicePipeline` class the mod runs: OpenAL
mic, Silero VAD, Parakeet, line assembler — on a real microphone, without Minecraft.
It lists the mics, prints a level meter once a second with what the VAD thinks
(`listening`, `SPEECH`, `silence, line open: closes in 2.3 s`), every segment it
transcribes with its speech-to-text time, and every line exactly as the mod would send
it: text, `started_at` → `ended_at`, peak and mean dBFS, the number of segments, how long
after the end of speech it was ready, and the §2 JSON body. Press Enter (or Ctrl-C) to
stop; an open line is sent at once, as when a player leaves the server.

```sh
./gradlew micCheck --args="--list"                 # just list the mics
./gradlew micCheck                                 # the system default mic, as the mod uses
./gradlew micCheck --args="--device 9"             # a mic by its number in the list (or by name)
VFG_CLIENT_TOKEN=test ./gradlew micCheck --args="--device 9 --bridge http://127.0.0.1:8787"
                                                   # also POST each line and show the answer
./gradlew micCheck --args="--wav build/speech-test/commands-16k.wav"
                                                   # play a recording in instead of a mic
```

It prints each decision on a line's end as the mod logs it (`turn 0.93 in 31 ms: closed`,
`turn 0.18 in 29 ms: waiting`, `closed after 3.5 s of silence`). `--turn turn-1-mini` or
`--turn off` hears it with the other model, or none.

Other options: `--seconds N`, `--token T`, `--player NAME` (the posted `player_name`,
default `MicCheck`), `--turn-threshold X`, `--silence S`, `--vad-threshold X`,
`--threads N`; `--args="--help"` lists them.

It uses the mod's default model, Parakeet TDT 110M. To hear the others on your own voice:
`--model base` or `--model tiny` (Moonshine, which the mod can also use), or `--model
parakeet` (Parakeet 0.6B, 482 MB once, about 0.9 GB of memory; not in the mod). With a
Parakeet model, `--hotwords SteveJM,Alex,creeper` favours those words (experimental; the mod
uses no hotwords):

```sh
./gradlew micCheck --args="--device 9 --model base"
./gradlew micCheck --args="--device 9 --model parakeet --hotwords SteveJM,Alex"
```

The first run downloads the engine and model into `build/vfg-test-assets/` (shared with
`engineTest`). Nothing here goes into the mod's jar.

The system default is not always the mic you talk into: a laptop's built-in array can read
0.0 dBFS with no speech at all. Check the levels, pick the mic you will play with, and pick the
same one in Voice settings in the game.

### Real-speech test: `./gradlew speechTest`

Plays speech into the mod's pipeline **in real time**, through `MicCapture` itself with a
stand-in sound card that delivers samples as the wall clock runs — at 16 kHz, and at 48 kHz
so the mod's resampling path is used — and checks how many lines come out, where they split,
which words they hold, and how long after the end of speech each was ready.

- **With the mod's defaults** (Smart Turn at its own threshold, `silence_seconds` 3): 12
  short Minecraft requests ("Build me a castle by the cave", "Summon an ender dragon", …)
  spoken by Kokoro TTS in eleven voices must each become their own line, **within 1 s of the
  last word, median** (0.49 s on 2026-10-10: the VAD's 0.35 s, then speech-to-text and the
  turn model's score), and every line, whatever the model heard, by `silence_seconds` +
  0.5 s. Kokoro, because Smart Turn hears most of Kitten's finished sentences as unfinished
  (`reports/turn-compare.md`).
- **Pauses mid-sentence:** 8 requests spoken whole, then split at a gap between words with
  1.5 to 2 s of silence put in. How many become two lines is reported, not checked (3 of 8 on
  2026-10-09); only that every word arrives, once and in order.
- **With `turn_model: off` and `silence_seconds: 5`**, the rule before the turn model: real
  recordings from sherpa-onnx (the Kennedy clip, "how do I get to Dublin?", 18 s of unbroken
  lecture) and Kitten TTS phrases in six voices at three speeds. Pauses of 1.5, 2, 3 and
  4.5 s must not split a line, 5.5 and 6.5 s must, and each line is ready 5 to 7 s after its
  speech (5 s of silence, plus 0.5 s grace, plus at most a 250 ms tick; speech-to-text runs
  during the silence, so it adds nothing).

The TTS models (Kitten ~27 MB, Kokoro ~103 MB) are pinned by SHA-256, and the Kokoro takes are
kept in `build/vfg-test-assets/speech-test-tts/` so every run hears the same audio. It takes
about four minutes. What it played is left in `build/speech-test/*.wav`, and what it heard,
with each turn decision, in `build/speech-test/report.txt`.

### Compare speech-to-text models: `./gradlew sttCompare`

A measurement; it changes nothing in the mod. It is how Parakeet TDT 110M was chosen as
the default (on 2026-10-08: 11.5% word errors against Moonshine base's 19.2%, at about the
same size, speed and memory). It builds about 17 minutes of speech — five
real recordings, the six Minecraft phrases and eight hard ones ("that was a five second
pause", "put a torch on the pillar", "tp me to Alex", "spawn a creeper behind SteveJM",
netherite, redstone, ender dragon…) in all eight Kitten voices, two takes each — sends it
through the mod's 48 kHz-to-16 kHz path, cuts it with the mod's VAD (7 s cap), and gives
the same segments to Moonshine base, Moonshine tiny, Parakeet TDT 110M and Parakeet TDT
0.6B v2 (and 0.6B again with hotwords), each in a process of its own. It reports word
error rate (recordings, Minecraft phrases, hard words), speech-to-text time per segment on
2 threads, download size, load time and resident memory, in `build/stt-compare/report.md`
with every mistake listed. The first run downloads about 620 MB of models into
`build/vfg-test-assets/`; it takes about 3 minutes after that. The TTS takes are kept, so a
second run hears the same audio.

Hotwords (contextual biasing) work in sherpa-onnx 1.13.8 for the Parakeet (transducer)
models only, with `modified_beam_search`, `modelingUnit=bpe` and a `bpe.vocab`. The model
archives do not ship one, so it is made from `tokens.txt` (pieces in order, scored by
minus their rank). Moonshine has no hotword support.

### Compare turn models: `./gradlew turnCompare`

A measurement; it changes nothing in the mod. It is how turn-1-mini's threshold was picked
(on 2026-10-09: 0.35, the one at which it cuts off unfinished clips as often as Smart Turn
v3.2 does at 0.5, 10%; there it keeps 47% of finished clips waiting, against Smart Turn's
5%), and it is what to read before switching from Smart Turn.
Each clip is given to both models as the mod gives them a line at a pause: from 0.5 s before
the first speech the mod's own VAD finds to 0.2 s after the last.

- **Public set:** the 789 English clips of one shard of Pipecat's
  [Smart Turn v3.2 test set](https://huggingface.co/datasets/pipecat-ai/smart-turn-data-v3.2-test)
  (CC-BY-4.0; real people and TTS, each labelled finished or not). The task first runs
  `turnData`, which runs `tools/turn_data.py` with `uv run --with pyarrow --with soundfile`:
  it downloads the shard once (486 MB, pinned by revision and SHA-256), writes its English
  clips as 16 kHz WAVs and a labels file into `build/vfg-test-assets/turn-data/public/`, and
  deletes the shard. It needs [uv](https://docs.astral.sh/uv/); a second run does nothing.
- **Game set:** the 118 direct asks of 4 to 12 words players said in the 2026-10-08
  playtest, from `build/vfg-test-assets/turn/game-asks.txt`. That file is players' speech
  and is not in the repo; without it the game set is skipped. Each ask is spoken by Kokoro TTS
  (int8, 11 voices, 103 MB, pinned) whole (finished), cut at a gap between words near the
  middle (not finished), and said again with "uh" or "um" in that gap and cut just after it
  (not finished). Gaps come from Parakeet's word timestamps, and a cut is kept only when both
  halves, transcribed alone, meet where the whole's words do. The same asks spoken by Kitten
  are scored too: Smart Turn hears most of Kitten's finished sentences as unfinished, so the
  game set is not Kitten's.

It reports, per model and set, accuracy, the share of unfinished clips scored finished (cut
off), the share of finished ones kept waiting, and the time per score; the game set by form;
Smart Turn at thresholds around 0.5; and both models asked later, 0.6 s into the pause. It
writes `build/turn-compare/report.md`, with every clip's scores in `scores.tsv` and every
cut tried, with what Parakeet heard in each half, in `cuts.tsv`. The latest report is kept
in [`reports/turn-compare.md`](reports/turn-compare.md). The first run downloads about
600 MB and says about 4,400 TTS takes, about an hour in all; the takes are kept, so a second
run hears the same audio, gives the same scores, and takes about 20 minutes.

### Dev run

```sh
VFG_CLIENT_TOKEN=test python3 tools/fake_bridge.py --port 8787     # one terminal
VFG_CLIENT_TOKEN=test VFG_BRIDGE_URL=http://127.0.0.1:8787 ./gradlew runClient
```

The dev client is offline, so join a local vanilla 26.3 server with
`online-mode=false`, and set the server address to `localhost` in Voice settings (or
also set `VFG_SERVER_ADDRESS=localhost`). The environment variables override the file
and are never written back.

The fake bridge serves `POST /v1/lines` as §2 does — `401` for a wrong token, checked
first; `400` with the reason for a body that breaks §2; `202 {"id": "<ULID>"}`, the same
id for a repeated `client_line_id` — and prints one line per accepted request.
`--fail N` answers `500` to the first N requests, `--status CODE` forces a code,
`--delay S` waits before answering. `--incantation "fire ball=fireball,fire bowl"` (repeatable)
binds a phrase and its aliases for every player: it then lists them at `GET /v1/incantations`
and answers a cast of one at `POST /v1/casts` with `fired`, and of anything else with `404`.

## How a line ends

The Silero VAD ends a segment of speech at 0.35 s of silence. At that pause, once the segment
is transcribed, the turn model scores the line's audio — from 0.5 s before its first speech
to 0.2 s after its last, the last 8 s of that at most — for whether you have finished. At or
above its threshold the line goes at once, about 0.5 s after your last word; below it the
line stays open, and speech that follows joins it and is scored again at its own pause. A
line the model keeps waiting goes at `silence_seconds` (3) + 0.5 s after the last speech.
Speech starting again before a line goes keeps it open. A thought cut off is not repaired:
the rest arrives as the next line. The caps are as before: 60 s of speech or 1500 characters.

The model runs on `vfg-stt`, one CPU thread, about 35 ms a score, on the ONNX Runtime
library sherpa-onnx has already loaded: `turn/OnnxRuntime.java` calls its C API through
`java.lang.foreign`. (ONNX Runtime's own Java API would load a second copy of the runtime.)
It installs and loads after the speech engine is ready, so it never holds up voice; until
it is in, or if it cannot download (tried again after 30 s, 2 min, then every 10 min) or
load, lines end on silence alone, and Voice settings says which under the status. The model
is asked at every pause, even after a cough or laugh that makes no line of its own; after
speech the VAD cut at its 7 s limit, it is asked once 0.35 s of silence follow.
`turn_model: turn-1-mini` swaps in [turn-1-mini](https://huggingface.co/p99lab/turn-1-mini)
(p99lab, Apache-2.0); `./gradlew turnCompare` is how the two were measured.

## Incantations: the keyword spotter

A player's incantations (docs/CONTRACTS.md: the bridge's `GET /v1/incantations` and
`POST /v1/casts`) are heard by a keyword spotter, a pipeline of its own beside the lines: it never
waits on a line, and a line never waits on it. It is sherpa-onnx's streaming `KeywordSpotter` over
icefall's English GigaSpeech keyword model (a 3.3M-parameter zipformer, int8, 17.6 MB once), on
its own thread, `vfg-kws`, one CPU thread.

- **The list.** While the mod listens, it asks the bridge for the player's incantations, at once
  on joining and then by long poll (`after` the version it has, `wait=25`; `304` is no change).
  Every phrase and every alias is a keyword; a hit on an alias casts the phrase. A phrase may be one
  word: it then casts in any sentence it is said in, and at the start of longer words ("heal" in
  "healing").
- **The tokens are made here.** The box sends plain phrases; the mod splits them into the model's
  tokens with the model's own `bpe.model` (`kws/SentencePiece.java`, checked against Python's
  sentencepiece on 45 phrases by `tools/kws_bpe_fixture.py`). That file is a *unigram* model, not
  BPE, despite its name. A keyword with a token the model lacks (a digit) is left out, with a log line.
- **Only while someone speaks.** The capture thread hands every 32 ms window to `vfg-kws` by a
  non-blocking offer (a full 2 s queue drops it). The spotter has a VAD of its own, a second Silero
  on the same file and threshold, which ends speech after 0.15 s of silence where the lines' VAD waits
  0.35 s (a median 0.22 s after a sentence's sound, against 0.41 s), for about 70 µs a window, 0.2% of
  a core. Nothing is decoded in silence.
- **The re-check casts a phrase as it ends.** While the player speaks, and until 0.15 s of silence
  after, `vfg-kws` handles every window waiting and then checks the newest audio again, back to back:
  a fresh, throwaway stream is fed the player's last 3 s (`incantation_recheck_seconds`) and then
  0.5 s of zeros, which stand for "the player stops right now", so the model decodes the end of what
  was said without waiting for audio not yet spoken; its first hit casts. On Jesse's laptop one takes
  a median 12 ms on one thread, 40 ms at the 90th percentile (a whole 3 s of speech about 45 ms), so
  one runs after nearly every 32 ms window and keeps about half a core busy while the player talks;
  a slower PC re-checks less often rather than falling behind. It keeps at most 0.32 s of the silence before speech, and none of a silence a second long:
  a stream resets itself after 1.5 s of blanks and drops the start of a keyword said just then
  (sherpa-onnx issue #3990). Kept whole, the silence made casts 0.1 s later and re-checks nearly
  twice as dear, for the same hits.
- **The live stream stays, as a backup.** It never skips audio when the PC is too busy to re-check.
  It opens when speech starts, fed the last 0.64 s first; when speech ends it is pushed 0.5 s of
  zeros, so its last 320 ms chunk decodes at once, and closed. No stream sits in the 1.5 s of silence
  of issue #3990 (9 of 42 missed one way, none this way).
- **A phrase that starts a longer one waits for it.** A keyword whose tokens start another's ("fire"
  beside "fire ball", or beside the alias "fireball") would end the longer one: a re-check's zeros, or
  the gap between the words, finish it first. So the re-checks leave it out while speech goes on, and
  one more with every keyword runs when speech ends; a hit on it from either way is held. A cast whose
  sound starts before the held one's end drops it; if none comes within 0.5 s (Microsoft's speech
  engine waits as long after a phrase that can start another), or speech ends first, it casts then.
  With `incantation_recheck_seconds: 0` the live stream alone hears "fire" first, and "fire ball" is
  lost.
- **One saying casts once.** Neither way hears audio from before the end of the last cast: a re-check
  never reaches back past it, and a re-check's cast closes the live stream, which reopens from after
  it. Whichever way hears a phrase first casts it; the bridge's duplicate check stays behind both.
- **A cast** is posted on a thread of its own with a fresh `client_cast_id`, the phrase, the keyword
  heard, and when it was said on the same audio clock a line's `started_at` uses. A failure is tried
  again for at most 2 s after the hit, then dropped: a late spell is worse than none. `404` (no such
  incantation) and `503` (no kit on the box) are final. One log line per cast, never with audio, with
  which way heard it:
  `cast "fire ball" (heard "fireball" by the re-check): fired as command_2, answered 0.30 s after the
  phrase, 0.24 s after the hit`. "After the phrase" counts from the cast's `ended_at`, as the log did
  before the re-check, so read the in-game bar (Prototype_Fast_Casts: a median at most 0.35 s, against
  0.65 s on 2026-10-11) from it: it compares with the old logs, and for a re-check's cast it is an
  overestimate, since a re-check usually hears the phrase before its last sound is over and then
  ends it at the newest sample (a median 0.18 s early on Kokoro). "After the hit" is the trip to the
  box and back alone.
- **Measured** (`./gradlew kwsTest`, Kokoro TTS, 2026-10-11; the live stream alone in brackets): seven
  two-word phrases ("fire ball", "ice wall", "lightning strike", "summon wolves", "rocket jump",
  "healing light", "meteor storm") said alone and ending a sentence heard 229 of 231 (217), a median
  0.12 s *before* the end of the phrase's sound and 0.06 s after it at the 90th percentile, compute
  included (0.22 and 0.24 s after; 0.46 s before the stream had its push); over every take a phrase
  ends, −0.14 and +0.05 s. "Fire ball" inside a sentence 12 of 12 (12), after a pause 42 of 42 (42),
  after 0–12 s of nonstop talk 16 of 16 (14); "kamehameha" 0 of 11 as Kokoro reads the spelling, 9 of
  11 said syllable by syllable, through its alias "kami hami ha" (8); no false casts in 16 ordinary
  requests or 6 near misses ("Throw the ball into the fire."); no saying cast twice. A phrase starting
  a longer word casts: "fire balloon", "ice walls", "fire ballad", 33 of 33 (31). One word: "fireball"
  22 of 22 alone and ending a sentence (18), "heal" 17 (8), "shield" 21 (22); "a shield for the fight"
  and "healing potions" cast 11 of 11. With "fire" and "fire ball" both bound, "fire ball" alone and
  in sentences cast "fire ball" 23 of 23 and "fire" never (5 of 23, "fire" 17 times), and "Fire!"
  cast "fire" 8 of 11, 0.23 s after it (3). About half a core while someone speaks, 51% (3%),
  nothing in silence; a re-check takes a median 12 ms here, 37 ms at the 90th percentile. Casting
  before the last sound is over, a cast's `ended_at` is the newest sample, a median 0.18 s before the
  sound ends; its `started_at` is within 0.05 s.

### Check the spotter: `./gradlew kwsTest`

The keyword model installed through the mod's installer, Kokoro takes (kept with the speech
test's), the spotter's VAD and its `SpotterWorker`, fed 32 ms at a time faster than real time, with
`vfg-kws` played on the same clock: a turn of it (every window waiting, then a re-check) starts when
the thread is free and lasts as long as it took, so a window that comes during a re-check waits, and a
cast counts as heard when its turn got that far, its compute included. Every case runs two ways, the
mod's (re-checks over 3 s and the live stream) and the live stream alone with its push
(`incantation_recheck_seconds: 0`): phrases alone and ending a sentence, inside sentences, the
after-a-pause case, ordinary sentences and near misses, phrases that start longer words ("fire
balloon"), a spell after 0–12 s of nonstop talk, one-word phrases, the spotter's VAD, latency and CPU,
in about 5 min once the takes are made. It checks what is certain (Prototype_Fast_Casts: a
median at most 0.1 s and a 90th percentile at most 0.2 s after the phrase ends; "fire ball" heard as
often as before, alone and inside sentences; no false casts; no saying cast twice) and reports the
rest, in `build/kws-test/report.txt`. `-Pkws-sweep` adds a table over thresholds, boosts and trailing
blanks on the stream alone (about 3 min); `-Pkws-lead` how much silence a re-check keeps, and 3 s
against 1.2 s.

### End to end: `./gradlew castCheck`

Plays a WAV, or Kokoro saying `--say` texts, through the mod's own pipeline and spotter as if from
the mic, posts casts and lines to a real bridge, and prints for each when it was said (on the audio
clock, as UTC and as seconds into the WAV), heard, posted and answered. The WAV plays through the
speech test's stand-in sound card, which delivers samples as the wall clock runs, and the capture
thread stamps each read as it does a mic's, so the audio clock maps to the wall clock as a mic's
does: sample `i` is at about the moment the device opened plus `i / rate`.

```sh
VFG_CLIENT_TOKEN=test python3 tools/fake_bridge.py --port 8787 --incantation "fire ball=fireball"
./gradlew castCheck --args="--bridge http://127.0.0.1:8787 --token test --player Steve --say 'Fire ball!' --say 'Okay, fire ball on the zombie now.'"
./gradlew castCheck --args="--bridge http://127.0.0.1:8787 --token test --player Steve --wav build/cast-check/said.wav"
```

It listens for the player's incantations as the bridge lists them; `--incantation "p=a,b"` adds
some for a bridge that lists none. `--threshold`, `--boost` and `--recheck` (seconds, 0 for the stream
alone) are the config's; `--help` lists all. Each cast says which way heard it, and its answer how long
after the phrase's end it came, as the mod's log does.

## Configuration

`<game folder>/config/vfg-client.json`, written by Voice settings:

```json
{ "server_address": "", "bridge_url": "", "client_token": "", "mic_device": "",
  "model": "parakeet-110m-en", "icon_corner": "top_left", "muted": false,
  "turn_model": "smart-turn-v3.2", "turn_threshold": null, "silence_seconds": 3,
  "vad_threshold": 0.5, "stt_threads": 2, "log_text": false,
  "incantations": true, "incantation_threshold": 0.25, "incantation_boost": 1.5,
  "incantation_recheck_seconds": 3 }
```

- `client_token` is `VFG_CLIENT_TOKEN`. It lives only in this file, is shown masked, and
  is never logged.
- An empty `bridge_url` means `http://<host of server_address>:8787`.
- `model` is `parakeet-110m-en` (the default; `parakeet-110m` also works), `moonshine-base-en`
  or `moonshine-tiny-en` (the lightest). A file that already says `moonshine-…` keeps it — a
  config written by an earlier version says `moonshine-base-en`, so that player stays on
  Moonshine until they pick Parakeet in Voice settings (Model) or edit the file. Changing it
  downloads the new model (if it is not there yet) and reloads the engine after Done.
- File only: `turn_model` (`smart-turn-v3.2`, the default; `turn-1-mini`; or `off`, which
  ends lines on silence alone), `turn_threshold` (0.05–0.95; `null` is the model's own, 0.5
  for Smart Turn and 0.35 for turn-1-mini), `silence_seconds` (2–30, default 3: the most a
  line waits after its last speech when the turn model keeps it open), `vad_threshold`
  (0.1–0.9), `stt_threads` (1–8), `log_text` (logs what was heard and sent). Out-of-range
  values fall back to the default with a log line. `turn_model: off` with
  `silence_seconds: 5` is the mod before the turn model.
- File only, the keyword spotter: `incantations` (on by default; off, the mod never downloads the
  keyword model and incantations are heard in lines only, by the bridge), `incantation_threshold`
  (0.05–0.95, default 0.25: the mean probability of a keyword's tokens at which it fires) and
  `incantation_boost` (0.5–5, default 1.5: the score added per token of a keyword). Lower threshold
  and higher boost hear more and cast more by mistake; on Kokoro speech 0.1 to 0.25 heard the same,
  and a boost of 3 heard less. `incantation_recheck_seconds` (1–10, or 0; default 3) is how far back
  the re-check reaches: further costs more CPU per re-check (about 14 ms a second of audio), and 0
  turns re-checks off, leaving the live stream with its push at the end of speech: a cast about 0.23 s
  after the phrase instead of as it ends, at 3% of a core while someone speaks instead of about half;
  the fallback if the re-checks cost frame rate.
- A file with no `turn_model` key was written before the turn model: when it is read, a
  `silence_seconds` of 5 becomes 3 (any other value stays) and the file is written back once.
- A file that does not parse is renamed `vfg-client.json.bad` and the defaults are used.

The engine and models live in `<game folder>/vfg/` (about 190 MB unpacked for Parakeet, Smart
Turn and the keyword model; each other model chosen adds its own folder). The log is
the game's `logs/latest.log`, logger `vfg`.

## Downloads

Each checked by SHA-256 as it streams. Only this PC's natives, the VAD, the chosen speech
model, the chosen turn model and the keyword model (unless `incantations` is off) are fetched. From
`https://github.com/k2-fsa/sherpa-onnx/releases/download/`:

| File | Bytes | SHA-256 |
|---|---|---|
| `v1.13.8/sherpa-onnx-native-lib-win-x64-1.13.8.jar` | 8277046 | `33fbdbd5410e9ba9bdda94aa164ec8f7825bb49246420d8ce9bdd88219d97039` |
| `v1.13.8/sherpa-onnx-native-lib-linux-x64-1.13.8.jar` | 10515871 | `30c93b59381113f9c20aedbbf9fc1ad399158f6bc03dddc0f8934a6e28e069ba` |
| `v1.13.8/sherpa-onnx-native-lib-osx-aarch64-1.13.8.jar` | 9460832 | `42e272180c8836127f024f3335d7afcdfb30fe0164b78330d5d449034e34ce34` |
| `v1.13.8/sherpa-onnx-native-lib-osx-x64-1.13.8.jar` | 10782466 | `9190c28951d85efdbd376bae6b6dff12993311ad605945289e8459bf9c886a96` |
| `asr-models/silero_vad.onnx` | 643854 | `9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6` |
| `asr-models/sherpa-onnx-nemo-parakeet_tdt_transducer_110m-en-36000-int8.tar.bz2` (default) | 108035095 | `f628312e9fdf8686374cb01a69425c41732529d540860311f16f37cbc32cfe9b` |
| `asr-models/sherpa-onnx-moonshine-base-en-quantized-2026-02-27.tar.bz2` | 111266225 | `43232c1d13013d37317163baec3135bd771a186a4356f28c889bab453bb0e891` |
| `asr-models/sherpa-onnx-moonshine-tiny-en-quantized-2026-02-27.tar.bz2` | 29858559 | `9ec31b342d8fa3240c3b81b8f82e1cf7e3ac467c93ca5a999b741d5887164f8d` |

The keyword spotter's model, from the same release, into `<game folder>/vfg/models/kws/`; only its
`encoder`, `decoder` and `joiner` (`*-epoch-12-avg-2-chunk-16-left-64.int8.onnx`), `tokens.txt` and
`bpe.model` are kept (icefall, Apache-2.0):

| File | Bytes | SHA-256 |
|---|---|---|
| `kws-models/sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01.tar.bz2` | 17626723 | `f170013b4716e41b62b9bfd809687c207cef798ef9bc6534d524e17af9b6561a` |

The turn models, from Hugging Face at pinned revisions, into
`<game folder>/vfg/models/turn/<turn_model>/`:

| File | Bytes | SHA-256 |
|---|---|---|
| `soniqo/Smart-Turn-v3.2-ONNX/resolve/b48fdbe20772bcec1fef02f4a1a355236ef6359e/smart-turn-v3.2-int8.onnx` (default) | 11123370 | `00cd131551e8d1e9011f31345edb632a26116dd83e159782c4ddff610718ea31` |
| `p99lab/turn-1-mini/resolve/076c86d1744a231d3a4cce31f3bbbd1211b4c281/onnx/turn-1-mini.step.int8dyn.onnx` | 7000932 | `a59a5b627190e7bf52a46dd25026ec8d4d0b01e1b0146643bb39161808c0619e` |

## Notes

- Singleplayer and LAN worlds never listen.
- Java 25 logs a warning that `System::load` is a restricted method, and another the first
  time the turn model calls into ONNX Runtime through `java.lang.foreign`; both are
  harmless. If a later Java blocks them, add `--enable-native-access=ALL-UNNAMED` to the
  profile's JVM arguments.
- Under the Flatpak launcher on Fedora the game folder is inside
  `~/.var/app/com.mojang.Minecraft/`, and the mic comes through its PulseAudio socket.
- `started_at` uses the PC's clock; a clock that is far off misorders that player's lines
  on the server.
- macOS is not tested; its natives download the same way and may simply work.
- The model is NVIDIA's Parakeet TDT 110M (from `nvidia/parakeet-tdt_ctc-110m`), CC-BY-4.0,
  converted by sherpa-onnx; Moonshine is MIT, Silero VAD MIT, sherpa-onnx Apache-2.0. Smart
  Turn is BSD-2-Clause (Daily) and turn-1-mini Apache-2.0 (p99lab); the jar carries their
  `LICENSE-*.txt` and `NOTICE-turn-1-mini.txt`.
- Moonshine returns no text for a clip of about 9.3 s or more, so the VAD ends a segment
  after 7 s of unbroken speech (at the quietest point after it; segments run to about 8 s),
  and the engine decodes anything longer than 9 s in pieces. A pause shorter than the
  silence setting still never splits a line.

## Layout

```text
build.gradle  settings.gradle  gradle.properties  gradlew  gradlew.bat  gradle/wrapper/
libs/                               the fetched sherpa-onnx-jvm jar (gitignored)
tools/fake_bridge.py                stand-in bridge, Python 3 stdlib
tools/turn_data.py                  turnCompare's public set, from Pipecat's test shard (run through uv)
tools/kws_bpe_fixture.py            the keyword tokens' fixture, from Python's sentencepiece (run through uv)
reports/turn-compare.md             turnCompare's report, as last run
src/main/java/com/voiceforgaming/vfg/
  VfgClient.java                    entrypoint; Fabric events, keys, HUD, the Voice… button
  VoiceService.java                 everything wired together, no Minecraft classes
  VoicePipeline.java                mic -> VAD -> speech-to-text -> lines; also run by micCheck and speechTest
  config/VfgConfig.java             load, validate and save the config; env overrides
  config/SettingsScreen.java        the settings screen
  setup/Assets.java                 the pinned downloads, platform detection, paths
  setup/AssetInstaller.java         download, verify, unpack, progress
  audio/MicCapture.java             OpenAL capture thread, device list
  audio/Resampler.java              48 kHz to 16 kHz
  speech/SpeechEngine.java          VAD and recognizer (the only sherpa-onnx code)
  speech/CaptureProcessor.java      windows to the VAD, segments timed and queued
  speech/SttWorker.java             the vfg-stt thread; asks the turn model at each pause
  speech/LineAssembler.java         segments into lines, finished or silence, caps
  speech/SampleRing.java            the last 9 s of audio, for the turn model
  speech/Levels.java                peak and RMS to dBFS
  speech/SampleClock.java           sample index to wall-clock instant
  turn/TurnDetector.java            whether a line is finished, from its audio
  turn/SmartTurn.java  turn/TurnOneMini.java   the two models
  turn/OnnxRuntime.java             ONNX Runtime's C API, on sherpa-onnx's own library
  net/LinePayload.java              the §2 body and its JSON
  net/BridgeClient.java             the sender, retries, delivery status, connection test
  net/CastPayload.java  net/CastClient.java   the incantation invoke's body, and its sender (2 s, then dropped)
  net/IncantationClient.java        the player's incantations, by long poll
  kws/SpotterWorker.java            the vfg-kws thread: windows in, re-checks and the live stream while speech, casts out
  kws/Spotter.java                  sherpa-onnx's KeywordSpotter and the spotter's VAD (its only sherpa-onnx code)
  kws/SpotterVad.java               the spotter's own VAD, which ends speech after 0.15 s of silence
  kws/Keywords.java  kws/SentencePiece.java   phrases and aliases to the model's tokens and keyword string
  session/ServerAddressMatch.java   normalising and comparing addresses
  session/SessionGate.java          whether to listen, and the chat notices
  session/StatusBoard.java          the one status snapshot the HUD and screen read
  hud/ListeningIcon.java            the corner icon
src/test/java/com/voiceforgaming/vfg/   unit tests; EngineSmokeTest is tagged "engine", SpeechTest "speech",
                                    kws/SpotterEngineTest "engine" and "kws"
  tools/MicCheck.java               ./gradlew micCheck
  tools/SttCompare.java             ./gradlew sttCompare
  tools/TurnCompare.java            ./gradlew turnCompare
  tools/CastCheck.java              ./gradlew castCheck
  testing/                          the engine for tests, WAV files, Kitten and Kokoro TTS, the stand-in
                                    sound card, the models sttCompare measures (SttModels)
```
