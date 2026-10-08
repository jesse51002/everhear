# VFG Voice — the client mod

A client-only Fabric mod for Minecraft Java 26.3. While you are connected to the one
server it is set up for, it turns what you say into text **on your own PC** and posts
each line — ended by 5 s of silence, with its time and its volume — to that server's
bridge (`POST /v1/lines`, docs/CONTRACTS.md §2). No audio ever leaves your PC, and it
never uses Minecraft's own networking, so it works on a vanilla server.

Speech-to-text is [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) 1.13.8 with the
Silero VAD and NVIDIA's Parakeet TDT 110M (English, int8; CC-BY-4.0). Moonshine base and
tiny can be chosen instead. The jar is small; the engine and model (about 120 MB)
download once on first start, pinned by SHA-256.

## For players: install

Windows, and the same elsewhere:

1. Install **Fabric Loader 0.19.5** for **Minecraft 26.3** with the Fabric installer
   (<https://fabricmc.net/use/installer/>). It adds a launcher profile.
2. Put **Fabric API `0.162.0+26.3`** and **`vfg-client-<version>.jar`** (the server's owner
   sends it) in your `.minecraft/mods/` folder.
3. Start that profile. The first start downloads about 120 MB in the background;
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
./gradlew speechTest    # real speech through the mod's pipeline in real time, about 90 s (below)
./gradlew micCheck      # your own mic through the mod's pipeline, no Minecraft (below)
./gradlew sttCompare    # the models side by side on the same audio: errors, speed, size, memory (below)
```

The version is `mod_version` in `gradle.properties`. **Raise it for every build you hand
out.** Updating is replacing the jar; the downloaded engine and model stay.

`./gradlew build` fetches `libs/sherpa-onnx-jvm-1.13.8.jar` from the sherpa-onnx
release once, checks its SHA-256, and copies its classes into the mod jar.

### Check your mic: `./gradlew micCheck`

Runs the mod's own speech pipeline — the same `VoicePipeline` class the mod runs: OpenAL
mic, Silero VAD, Parakeet, line assembler — on a real microphone, without Minecraft.
It lists the mics, prints a level meter once a second with what the VAD thinks
(`listening`, `SPEECH`, `silence, line open: closes in 3.8 s`), every segment it
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

Other options: `--seconds N`, `--token T`, `--player NAME` (the posted `player_name`,
default `MicCheck`), `--silence S`, `--vad-threshold X`, `--threads N`; `--args="--help"`
lists them.

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
stand-in sound card that delivers samples as the wall clock runs — once at 16 kHz, and once
at 48 kHz so the mod's resampling path is used — and checks how many lines come out,
where they split, which words they hold, and how long after the end of speech each was
ready (5 s of silence, plus 0.5 s grace, plus at most a 250 ms tick; speech-to-text runs
during the silence, so it adds nothing).

- Real recordings from sherpa-onnx: the Kennedy clip, "how do I get to Dublin?", and 18 s
  of unbroken lecture.
- Minecraft phrases ("Build me a castle by the cave", "Make it day", "Who's winning?",
  "Give everyone diamonds", "Spawn a zombie behind Steve", "Turn off the rain, please")
  spoken by Kitten TTS (sherpa-onnx, ~27 MB, pinned by SHA-256) in six voices at three
  speeds.
- Pauses of 1.5, 2, 3 and 4.5 s must not split a line; 5.5 and 6.5 s must.

It takes about a minute and a half. What it played is left in `build/speech-test/*.wav`, and what it
heard in `build/speech-test/report.txt`.

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
`--delay S` waits before answering.

## Configuration

`<game folder>/config/vfg-client.json`, written by Voice settings:

```json
{ "server_address": "", "bridge_url": "", "client_token": "", "mic_device": "",
  "model": "parakeet-110m-en", "icon_corner": "top_left", "muted": false,
  "silence_seconds": 5, "vad_threshold": 0.5, "stt_threads": 2, "log_text": false }
```

- `client_token` is `VFG_CLIENT_TOKEN`. It lives only in this file, is shown masked, and
  is never logged.
- An empty `bridge_url` means `http://<host of server_address>:8787`.
- `model` is `parakeet-110m-en` (the default; `parakeet-110m` also works), `moonshine-base-en`
  or `moonshine-tiny-en` (the lightest). A file that already says `moonshine-…` keeps it — a
  config written by an earlier version says `moonshine-base-en`, so that player stays on
  Moonshine until they pick Parakeet in Voice settings (Model) or edit the file. Changing it
  downloads the new model (if it is not there yet) and reloads the engine after Done.
- File only: `silence_seconds` (2–30), `vad_threshold` (0.1–0.9), `stt_threads` (1–8),
  `log_text` (logs what was heard and sent). Out-of-range values fall back to the
  default with a log line.
- A file that does not parse is renamed `vfg-client.json.bad` and the defaults are used.

The engine and models live in `<game folder>/vfg/` (about 175 MB unpacked for Parakeet;
each other model chosen adds its own folder). The log is
the game's `logs/latest.log`, logger `vfg`.

## Downloads

From `https://github.com/k2-fsa/sherpa-onnx/releases/download/`, each checked by SHA-256
as it streams. Only this PC's natives, the VAD and the chosen model are fetched.

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

## Notes

- Singleplayer and LAN worlds never listen.
- Java 25 logs a warning that `System::load` is a restricted method; it is harmless. If a
  later Java blocks it, add `--enable-native-access=ALL-UNNAMED` to the profile's JVM
  arguments.
- Under the Flatpak launcher on Fedora the game folder is inside
  `~/.var/app/com.mojang.Minecraft/`, and the mic comes through its PulseAudio socket.
- `started_at` uses the PC's clock; a clock that is far off misorders that player's lines
  on the server.
- macOS is not tested; its natives download the same way and may simply work.
- The model is NVIDIA's Parakeet TDT 110M (from `nvidia/parakeet-tdt_ctc-110m`), CC-BY-4.0,
  converted by sherpa-onnx; Moonshine is MIT, Silero VAD MIT, sherpa-onnx Apache-2.0.
- Moonshine returns no text for a clip of about 9.3 s or more, so the VAD ends a segment
  after 7 s of unbroken speech (at the quietest point after it; segments run to about 8 s),
  and the engine decodes anything longer than 9 s in pieces. A pause shorter than the
  silence setting still never splits a line.

## Layout

```text
build.gradle  settings.gradle  gradle.properties  gradlew  gradlew.bat  gradle/wrapper/
libs/                               the fetched sherpa-onnx-jvm jar (gitignored)
tools/fake_bridge.py                stand-in bridge, Python 3 stdlib
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
  speech/SttWorker.java             the vfg-stt thread
  speech/LineAssembler.java         segments into lines, silence timer, caps
  speech/Levels.java                peak and RMS to dBFS
  speech/SampleClock.java           sample index to wall-clock instant
  net/LinePayload.java              the §2 body and its JSON
  net/BridgeClient.java             the sender, retries, delivery status, connection test
  session/ServerAddressMatch.java   normalising and comparing addresses
  session/SessionGate.java          whether to listen, and the chat notices
  session/StatusBoard.java          the one status snapshot the HUD and screen read
  hud/ListeningIcon.java            the corner icon
src/test/java/com/voiceforgaming/vfg/   unit tests; EngineSmokeTest is tagged "engine", SpeechTest "speech"
  tools/MicCheck.java               ./gradlew micCheck
  tools/SttCompare.java             ./gradlew sttCompare
  testing/                          the engine for tests, WAV files, Kitten TTS, the stand-in sound card,
                                    the models sttCompare measures (SttModels)
```
