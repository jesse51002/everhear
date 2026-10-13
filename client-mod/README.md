# VFG Voice — the client mod

A client-only Fabric mod for Minecraft Java 26.3. While you are connected to the one
server it is set up for, it turns what you say into text **on your own PC** and posts
each line — with its time and its volume — to that server's bridge (`POST /v1/lines`,
docs/CONTRACTS.md §2) as soon as you have finished saying it. Beside that, a keyword
spotter and Vosk listen for your incantations and post each one the moment you say it (below). No
audio ever leaves your PC, and it never uses Minecraft's own networking, so it works on a
vanilla server.

Speech-to-text is [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) 1.13.8 with the
Silero VAD and NVIDIA's Parakeet TDT 0.6B v2 (English, int8; CC-BY-4.0), favouring a list of
**hotwords** for each stretch of speech: "AI" and "brain", the names in the tab list, every
incantation phrase on the server, and a few command words (below). Parakeet TDT 110M, the
default before 0.3.5, stays for a PC short of memory; Moonshine base and tiny can be chosen
too. A line ends when a small turn model,
[Smart Turn v3.2](https://huggingface.co/pipecat-ai/smart-turn-v3) (Pipecat, BSD-2-Clause),
hears at a pause that you have finished, or after 3 s of silence (below). The jar is
small; the engine, the models and Vosk's library download once, pinned by SHA-256, this PC's
natives alone: about 200 MB on the first start, with which voice works (Parakeet 110M listens),
then the 482 MB of Parakeet 0.6B in the background, swapped in once it is loaded.

## For players: install

Windows, and the same elsewhere:

1. Install **Fabric Loader 0.19.5** for **Minecraft 26.3** with the Fabric installer
   (<https://fabricmc.net/use/installer/>). It adds a launcher profile.
2. Put **Fabric API `0.162.0+26.3`** and **`vfg-client-<version>.jar`** (the server's owner
   sends it) in your `.minecraft/mods/` folder.
3. Start that profile. The first start downloads about 200 MB in the background, and voice
   works once that is in; then Parakeet 0.6B, 482 MB, downloads while Parakeet 110M listens.
   Voice settings shows the progress of both. It needs about 0.9 GB of memory beside the game;
   a PC short of it can pick Parakeet 110M in Voice settings (Model).
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
./gradlew build         # build/libs/vfg-client-<version>.jar, about 0.5 MB; also runs the tests
./gradlew test          # unit tests: no natives, no internet (the fake-bridge test needs python3)
./gradlew engineTest    # the real VAD and all four models, hotwords too; installs ~750 MB into build/vfg-test-assets/ once
./gradlew speechTest    # real speech through the mod's pipeline in real time, about 4 min (below)
./gradlew kwsTest       # the keyword spotter and Vosk on Kokoro speech: hits, false casts, latency, CPU (below)
./gradlew micCheck      # your own mic through the mod's pipeline, no Minecraft (below)
./gradlew castCheck     # a WAV as if from the mic, posting casts and lines to a real bridge (below)
./gradlew sttCompare    # the models, greedy and with hotwords, on the same audio: errors, names, false hotwords, speed, memory (below)
./gradlew turnCompare   # the two turn models on real and game speech: cut-offs, waits, speed (below; needs uv)
```

The version is `mod_version` in `gradle.properties`. **Raise it for every build you hand
out.** Updating is replacing the jar; the downloaded engine and model stay.

`./gradlew build` fetches `libs/sherpa-onnx-jvm-1.13.8.jar` from the sherpa-onnx
release once, checks its SHA-256, and copies its classes into the mod jar; and the same for
`libs/vosk-0.3.45.jar` from Maven Central (`com.alphacephei:vosk`), whose classes alone go into
the jar: its natives are left out, and the mod downloads this PC's (Downloads, below). Vosk runs on
JNA, which Minecraft itself ships (5.17.0, on every platform), so JNA is not bundled: a second copy
would load a second JNA native library into the game.

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

It uses the mod's default model, Parakeet TDT 0.6B v2 (482 MB once, about 0.9 GB of memory),
with the mod's hotwords. To hear the others on your own voice: `--model parakeet-110m` (the
fallback), `--model base` or `--model tiny` (Moonshine). With Parakeet, `--players SteveJM,Alex`
are the players online and `--phrases "fire ball,freeze"` the server's incantations, hotwords as
in the mod beside "AI", "brain" and the command words; `--no-hotwords` decodes greedily, as with
`hotwords: false`:

```sh
./gradlew micCheck --args="--device 9 --model base"
./gradlew micCheck --args="--device 9 --players SteveJM,Alex --phrases 'fire ball,freeze'"
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

A measurement; it changes nothing in the mod. It is how Parakeet TDT 110M was chosen as the default
(on 2026-10-08, against Moonshine), and how Parakeet TDT 0.6B v2 with hotwords replaced it (on
2026-10-13). Two sets, one timeline, sent through the mod's 48 kHz-to-16 kHz path and cut by the
mod's VAD (7 s cap); the same segments go to each model and way of decoding, each in a process of
its own: Moonshine base and tiny, and Parakeet 110M and 0.6B, each greedy and with the mod's hotwords.

- **The Minecraft set**: five real recordings, the six Minecraft phrases and eight hard ones ("that
  was a five second pause", "tp me to Alex", "spawn a creeper behind SteveJM", netherite, redstone,
  ender dragon…) in all eight Kitten voices, two takes each. Its players are Steve, Alex and SteveJM.
- **The addressing set**, `src/test/resources/com/voiceforgaming/vfg/tools/stt-addressing.txt`: 84
  lines with invented players (Pyrothane, DuskJR, Steve_123, Mirelle, Tobble) and incantations,
  modelled on what speech-to-text broke in the playtest of 2026-10-12: lines that name the AI ("Hey AI,
  give me food", "AI, set the time to day", "It always thinks I'm talking to AI"), "brain", a player,
  an incantation or a command word ("Turn off blizzard aura", "Yes, unbind freeze", "damage to mobs"),
  and 40 lines of everyday talk with none of them, for false hotwords. Kokoro says each in four voices
  as it speaks ("clean") and in four others 1.25 times as fast with pink noise 10 dB under the speech
  ("hard"). `-PsttSet=<file>` reads another set in the same form (`players:`, `phrases:`, then one line
  each, `said => meant` where they differ): the playtest's own lines, with its players' real names,
  are such a file, kept out of this repo.

It reports, per model and way of decoding: word errors (both sets; the addressing set clean, hard and
its no-name lines); how often "AI", "brain", each player's name, each incantation and each command
word comes through in the lines that hold them; false hotwords (said more often than meant), in the
lines with no name and in all; speech-to-text time per segment, and what beam search with hotwords
adds to the same model's greedy time segment by segment (what a line's last stretch waits longer);
load time and resident memory; then the proposed bar of the spec, every false hotword, the hotword
lists, and every utterance with errors, in `build/stt-compare/report.md`. The latest committed-set
report is kept in [`reports/stt-compare.md`](reports/stt-compare.md). The first run downloads about
730 MB of models and makes about 900 TTS takes (about half an hour); the takes are kept, so a second
run hears the same audio, in about 15 minutes. `-PsttTune=<file>` tries other decoding configurations
(a JSON list: model, hotwords, paths, each kind's boost, command words), `-PsttJobs=N` runs N
processes at once (their times are then not comparable), `-PsttMinecraft=false` and
`-PsttMoonshine=false` leave those out, `-PsttOut=<dir>` writes elsewhere.

**Measured** (2026-10-13, committed sets, 2 threads on a Ryzen AI 5 340 shared with other work, load
average about 4–7; `reports/stt-compare.md`):

| Model | WER Minecraft | WER addressing, clean / hard | "AI" | "brain" | names | incantations | command words | false per 100 no-name lines | STT per segment, median / p90 | added over greedy | memory after load / peak |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Moonshine base | 14.5% | 5.2% / 23.8% | 79% | 75% | 41% | 67% | 76% | 0.2 | 41 / 57 ms | | 221 / 323 MB |
| Moonshine tiny | 20.0% | 7.5% / 26.2% | 74% | 66% | 35% | 58% | 66% | 0 | 22 / 31 ms | | 91 / 118 MB |
| Parakeet 110M, greedy (0.3.4) | 8.8% | 4.8% / 12.9% | 91% | 81% | 41% | 78% | 70% | 0 | 31 / 42 ms | | 205 / 270 MB |
| Parakeet 110M + hotwords (the fallback) | 7.9% | 1.7% / 8.3% | 96% | 94% | 69% | 88% | 91% | 0.4 | 38 / 58 ms | +6 / +20 ms | 201 / 273 MB |
| Parakeet 0.6B v2, greedy | 5.2% | 3.5% / 8.8% | 93% | 81% | 54% | 82% | 88% | 0.2 | 110 / 150 ms | | 861 / 942 MB |
| **Parakeet 0.6B v2 + hotwords (the default)** | **4.5%** | **1.6% / 5.6%** | **98%** | **91%** | **71%** | **93%** | **95%** | **0.2** | **112 / 148 ms** | **+2 / +11 ms** | **861 / 951 MB** |

The addressing set's lines with "AI" are 160, "brain" 32, a name 80, an incantation 120, a command word
120; the lines with no name 501 (both sets). "Added over greedy" is segment by segment, median / 90th
percentile. The one false hotword the default made in a line with no name, as greedy 0.6B did too, is
"spells" for "spills" in a lecture recording.

Against the spec's proposed bar, Parakeet 0.6B with hotwords: fewer word errors than the 110M — 3.8%
against 8.9% over both sets, **pass**; "AI" through in at least 95% of the lines holding it — 98%,
**pass**; at most 1 false hotword in 100 lines with no name — 0.2 (1 in 501), **pass**; the players'
names through in at least 95% of the lines holding them — 71%, **fail**. The names that fail are the
ones Kokoro does not say as they are spelled: "Mirelle" as "Myra" or "Myral" (4 of 16 lines, none
without hotwords) and "Tobble" as "Hobble" or "Cobble" (7 of 16); Pyrothane, DuskJR and Steve_123
come through in 46 of 48. A boost high enough to bend more of them (2.5 a piece for every name) heard
"Steve" in "steak" and "set". In lines that hold a hotword already, the default heard "brain" for
"rain" or "raining" 3 times: false words that a line to the AI carries to it.

On a private set made the same way from the playtest's own lines and players (kept out of this repo:
its real names are two long, made-up ones), the default made 4.9% word errors against the 110M's 9.0%,
heard "AI" in 99% of its lines, each incantation phrase in 100% and no false hotword in its 200 lines
with no name; its players' names came through in 46% of lines (0% greedy on either model), and in
lines to the AI it heard the phrase "freeze" for "please" or "food" 3 times, in fast noisy takes —
lines short enough for the bridge's Layer 2 to cast them (CONTRACTS §2). Lowering a one-word phrase's
boost, even to nothing, did not stop it: it is beam search's, not the boost's.

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

## The speech model

Parakeet TDT 0.6B v2 (int8) is the default since 0.3.5: about 20% fewer word errors than the 110M
across the Open ASR Leaderboard's sets, 29% fewer on AMI (meeting talk, the public set closest to
players talking), and fewer on the mod's own sets (`./gradlew sttCompare`, below). It costs a 482 MB
download once, about 0.9 GB of memory while playing against the 110M's 0.2 GB, and a little more
time on a line's last stretch (measured below). How setup picks the model (`setup/ModelChoice.java`):

- **The 0.6B chosen and installed** loads. If it will not (an error, or memory short), Parakeet TDT
  110M is installed if need be (108 MB) and loads in its place for this start; the log says
  `speech engine: parakeet-0.6b-v2-en failed to load (…); falling back to parakeet-110m-en`, and
  Voice settings says so under the status. The next start tries the 0.6B again.
- **The 0.6B chosen and not there yet** — a first start, or the first start of 0.3.5 — has the 110M
  listen at once (installed first if need be: a first start fetches about 200 MB before voice works,
  as before), while the 0.6B downloads in the background on `vfg-stt-upgrade`; Voice settings shows
  `Parakeet 0.6B: downloading 43%; 110M listens meanwhile`. A download that breaks off carries on
  where it stopped (an HTTP range request), at the next try or the next start; a failed one is
  tried again after 30 s, 2 min, then every 10 min. Once in, the 0.6B's recognizer is loaded and
  swapped into the running engine between two segments, the VAD and its state kept:
  `speech engine: parakeet-0.6b-v2-en in, in place of parakeet-110m-en (hotwords, 2154 ms)`. One that
  will not load leaves the 110M listening, with a log line and a note.
- **Loads of the 0.6B that never finished** (the game was killed mid-load, as an out-of-memory
  killer does): a mark, `<game folder>/vfg/stt-loading` (`parakeet-0.6b-v2-en 1`: the model and how
  many of its loads in a row began without finishing), is written as a load begins and deleted when
  any load finishes, well or not, and on a clean quit. One unfinished load is tried again at the next
  start, with a log line, since the game may have died of something else while the 0.6B loaded in the
  background. Two in a row, and setup chooses Parakeet 110M in the config itself, with a log line and
  a note, so the game does not die the same way at every start; picking Parakeet 0.6B again in Voice
  settings tries it again.
- **A download** that broke off resumes; a resume the server answers for another range (or with a
  4xx) deletes the part, so the next try starts clean; a part already the whole size (finished, then
  killed before its check) is hashed and used if right.

## Hotwords

Speech-to-text favours a list of words for each stretch of speech (sherpa-onnx's contextual
biasing, `OfflineRecognizer.createStream(hotwords)`), so the AI's name and the server's own words
come through: the playtest of 2026-10-12 heard "AI" as "I", "Aya" and "today", "blizzard aura" as
"Lizora", "unbind" as "unpound". The list (`speech/Hotwords.java`):

- **"AI" and "brain"**, the AI's names.
- **Every player online**: the names in the tab list (read once a second on the client thread), as
  the model writes them: cut where lowercase turns to a capital and at anything but a letter, digits
  dropped (`KorbinJM` → `Korbin JM`, `Steve_123` → `Steve`), since a hotword boosts whole words and
  players seldom say the digits.
- **Every incantation phrase on the server**, whoever's: the bridge's `server_phrases`, beside the
  player's own list in `GET /v1/incantations` (a bridge that does not send them: the player's own
  phrases). Not the aliases: they are the keyword spotter's other spellings of a phrase, and a
  hotword boosts the real word.
- **Command words**: `unbind`, `incantation`, `incantations`, `aura`, `cooldown`, `mobs`, `spell`,
  `spells`: each came through more often boosted on the addressing set, and none made a false word
  that greedy search did not (`mobs`, the playtest's "Moms", 4 of 8 lines unboosted on the 0.6B).

Each kind has a boost of its own (`Hotwords.Settings`), the score sherpa-onnx adds for each of a
word's pieces as the decoder takes them, all taken back if the word is not finished. Measured from
0.5 to 4.0 a piece on the committed sets (`./gradlew sttCompare -PsttTune=…`), **1.0 for every kind**
is where it stops paying, but for one: **on the 0.6B, a name of 4 pieces or more gets 2.5**
(`Hotwords.PARAKEET_06B`; "Pyrothane" is 6 pieces, "Korbin JM" 6, "Steve" 3). A long name is seldom
matched by chance, and a made-up name needs the push: on the committed set names came through in
71% of lines against 65%, on the playtest's own players 46% against 31%, with no false name either
way; the 110M heard false names with it (6 in 501 lines) and keeps 1.0. Above 1.0 elsewhere nothing
more came through on the 0.6B, while false words began —
"brain" for "rain" at 1.5, false names on the 110M at 1.5 ("Steve" for "steak", "Alex" before a
line), false "AI" at 2.5, and at 4.0 the Minecraft set's word errors rose to 14% on both. "Brain"
with a capital is not listed: its first piece, `▁B`, turned "Spawn" into "Bawn" in 11 lines of the
Minecraft set. A Parakeet model with hotwords decodes by beam search (`modified_beam_search`, 4 paths,
sherpa-onnx's default; 8 heard a few more names and made more false ones); without, greedily, as
before. `hotwords: false` turns them off; Moonshine never uses them.

**An empty segment is heard again.** sherpa-onnx 1.13.8's beam search for TDT models keeps
hypotheses at different frames in one beam, so now and then a path of blanks alone, which skips
ahead in fewer and likelier steps, wins and a segment comes out empty (3 of 907 segments for the 0.6B
on the committed sets, each one greedy search heard whole; none for the 110M). Such a segment is
decoded again with 0.3 s of silence after it, then 0.1 s before it, which moves the frames; on the
same sets that brought all three back, and it costs nothing on segments that come out.

**Cut as the model cuts.** A hotword boosts exactly the pieces it is cut into, so it must be cut as
the model's own tokenizer cuts the word, or the boost goes along a path the model seldom takes.
Parakeet's tokenizer is a SentencePiece BPE model (1,024 pieces, `tokens.txt` in merge order), but
sherpa-onnx cuts plain-text hotwords with a unigram encoder over a `bpe.vocab`: with scores of minus
the rank it cuts "today" as `▁t od ay` where the model says `▁today`, "and" and "everything" wrongly
too, about a third of common English words; no choice of scores did better than one word in eight.
So the mod cuts each hotword itself with the model's BPE merges (`speech/ParakeetPieces.java`; the
same pieces as Python's sentencepiece for all 8,142 words tried, on both models), and hands the
pieces to sherpa-onnx by code name: the recognizer reads a copy of `tokens.txt` whose pieces are
renamed `qbab`, `qbac`… (`tokens-hotwords.txt`, written beside it), with `modeling_unit cjkchar`, which
looks each space-separated word of a hotword up as it is; the text is put back together from the
pieces it answers. "AI" is `▁A I`, the first piece the word "A", which is why its boost is the lowest.

**Threads.** The tab list is read on the client thread and the phrases arrive on
`vfg-incantations`; each replaces an immutable list. `vfg-stt` builds the hotwords string once for
each change and uses the latest for each segment it decodes.

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
its own thread, `vfg-kws`, one CPU thread. Vosk listens beside it (next section).

- **The list.** While the mod listens, it asks the bridge for the player's incantations, at once
  on joining and then by long poll (`after` the version it has, `wait=25`; `304` is no change).
  Every phrase and alias is a keyword, the first 10 aliases of each (`incantation_spotter_aliases`;
  the bridge lists up to 24, likeliest first); a hit on an alias casts the phrase. A phrase may be one
  word: it then casts in any sentence it is said in, and at the start of longer words ("heal" in
  "healing").
- **The tokens are made here.** The box sends plain phrases; the mod splits them into the model's
  tokens with the model's own `bpe.model` (`kws/SentencePiece.java`, checked against Python's
  sentencepiece on 45 phrases by `tools/kws_bpe_fixture.py`). That file is a *unigram* model, not
  BPE, despite its name. A keyword with a token the model lacks (a digit) is left out, with a log line.
- **A wide beam.** The decoder keeps 16 paths alive (`max_active_paths`, the config's
  `incantation_paths`), where sherpa-onnx's default is 4: a keyword's path survives fast speech.
  On Kokoro takes of eight phrases at 1, 1.3, 1.6 and 2 times its speed (below), 4 heard 86, 83, 65
  and 34%, 16 heard 95, 93, 84 and 58%, at no more CPU.
- **Only while someone speaks.** The capture thread hands every 32 ms window to `vfg-kws` by a
  non-blocking offer (a full 2 s queue drops it). The spotter has a VAD of its own, a second Silero
  on the same file and threshold, which ends speech after 0.15 s of silence where the lines' VAD waits
  0.35 s (a median 0.22 s after a sentence's sound, against 0.41 s), for about 70 µs a window, 0.2% of
  a core. It runs once a window on the capture thread, for the spotter and Vosk both, and each gets
  its answer with the window (`kws/SpeechTap.java`); only while one of them listens. Nothing is
  decoded in silence.
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
  The last cast is the player's last, Vosk's too (next section).
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
- **Measured** (`./gradlew kwsTest`, Kokoro TTS, 2026-10-11, with `max_active_paths` 4 and no Vosk; the
  live stream alone in brackets): seven
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

## Incantations: Vosk beside the spotter

Vosk 0.3.45 (Alpha Cephei's speech recognizer, Kaldi inside) listens for the same incantations as
the spotter, on a thread of its own, `vfg-vosk`, over its small US English model
(`vosk-model-small-en-us-0.15`, 41 MB once). The spotter matches sound against a few token
sequences and loses them in fast speech; Vosk decodes words and keeps them. Whichever hears a
saying first casts it, once. `vosk: false` turns it off.

- **Its grammar** is the player's phrases, about 5,100 common English words, and `[unk]`, one JSON
  list (`kws/VoskGrammar.java`), rebuilt with every new list. The phrases only, normalised as the
  spotter's keywords are: the aliases are the spotter's mishearings, and Vosk hears the phrase
  without them. The common words give ordinary speech somewhere else to go: with the phrases alone
  Vosk heard one in nearly anything (272 false casts an hour of real speech), with 3,000 common
  words 13.8, with 5,000 7.6 (the 2026-10-12 research). None is a word of a bound phrase. A phrase
  with a word Vosk does not know ("galick gun", "rasengan") is left out and left to the spotter, with
  one log line per phrase: `vosk: "galick gun" left out, as Vosk does not know galick; the keyword
  spotter alone listens for it`. Vosk knows the 152,217 words of the model's own language model,
  read from the head of its `graph/Gr.fst` (`kws/VoskVocabulary.java`): the same list, number for
  number, that Vosk's `vosk_model_find_word` answers from.
- **Its audio** is what the spotter hears while someone speaks: the capture thread offers it every
  window too (a 2 s queue of its own), with the spotter's VAD's answer (above), so the two hear
  speech start and end on the same window. When speech starts it is fed up to the last second it has not
  had (it hears the first word better with more before it: at twice Kokoro's speed 79% with 1 s, 75%
  with 0.64 s, 72% with 0.32 s); when speech ends, 0.3 s of zeros, so it decodes the last words at
  once, and then 1 s more of the real audio: its decoder runs about half a second behind, and fast
  speech's last word often shows only then; its own endpointing then ends the utterance at the pause.
  Nothing more is fed in a longer silence. Audio that does not follow on (a new capture, windows
  dropped from a full queue) resets the recognizer, which keeps its grammar and goes on counting its
  times over every sample it was fed, as the worker does.
- **It casts from its partial text**, read after every window: a phrase there as whole words
  (`kws/PhraseMatcher.java`; "fire ball" is not in "fire balloon"), each saying once however the
  text is revised, a phrase said twice ("fireball fireball") twice, and the longest phrase at the same
  words ("hot fireball", not "fireball" in it), as soon as a partial shows it. A partial can show a
  phrase's words together for a moment and take them back ("Throw the ball into the fire." flickered
  "fire ball" in `kwsTest`); with `vosk_confirm` a saying casts only once it holds: when the next
  partial that differs still shows it, when speech ends with it shown, or in the utterance's final
  result, and one that does not hold is dropped (and is new if it shows again). It is off: on the
  takes below it made a cast Vosk heard first a median 0.26 s later (−0.02 to +0.24 s after the
  phrase, the 90th percentile +0.22 to +0.28 s), and lost 3 points of the phrases said at twice
  Kokoro's speed (Vosk takes back real sayings too), to save 2 of Vosk's 4 false casts in the 39 min
  of real speech. A saying that shows only in an utterance's final result is cast too, with its
  words' times, unless that is more than 1 s after it ended.
- **The window is estimated.** A partial has no times; it shows a phrase a median 0.63 s after its
  first word starts (0.78 s at the 90th percentile) and 0.02 s after it ends. So a Vosk cast's
  `started_at` is 0.8 s of Vosk's audio before the newest sample it had and its `ended_at` 0.15 s
  before it: it covers the phrase nine times in ten, and ends about where it does, early more often
  than late, as a re-check's cast does. Ending at the newest sample instead, it ran into a phrase said
  right after, which the spotter then could not hear (below). A saying found only in a final result
  has its words' own times.
- **One saying, one cast** (`kws/CastGate.java`). Each engine claims a cast in a gate the two share
  before posting it. A claim of the same incantation that overlaps by at least half the shorter
  window one the other engine claimed (Vosk's widened by 0.3 s each side, the bridge's own slack, as
  its times are estimates) is that saying again, and is not posted; each claim stands for one saying
  of the other's, so "fireball fireball" said fast stays two. Another incantation over the
  same audio is a cast of its own, as it is for the bridge ("hot fireball" by Vosk where the spotter
  heard "fireball"), but one whose words are inside a longer phrase the other engine cast over that
  audio is that saying again ("fireball" in the tail of Vosk's "hot fireball"). The spotter's rule holds across both: its re-checks never reach back past the
  end of the player's last cast, Vosk's included, and a Vosk cast closes the live stream, which
  reopens from after it; a held hit inside a longer phrase Vosk cast ("fire", "fire ball") is
  dropped. For a Vosk cast that end is 0.3 s before its estimated one: one time in ten the estimate
  is up to 0.27 s late, and would run into a phrase said right after ("ice beam fireball"), which the
  spotter could then not hear; the tail of Vosk's saying it may hear again is refused as the same
  saying. The bridge's duplicate check stays behind it all. A cast's log line says `by vosk`:
  `cast "ice beam" (heard "ice beam" by vosk): fired as command_2, answered 0.31 s after the phrase,
  0.24 s after the hit`. `heard` is the phrase itself.
- **Setup.** Vosk's library for this PC and its model download and load on `vfg-kws-setup` after
  the spotter's, into `<game folder>/vfg/natives/vosk-0.3.45-<platform>/` and
  `<game folder>/vfg/models/vosk/`. The platform is the one the speech engine's natives are chosen
  by (`Assets.platform`: `os.name` and `os.arch` to `win-x64`, `linux-x64`, `osx-x64` or
  `osx-aarch64`; any other runs no voice at all). JNA (Minecraft's) is told to look in that folder
  first, under both names Vosk's Java API asks for (`vosk`, and `libvosk` on Windows), so it loads
  the file in place, and on Windows finds the MinGW runtime beside it; nothing is copied to the
  temporary folder. If the library or the model will not download or load, the log says so once
  (`vosk not installed (…)` or `vosk failed to load (…)`; incantations are heard by the keyword
  spotter alone) and the spotter listens alone.
- **Cost.** About 0.05 of a core while someone speaks on and on (39 min of real speech; 0.09 to 0.13
  over short Kokoro takes, whose second of pre-roll and second of tail it decodes too), against the
  spotter's 0.47 to 0.54 (`kwsTest`, two runs); nothing in a long silence. The model takes about 130 MB of memory and loads in
  0.6 s; a grammar of 5,100 words builds, and its recognizer starts, in about 0.1 s.

**Measured** (2026-10-12; the research's Kokoro takes: eight phrases, said alone, in sentences and
twice, in five voices at 1, 1.3, 1.6 and 2 times Kokoro's speed, 940 takes; and 39 min of real
people's speech, 256 clips of Pipecat's Smart Turn test set, for false casts; the mod's own
`SpotterWorker` and `VoskWorker` played on the audio clock as `kwsTest` plays them). The aliases are
the box's: 20 from the alias call and the code's joined and split forms, its everyday ones left out
(about 19 a phrase), of which the spotter keeps the first 10:

| | x1.0 | x1.3 | x1.6 | x2.0 | false casts an hour | after the phrase, median / 90th |
|---|---|---|---|---|---|---|
| the spotter, `max_active_paths` 4 (0.3.3), ~7 aliases | 86% | 83% | 65% | 34% | 15.3 | +0.05 / +0.33 s |
| the spotter, 16, ~7 aliases | 95% | 93% | 84% | 58% | 19.9 | −0.04 / +0.25 s |
| Vosk alone | 95% | 95% | 88% | 74% | 6.1 | +0.02 / +0.21 s |
| **the mod: the spotter (16, 10 aliases) and Vosk** | **98%** | **97%** | **94%** | **78%** | **21.4** | **−0.05 / +0.20 s** |
| the research's estimate | 99% | 98% | 95% | 82% | ~24 | −0.05 / +0.24 s |

How many aliases the spotter keeps (`incantation_spotter_aliases`), with Vosk and alone:

| first N aliases | with Vosk: x1.0 / x2.0 | false casts an hour | the spotter alone: x1.0 / x2.0 |
|---|---|---|---|
| 6 | 98 / 78% | 22.9 | 95 / 57% |
| **10** | **98 / 78%** | **21.4** | **98 / 57%** |
| 20 | 98 / 78% | 26.0 | — |
| all (~19) | 98 / 78% | 26.0 | 98 / 59% |

With Vosk the hits hardly move; past 10 the aliases add false casts ("ice been", "is beam", "bleed
dash" on real speech: 5 in 39 min against 2). Alone, 6 cost the spotter 3 points at normal speed,
and all of them won it 2 at twice the speed, within the noise. Two-word phrases at 2 times its speed 79%, one-word 73% (the spotter alone 58
and 58%). The gap to the research's estimate is Vosk's: the research ran Vosk on its own 16 kHz
timeline and added its casts to the spotter's afterwards; on the mod's audio Vosk alone hears 74% at
2 times, against 77% there, and adding the two engines' casts afterwards on the same audio gives 98,
98, 94 and 78%: the one-cast rules cost at most a point at run time (the "twice" takes lose most:
Vosk's windows are estimates, so where the spotter heard one saying and Vosk the other, the gate
sometimes takes them for one). The false casts on real speech, 14 in 39 min: the spotter's 10 ("fire"
8 times, by the live stream) and Vosk's 4. The takes (16 kHz) are brought to 48 kHz and back through
the mod's resampler, as `kwsTest` plays them; Vosk heard the same takes better on the research's plain
16 kHz timeline (79% at 2 times), so a real 48 kHz mic may do better than this.

### The filler words

`src/main/resources/com/voiceforgaming/vfg/kws/vosk-fillers.txt` is made by `tools/vosk_fillers.py`
(`uv run --with vosk tools/vosk_fillers.py --model <the unpacked model>`): the 5,000 commonest words
of Mozilla Common Voice's English sentences, `sentence-collector.txt` and `wiki.en.txt` from
github.com/common-voice/common-voice at a pinned commit, checked by SHA-256 (CC0-1.0, public domain),
that Vosk knows, each file counting as much as the other; then 105 words of everyday speech and
Minecraft written sentences seldom hold ("yeah", "gonna", "i'm", "creeper"), picked by hand for the
mod. It ships under the repo's MIT license. The research's list came from wordfreq, whose data is
CC BY-SA 4.0; this one does as well on the same measures: 4 false casts in the 39 min of real speech
(wordfreq's 5), and the same hits on the takes.

### Check the spotter and Vosk: `./gradlew kwsTest`

The keyword model and Vosk's installed through the mod's installer, Kokoro takes (kept with the
speech test's), the spotter's VAD and its `SpotterWorker`, and Vosk's `VoskWorker` with a VAD of its
own, sharing a `CastGate`, fed 32 ms at a time faster than real time, with `vfg-kws` played on the
same clock: a turn of it (every window waiting, then a re-check) starts when the thread is free and
lasts as long as it took, so a window that comes during a re-check waits, and a cast counts as heard
when its turn got that far, its compute included; `vfg-vosk` is played the same way, as a thread of
its own. Every case runs three ways, the mod's (re-checks over 3 s, the live stream and Vosk), the
spotter alone (`vosk: false`) and the live stream alone with its push
(`incantation_recheck_seconds: 0`): phrases alone and ending a sentence, inside sentences, the
after-a-pause case, ordinary sentences and near misses, phrases that start longer words ("fire
balloon"), a spell after 0–12 s of nonstop talk, one-word phrases, the two-word phrases at 1.6 and 2
times Kokoro's speed, Vosk's vocabulary and grammar, the spotter's VAD, latency and CPU, in about
20 min once the takes are made. It checks what is certain on the mod's way (Prototype_Fast_Casts: a
median at most 0.1 s and a 90th percentile at most 0.2 s after the phrase ends; "fire ball" heard as
often as before, alone and inside sentences; no false casts in the ordinary requests, nor any by the
spotter alone in the near misses; no saying cast twice) and reports the rest, in
`build/kws-test/report.txt`. `-Pkws-sweep` adds a table over thresholds, boosts and trailing
blanks on the stream alone (about 3 min); `-Pkws-lead` how much silence a re-check keeps, and 3 s
against 1.2 s.

### End to end: `./gradlew castCheck`

Plays a WAV, or Kokoro saying `--say` texts, through the mod's own pipeline, spotter and Vosk as if
from the mic, posts casts and lines to a real bridge, and prints for each when it was said (on the
audio clock, as UTC and as seconds into the WAV), heard (and by which engine), posted and answered. The WAV plays through the
speech test's stand-in sound card, which delivers samples as the wall clock runs, and the capture
thread stamps each read as it does a mic's, so the audio clock maps to the wall clock as a mic's
does: sample `i` is at about the moment the device opened plus `i / rate`.

```sh
VFG_CLIENT_TOKEN=test python3 tools/fake_bridge.py --port 8787 --incantation "fire ball=fireball"
./gradlew castCheck --args="--bridge http://127.0.0.1:8787 --token test --player Steve --say 'Fire ball!' --say 'Okay, fire ball on the zombie now.'"
./gradlew castCheck --args="--bridge http://127.0.0.1:8787 --token test --player Steve --wav build/cast-check/said.wav"
```

It listens for the player's incantations as the bridge lists them; `--incantation "p=a,b"` adds
some for a bridge that lists none. `--threshold`, `--boost`, `--recheck` (seconds, 0 for the stream
alone) and `--paths` are the config's, and `--no-vosk` leaves the spotter alone; `--help` lists all.
Each cast says which way heard it (`by the re-check`, `by the stream`, `by vosk`), and its answer how
long after the phrase's end it came, as the mod's log does.

## Configuration

`<game folder>/config/vfg-client.json`, written by Voice settings:

```json
{ "config_version": 2, "server_address": "", "bridge_url": "", "client_token": "", "mic_device": "",
  "model": "parakeet-0.6b-v2-en", "icon_corner": "top_left", "muted": false,
  "turn_model": "smart-turn-v3.2", "turn_threshold": null, "silence_seconds": 3,
  "vad_threshold": 0.5, "stt_threads": 2, "log_text": false,
  "incantations": true, "incantation_threshold": 0.25, "incantation_boost": 1.5,
  "incantation_recheck_seconds": 3, "incantation_paths": 16, "incantation_spotter_aliases": 10,
  "vosk": true, "vosk_confirm": false, "hotwords": true }
```

- `client_token` is `VFG_CLIENT_TOKEN`. It lives only in this file, is shown masked, and
  is never logged.
- An empty `bridge_url` means `http://<host of server_address>:8787`.
- `model` is `parakeet-0.6b-v2-en` (the default; `parakeet-0.6b` also works), `parakeet-110m-en`
  (`parakeet-110m`; less memory, more errors), `moonshine-base-en` or `moonshine-tiny-en` (the
  lightest). Changing it downloads the new model (if it is not there yet) and reloads the engine
  after Done; choosing the 0.6B while it is not there yet has the 110M listen while it downloads
  (The speech model, below).
- `config_version` is 2 from 0.3.5. A file without it that says `parakeet-110m-en`, 0.3.4's
  default, which every save wrote, moves to `parakeet-0.6b-v2-en` once, with a log line, and is
  written back with `config_version: 2`; a player who picks 110M after that keeps it. A file that
  says `moonshine-…` keeps it (a config written before Parakeet says `moonshine-base-en`).
- `hotwords` (on by default; file only): a Parakeet model favours the hotwords, decoding by beam
  search (Hotwords, below); off, it decodes greedily with none, as 0.3.4 did. Changing it reloads
  the engine. Moonshine never uses them.
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
  the fallback if the re-checks cost frame rate. `incantation_paths` (1–64, a whole number, default 16)
  is the spotter's beam, sherpa-onnx's `max_active_paths` (its own default is 4), read when the
  keyword model loads, at start: wider hears fast speech far more often (above).
  `incantation_spotter_aliases` (0–24, a whole number, default 10) is how many of each
  incantation's aliases, the first, the spotter listens for: the bridge lists up to 24, likeliest
  first, and every alias is a keyword, so more hear more mishearings and cast more by mistake
  (below). Vosk listens for the phrases alone.
- File only, Vosk: `vosk` (on by default, with `incantations`; off, Vosk's model and library are never
  downloaded and the spotter listens alone) and `vosk_confirm` (off: a saying Vosk hears casts as
  soon as a partial shows it; on, once it holds, above). All three keys are in the config line the
  log starts with.
- A file with no `turn_model` key was written before the turn model: when it is read, a
  `silence_seconds` of 5 becomes 3 (any other value stays) and the file is written back once.
- A file that does not parse is renamed `vfg-client.json.bad` and the defaults are used.

The engine and models live in `<game folder>/vfg/` (about 920 MB unpacked on Linux for both
Parakeet models (631 and 131 MB), Smart Turn, the keyword model and Vosk's model and library; each
other model chosen adds its own folder). The log is the game's `logs/latest.log`, logger `vfg`.

## Downloads

Each checked by SHA-256 as it streams. Only this PC's natives, the VAD, the chosen speech
model, the chosen turn model, the keyword model (unless `incantations` is off) and Vosk's model and
this PC's Vosk library (unless `vosk` or `incantations` is off) are fetched. From
`https://github.com/k2-fsa/sherpa-onnx/releases/download/`:

| File | Bytes | SHA-256 |
|---|---|---|
| `v1.13.8/sherpa-onnx-native-lib-win-x64-1.13.8.jar` | 8277046 | `33fbdbd5410e9ba9bdda94aa164ec8f7825bb49246420d8ce9bdd88219d97039` |
| `v1.13.8/sherpa-onnx-native-lib-linux-x64-1.13.8.jar` | 10515871 | `30c93b59381113f9c20aedbbf9fc1ad399158f6bc03dddc0f8934a6e28e069ba` |
| `v1.13.8/sherpa-onnx-native-lib-osx-aarch64-1.13.8.jar` | 9460832 | `42e272180c8836127f024f3335d7afcdfb30fe0164b78330d5d449034e34ce34` |
| `v1.13.8/sherpa-onnx-native-lib-osx-x64-1.13.8.jar` | 10782466 | `9190c28951d85efdbd376bae6b6dff12993311ad605945289e8459bf9c886a96` |
| `asr-models/silero_vad.onnx` | 643854 | `9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6` |
| `asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8.tar.bz2` (default) | 482468385 | `157c157bc51155e03e37d2466522a3a737dd9c72bb25f36eb18912964161e1ad` |
| `asr-models/sherpa-onnx-nemo-parakeet_tdt_transducer_110m-en-36000-int8.tar.bz2` (its fallback, and what listens while it downloads) | 108035095 | `f628312e9fdf8686374cb01a69425c41732529d540860311f16f37cbc32cfe9b` |
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

Vosk's model, Alpha Cephei's small US English one (Apache-2.0), from its official address, into
`<game folder>/vfg/models/vosk/vosk-model-small-en-us-0.15/` (71 MB unpacked, the zip's 14 files):

| File | Bytes | SHA-256 |
|---|---|---|
| `https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip` | 41205931 | `30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498` |

Vosk's library for this PC alone, into `<game folder>/vfg/natives/vosk-0.3.45-<platform>/`: Windows
x64 and Linux x64 from Vosk's GitHub release, keeping `libvosk.dll` and its MinGW runtime
(`libstdc++-6.dll`, `libgcc_s_seh-1.dll`, `libwinpthread-1.dll`; 54 MB unpacked) or `libvosk.so`
(26 MB); macOS, Intel or Apple silicon, from the Vosk jar on Maven Central, the only place 0.3.45's
macOS library is published, keeping `darwin/libvosk.dylib` (one file for both, 12.5 MB). They are
byte for byte the files the Maven jar carries:

| Platform | File | Bytes | SHA-256 |
|---|---|---|---|
| `win-x64` | `https://github.com/alphacep/vosk-api/releases/download/v0.3.45/vosk-win64-0.3.45.zip` | 14882445 | `f1dcc9cca460630f81ea8f71794f69c80bed6556d2a4e6237b5785e1d2dff34b` |
| `linux-x64` | `https://github.com/alphacep/vosk-api/releases/download/v0.3.45/vosk-linux-x86_64-0.3.45.zip` | 7185065 | `bbdc8ed85c43979f6443142889770ea95cbfbc56cffb5c5dcd73afa875c5fbb2` |
| `osx-x64`, `osx-aarch64` | `https://repo1.maven.org/maven2/com/alphacephei/vosk/0.3.45/vosk-0.3.45.jar` | 25861916 | `9e38e96e4448a41d889bb254b2f8424945554945c6e77e1cd97a3d8633fac2ba` |

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
- macOS is not tested; its natives download the same way and may simply work. Vosk's library
  was loaded from its folder, through the built jar, on Linux only; Windows and macOS are untested.
- The models are NVIDIA's Parakeet TDT 0.6B v2 (`nvidia/parakeet-tdt-0.6b-v2`) and Parakeet TDT
  110M (from `nvidia/parakeet-tdt_ctc-110m`), CC-BY-4.0, converted by sherpa-onnx; Moonshine is
  MIT, Silero VAD MIT, sherpa-onnx Apache-2.0. Smart
  Turn is BSD-2-Clause (Daily) and turn-1-mini Apache-2.0 (p99lab); the jar carries their
  `LICENSE-*.txt` and `NOTICE-turn-1-mini.txt`. Vosk (`com.alphacephei:vosk` 0.3.45, its Java API
  in the jar and its downloaded library) is Apache-2.0, `LICENSE-vosk.txt`; its Windows download
  includes MinGW's runtime (`libstdc++-6.dll` and `libgcc_s_seh-1.dll`, GPL-3.0 with the GCC Runtime
  Library Exception, and `libwinpthread-1.dll`, MIT-style), as Vosk ships it. Vosk's model is Apache-2.0 (Alpha Cephei).
- Vosk's filler words, `src/main/resources/com/voiceforgaming/vfg/kws/vosk-fillers.txt`, are made
  by `tools/vosk_fillers.py` (below) from Mozilla Common Voice's English sentence lists, which are
  CC0-1.0, and a short list of everyday and Minecraft words written for the mod; the file ships
  under the repo's MIT license.
- Moonshine returns no text for a clip of about 9.3 s or more, so the VAD ends a segment
  after 7 s of unbroken speech (at the quietest point after it; segments run to about 8 s),
  and the engine decodes anything longer than 9 s in pieces. A pause shorter than the
  silence setting still never splits a line.

## Layout

```text
build.gradle  settings.gradle  gradle.properties  gradlew  gradlew.bat  gradle/wrapper/
libs/                               the fetched sherpa-onnx-jvm and vosk jars (gitignored)
tools/fake_bridge.py                stand-in bridge, Python 3 stdlib
tools/turn_data.py                  turnCompare's public set, from Pipecat's test shard (run through uv)
tools/kws_bpe_fixture.py            the keyword tokens' fixture, from Python's sentencepiece (run through uv)
tools/vosk_fillers.py               Vosk's filler words, from Common Voice's sentences (run through uv)
reports/turn-compare.md             turnCompare's report, as last run
src/main/java/com/voiceforgaming/vfg/
  VfgClient.java                    entrypoint; Fabric events, keys, HUD, the Voice… button
  VoiceService.java                 everything wired together, no Minecraft classes
  VoicePipeline.java                mic -> VAD -> speech-to-text -> lines; also run by micCheck and speechTest
  config/VfgConfig.java             load, validate and save the config; env overrides
  config/SettingsScreen.java        the settings screen
  setup/Assets.java                 the pinned downloads, platform detection, paths
  setup/AssetInstaller.java         download (carried on where it broke off), verify, unpack, progress
  setup/ModelChoice.java            which speech model loads now, which is swapped in later, the load mark
  audio/MicCapture.java             OpenAL capture thread, device list
  audio/Resampler.java              48 kHz to 16 kHz
  speech/SpeechEngine.java          VAD and recognizer (the only sherpa-onnx code); greedy, or beam search with hotwords
  speech/Hotwords.java              the hotwords: AI, brain, the tab list, the server's phrases, command words; boosts
  speech/ParakeetPieces.java        a Parakeet model's pieces: its own BPE cut, code names, text back
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
  net/IncantationClient.java        the player's incantations and the server's phrases, by long poll
  kws/SpotterWorker.java            the vfg-kws thread: windows in, re-checks and the live stream while speech, casts out
  kws/Spotter.java                  sherpa-onnx's KeywordSpotter and the spotter's VAD (its only sherpa-onnx code)
  kws/SpotterVad.java               the spotter's own VAD, which ends speech after 0.15 s of silence
  kws/Keywords.java  kws/SentencePiece.java   phrases and aliases to the model's tokens and keyword string
  kws/VoskWorker.java               the vfg-vosk thread: speech windows in, Vosk's partial text read, casts out
  kws/Vosk.java                     Vosk's model and recognizers (its only Vosk code)
  kws/VoskGrammar.java              the phrases, the filler words and [unk] as Vosk's grammar
  kws/VoskVocabulary.java           the words a Vosk model knows, from its graph/Gr.fst
  kws/PhraseMatcher.java            phrases in Vosk's words: whole words, each saying once
  kws/CastGate.java                 one saying, one cast, across the spotter and Vosk
  kws/SpeechTap.java                the capture thread's tap: one VAD says for both when someone speaks
src/main/resources/com/voiceforgaming/vfg/kws/vosk-fillers.txt   Vosk's filler words
  session/ServerAddressMatch.java   normalising and comparing addresses
  session/SessionGate.java          whether to listen, and the chat notices
  session/StatusBoard.java          the one status snapshot the HUD and screen read
  hud/ListeningIcon.java            the corner icon
src/test/java/com/voiceforgaming/vfg/   unit tests; EngineSmokeTest is tagged "engine", SpeechTest "speech",
                                    kws/SpotterEngineTest "engine" and "kws"
  tools/MicCheck.java               ./gradlew micCheck
  tools/SttCompare.java  tools/SttScores.java   ./gradlew sttCompare, and its scores and report
  tools/TurnCompare.java            ./gradlew turnCompare
  tools/CastCheck.java              ./gradlew castCheck
  testing/                          the engine for tests, WAV files, Kitten and Kokoro TTS, the stand-in
                                    sound card, the models sttCompare measures (SttModels)
src/test/resources/com/voiceforgaming/vfg/tools/stt-addressing.txt   sttCompare's addressing set (invented)
reports/stt-compare.md              sttCompare's report, as last run
```
