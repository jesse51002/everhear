# Everhear

Everhear is an AI that plays Minecraft with you. It listens to what players say into their mics or
type in chat, answers in chat, and changes the game for them: builds, mobs, events, minigames.

The code still carries its working name, `vfg`, in commands, folders and settings.

## How it works

```mermaid
flowchart TB
  subgraph Player["🎮 EACH PLAYER'S PC"]
    direction TB
    Mod["🎙️ client mod<br/>speech to text"]
  end

  subgraph Box["☁️ THE EC2 BOX"]
    direction TB
    subgraph Listen["listening"]
      Bridge["bridge"]
      Jev["Jev filter"]
      Feed["🗄️ feed"]
      Bridge -->|"new lines"| Jev
      Jev -->|"LINE"| Feed
      Bridge -->|"GAME"| Feed
    end
    subgraph Game["the game"]
      Run["running copies<br/>scripts · kit"]
      MC["Minecraft server"]
      Places["🗄️ saved places<br/>everhear:places"]
      Run -->|"RCON · one lock"| MC
      MC -->|"command storage"| Places
    end
    Game -->|"console.log"| Listen
  end

  subgraph Owner["🖥️ THE OWNER'S PC"]
    direction TB
    subgraph Agents["agents"]
      Brain["brain<br/>one call per event"]
      Respond["Respond<br/>the cheap mode"]
      Impl["implementers"]
      Respond -->|"hand up"| Brain
      Brain -->|"brief · EXAMPLES"| Impl
      Impl -->|"report"| Brain
    end
    Vfg["🔒 vfg"]
    subgraph Repo["this repo"]
      Actions["actions/"]
      Library["scripts/<br/>kit · presets · examples"]
      Made["made/‹server›/"]
      Lib["lib/py · structured()"]
      Library -.->|"vfg script new"| Made
    end
    subgraph Home["~/.vfg"]
      Cache["places · running copies · actions"]
      Refs["command references"]
    end
    Agents -->|"every call to the box"| Vfg
    Agents -.->|"load actions · the library · write made/"| Repo
    Agents -->|"tasks · command refs"| Home
    Vfg -->|"writes through"| Home
  end

  TypeSafe["TypeSafe"]
  OpenRouter["OpenRouter"]
  Claude["Claude"]
  Wiki["minecraft.wiki"]

  Player -->|"POST /v1/lines"| Box
  Box -->|"feed · vfg watch"| Owner
  Owner -->|"vfg over SSH"| Box
  Box -->|"judge each line · pick scripts"| TypeSafe
  Owner -->|"brain · Respond · structured()"| OpenRouter
  Owner -->|"implementers · Agent SDK"| Claude
  Owner -->|"command pages, once"| Wiki

  classDef default fill:#ffffff,stroke:#94a3b8,color:#1f2937;
  classDef client fill:#e3f0ff,stroke:#2f6fb0,color:#0b2942;
  classDef svc    fill:#e6f7ec,stroke:#2f8f53,color:#0b2b18;
  classDef ext    fill:#ffd9a8,stroke:#d2691e,color:#4a2c08;
  classDef tool   fill:#fdf6d8,stroke:#caab2f,color:#4a3d08;
  class Mod client;
  class Bridge,Jev,Brain,Respond,Impl,Vfg svc;
  class Feed,MC,Places,Cache,Refs,TypeSafe,OpenRouter,Claude,Wiki ext;
  class Run,Actions,Library,Made,Lib tool;
  style Player fill:#eaf2ff,stroke:#2f6fb0,stroke-width:3px;
  style Box fill:#f7fafc,stroke:#2f8f53,stroke-width:3px;
  style Owner fill:#f7fafc,stroke:#2f8f53,stroke-width:3px;
  style Listen fill:#e6f7ec,stroke:#2f8f53;
  style Game fill:#fff2e0,stroke:#c9781f;
  style Agents fill:#e6f7ec,stroke:#2f8f53;
  style Repo fill:#fdf6d8,stroke:#caab2f;
  style Home fill:#fff2e0,stroke:#c9781f;
  style Vfg stroke-width:4px;
```

*Legend: read top-down, from a player's voice to the game. The three big boxes are the machines everything runs on; an arrow between two of them stands for every call that crosses, with the contract on its label. Blue runs on each player's PC; green is Everhear's own services; orange is data, the game and third parties; yellow is code and scripts the agents load or write. Solid arrows are live calls, the dashed one loads and writes files. An arrow only joins two boxes with the same parent. `vfg` is drawn on its own because every call from the owner's PC to the box goes through it.*

- The **client mod** turns speech into text on the player's own PC (no audio leaves it) and posts
  each line to the **bridge** on the server's EC2 box. The bridge also tails the Minecraft console
  for joins, deaths and the like.
- The **Jev filter** asks Jev (TypeSafe) whether each line asks the AI for something to happen
  (`action_oriented`), talks to it (`conversational`), is context it needs, or is not relevant, and
  writes what it keeps to the **feed**, one line per entry, beside the bridge's game events.
- The **brain**, on the owner's PC, follows the feed over SSH (`vfg watch`). Every action-oriented
  line is one call to a fast OpenRouter model (`structured()`), its own task, so no line waits for
  another. That one call routes each part of the line and does it in the same answer: an
  **action** written in full (an item, an effect, a teleport, a saved place, a castle, a weapon:
  one action over each script, and every one implementers make), which runs at once; a brief for an
  **implementer**; or talk. It writes the player's reply in the same call.
- **Respond**, the brain's cheap mode, answers a conversational line on a cheaper model, or hands it
  up to the brain when it asks for something to happen.
- **Implementers** are started by code from the brain's brief, one per task, each with the scripts
  and presets Jev picks for the job (`vfg route`), whole, and the server's state. They act through
  actions (`vfg action run`): they run one, change a running copy's values, or copy presets into a
  new script and its action with `vfg script new` in `made/<server>/`. The library in `scripts/`
  and `actions/` is read-only during play. Each reports to the brain, which tells the player.
- The **shared context** is the feed itself: every kept line, every game event and what the brain
  did, in order, read whole on every call; the lines Jev files as context are kept but shown to no
  model yet. What arrives between calls is read by the next one.
- **Saved places** live in the world itself, in the command storage `everhear:places`. Every agent reads and
  saves them with `vfg place`, which keeps a 30-second copy in `~/.vfg/places.json`.
- A command Minecraft refuses is planned again once against the server's own command tree and the
  command's minecraft.wiki page, both fetched once per server version into `~/.vfg/brain/refs/`.
- Everything on the owner's PC reaches the box through one command, [`vfg`](toolkit/README.md).

What each part takes in and puts out is in [docs/CONTRACTS.md](docs/CONTRACTS.md); code comments cite it as "Contracts §N".

## The parts

| Folder | What it is |
|---|---|
| [`client-mod/`](client-mod/README.md) | the Fabric mod players install: mic, speech to text, posting lines |
| [`installer/`](installer/README.md) | one link for players that installs Fabric, the mod and today's server |
| [`bridge/`](bridge/README.md) | on the box: the speech endpoint, the console tail, the feed; `deploy/` launches, provisions and retires the box |
| [`jev/`](jev/README.md) | on the box: the Jev filter |
| [`toolkit/`](toolkit/README.md) | `vfg`, and [`config.example`](toolkit/config.example), every setting with its default |
| [`brain/`](brain/README.md) | the brain, Respond and the implementers it starts: one call per event, its [prompts](brain/prompts/), the implementers' guard |
| [`actions/`](actions/README.md) | what anything can run on the server: one pydantic class per file, naming the script it runs and the values it takes; no code |
| [`scripts/`](scripts/README.md) | the script library: the kit every script imports, presets copied into scripts, and the scripts the actions run, which implementers copy |
| `made/` | what implementers make during play, one folder per server (`VFG_SERVER`); gitignored |
| [`lib/py/`](lib/py/vfg_llm.py) | `structured()`, the one OpenRouter call every part makes for a validated pydantic answer; [`vfg_actions`](lib/py/vfg_actions.py), the one loader of the actions |
| [`integration/`](integration/README.md) | the bridge, Jev filter and toolkit run together on one PC, no EC2; [`realmc/`](integration/realmc/README.md) runs real 26.3 servers with fake players, [`replay/`](integration/replay/README.md) replays a playtest's tasks |

## Requirements

- An AWS account, and the AWS CLI v2 logged in to it.
- Claude Code, logged in with a Claude subscription, for the implementers, and
  [uv](https://docs.astral.sh/uv/) for the brain's Python. Implementers never run on an API key.
- A TypeSafe API key, for Jev.
- An [OpenRouter](https://openrouter.ai) API key, for the brain and Respond.
- Minecraft Java 26.3 with Fabric, for every player (the installer sets Fabric up).
- bash, ssh, rsync and curl on the owner's PC; `uv` runs everything there on Python 3.12, as on the box.
- JDK 25 to build the mod (Gradle downloads one if there is none).

## Quick start

From the repo's root. Every setting lives in `~/.vfg/config`; its defaults are in
[`toolkit/config.example`](toolkit/config.example).

```sh
# 0. Once: vfg on the PATH, and ~/.vfg/config from config.example.
toolkit/install.sh
# Fill VFG_OWNER (your Minecraft name) and VFG_SSH_KEY. Read Minecraft's EULA
# (https://aka.ms/MinecraftEULA) and, if you accept it, set VFG_ACCEPT_EULA=true.
# The brain's OpenRouter key, alone in ~/.vfg/openrouter.key with mode 600, typed at a
# hidden prompt (VFG_OPENROUTER_KEY in ~/.vfg/config can point at another file):
(umask 077 && read -rsp 'OpenRouter key: ' key && echo && printf '%s\n' "$key" > ~/.vfg/openrouter.key)

# 1. Once: the EC2 key pair (VFG_KEY_PAIR, default everhear) in VFG_AWS_REGION (default us-east-2).
aws ec2 create-key-pair --region us-east-2 --key-name everhear \
  --query KeyMaterial --output text > ~/.ssh/everhear.pem
chmod 600 ~/.ssh/everhear.pem       # and set VFG_SSH_KEY=~/.ssh/everhear.pem

# 2. Launch the box. It prints its IP: put it in ~/.vfg/config as VFG_SSH_HOST.
bridge/deploy/ec2.sh launch

# 3. Provision Java and Minecraft. --restore brings back the world ec2.sh retire saved.
bridge/deploy/deploy.sh provision          # or: bridge/deploy/deploy.sh provision --restore

# 4. Deploy the bridge, then the Jev filter.
bridge/deploy/deploy.sh
jev/deploy.sh

# 5. The secrets. Once: put TYPESAFE_API_KEY=... in ~/.vfg/box.env (mode 600; VFG_BOX_ENV can
#    name another .env). Step 3 then sends it to every new box and makes the client token there;
#    after you change the file, send it again:
bridge/deploy/deploy.sh env
# Without that file, type each one at a hidden prompt instead (--restore keeps them):
bridge/deploy/set-secret.sh TYPESAFE_API_KEY

# 6. Build the mod and publish the installer link; send the link it prints to your players.
(cd client-mod && ./gradlew build)
installer/publish.sh

# 7. Start the brain. It brings the server up, sets the watchers and follows the feed;
#    its log is ~/.vfg/brain/brain.log.
vfg brain start

# 8. Play: players run the installer, pick "VFG 26.3" in the launcher, join "VFG",
#    press K to pick a mic, and talk. `vfg brain stop` signs the AI off.

# 9. Retire: back the world up into VFG_BACKUP_DIR, check it, terminate the box.
bridge/deploy/ec2.sh retire
```

The box's IP changes on every launch and every stop and start: update `VFG_SSH_HOST` and run
`installer/publish.sh` again for a new link. When your own IP changes, `bridge/deploy/ec2.sh ssh-ip`
moves the SSH rule to it. `bridge/deploy/ec2.sh stop`, `start` and `status` pause and resume a box
without retiring it.

## Costs

Rough guidance from list prices; check the current ones for your region and plan.

| What | How it is billed |
|---|---|
| EC2 | `m7i.xlarge` on demand, about $0.20 an hour while running, plus a 30 GB gp3 disk until the box is retired. `ec2.sh stop` stops the hourly charge; `retire` ends both |
| Jev (TypeSafe) | per call, one per line players say or type; see TypeSafe's pricing |
| OpenRouter | the brain and Respond: a fraction of a cent a call with the conversation cached, roughly $0.30 an hour for one player and $1 for three |
| Claude | implementers run on a Claude subscription and count against its usage limits; at API prices they would be the largest cost by far. `vfg usage add` records each part's tokens and their API-price cost in the `usage` table |
| S3 | the installer bucket holds a few MB, deleted after 8 days |

## Safety

- **Players ask; they do not command.** Only the owner (`VFG_OWNER`) may ask for op or deop, kicks,
  bans, the whitelist, gamemode, difficulty or gamerule changes, stopping the server or resetting
  the world. A line claiming to speak for the owner counts as the player who said it.
- **The brain and Respond run nothing but actions.** They are model calls with no tools: the brain
  answers with actions, whose values `vfg` checks against each action's class before anything is
  sent, and Respond only replies or hands up.
- **The implementers' guard** ([`brain/brain/implementers/guard.py`](brain/brain/implementers/guard.py)),
  a hook on every tool call, and their allow rules refuse:
  - `stop`, `op`, `deop`, `ban`, `ban-ip`, `pardon`, `pardon-ip`, `whitelist`, `kick` and `save-off`
    in any `vfg cmd`, as the first word or after any `run`, however the command is chained.
    Those are the owner's to type: one list, in `scripts/_lib/commands.py`, which `mc.py` (every
    script), `vfg`, the brain and the guard all refuse.
  - for implementers, server-wide settings: `setworldspawn`, `defaultgamemode`, `difficulty` and
    `worldborder`;
  - for implementers, any shell command but `vfg`'s own, `python3 -m py_compile`, `sleep`, `echo`,
    and `ls`, `grep`, `head` and `tail` on files in the repo; `vfg` may be piped into `head`,
    `tail` or `grep`; no `$( )`, backtick, variable, loop or redirection into a file;
  - reading or searching outside the repo; writing anywhere but `made/<server>/`, so the library
    never changes during play; and editing a made script a copy started by another task is running.
- **The box:** RCON answers on loopback only, SSH only from the owner's IP; the game (25565) and the
  bridge (8787) are open. The client token in the installer only lets someone post speech lines.

## Status

An early prototype, run by a few friends on one server. Expect rough edges.

## License

[MIT](LICENSE). The mod bundles sherpa-onnx, under the Apache License 2.0
([`client-mod/LICENSE-sherpa-onnx.txt`](client-mod/LICENSE-sherpa-onnx.txt)).
