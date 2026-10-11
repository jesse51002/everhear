# The orchestrator, measured before it is built

The orchestrator is the LLM version of the old orchestrator: one `structured()` call per implementer part, with no agent and no session. When the brain sends a part of a line to `implementer`, the orchestrator decides what that work is:

- a follow-up to a named task;
- a new task;
- an edit of a copy that is running;
- a stop of a copy that is running.

It names a new task, says what the thing is now and how a player uses it, writes the implementer's brief, and writes the line's chat reply. It sees what the brain sees of the conversation, plus the task list, the line, the brain's instruction and the line's other parts. It does not see the action menu, the commands or the world.

This replays the 2026-10-08 playtest (`taskcall.py`, scored by `taskcall_score.py`). It used every part that the brain's full raw-line replay sent to an implementer: 168 parts on 137 lines, in time order. Model calls only, nothing was run.

## Verdict: Gemini 3.8 Flash, at low reasoning

- **Latency is the same for both:**
  - Gemini: median 3.0 s a call, p90 5.0 s.
  - DeepSeek V4 Flash with reasoning off: median 3.6 s, p90 5.0 s.
  - Neither reaches the design's 1–2 s.
  - The reply waits on the line's first call: 3.1 s / 5.1 s for Gemini, 3.7 s / 5.3 s for DeepSeek.
  - At low reasoning DeepSeek took 7.7 s median and 26 s p90 (prompt v2, 42 parts), so it ran with reasoning off.
- **Gemini keeps one thing to one task, and DeepSeek does not:**
  - Gemini made 49 tasks, DeepSeek 26.
  - DeepSeek put 29 asks about nine different powers (Hollow Purple, the Gatling Gun, the death gaze, the Spirit Bomb, Super Saiyan …) into one task, `infinity_shield_eclipszr`.
  - DeepSeek sent every Galick Gun change to `sniper_nest_styrkaeopteryx`, whose description then became the Galick Gun's.
  - With DeepSeek, a follow-up would resume the wrong implementer.
- **Both pass the follow-up bar:** 90.2% and 87.8% by script, against 85%. That score reads the replay's own chains, so a model that lumps is marked against its own lumps (see *One thing, one task*).
- **Neither passes the new-task bar** of 5%:
  - By script, Gemini took 2 of 9 new tasks for follow-ups and DeepSeek 3 of 9.
  - Only one of Gemini's two is the orchestrator's own mistake: #517, an Ice Beam Staff filed as a change to the Ice Staff.
  - Nine is a small sample. Most of the session's new things were made by library actions, so they never reached the orchestrator.
- **Cost:**
  - Gemini: $1.18 for the session's 168 calls ($0.0075 a call median), about $0.46 an hour over the 2.55 h session.
  - DeepSeek: $0.16 (about $0.06 an hour).
  - Gemini's uncached part is the tail (the task list) plus the minute-blocks since the last cache mark, about 5–6K tokens at $0.75/M.

## Results, prompt v3 (the one we would ship)

A **label artefact** makes the conversation's own labels a poor truth for new tasks. The conversation often wrote a fix or a change as a fresh task, with no `Change to T<x>` head: #222 "my gatling gun doesn't work" became T53 "Fix the Gatling Gun". By those labels, a right follow-up counts as a new task taken. The script-based truth comes first for that reason: a task that wrote a new script is new, and a task that edited an earlier task's script follows up that task.

| | Gemini 3.8 Flash, low | DeepSeek V4 Flash, off | Bar |
|---|---|---|---|
| **A call: median, p90** | **3.0 s, 5.0 s** | **3.6 s, 5.0 s** | |
| **The reply (the line's first call): median, p90** | **3.1 s, 5.1 s** | **3.7 s, 5.3 s** | |
| **Follow-ups, by script**: named the task holding the script it edited | **37 of 41, 90.2%** | **36 of 41, 87.8%** | at least 85%: both met |
| **New tasks, by script**: a new script taken for a follow-up | **2 of 9, 22.2%** | **3 of 9, 33.3%** | at most 5%: neither |
| Follow-ups, by the labels (`Change to T<x>`) | 18 of 19, 94.7% | 18 of 19, 94.7% | at least 85%: both met |
| New tasks, by the labels: taken for follow-ups | 42 of 65, 64.6% | 50 of 65, 76.9% | at most 5%: neither (the artefact) |
| Tasks made | 49 | 26 | |
| Lumped: tasks holding more than one script's work | 7; the biggest holds 3 scripts | 8; the biggest holds 9 (29 asks) | |
| Split: scripts spread over more than one task | 9 | 5 | |
| Parts: new / follow-up / edit / stop | 49 / 119 / 0 / 0 | 26 / 140 / 1 / 1 | |
| Cost: the session, a call median | $1.18, $0.0075 | $0.16, $0.0010 | |
| Cached share of prompt tokens | 86.2% (the prompt grew from 5.7K to 58K tokens) | 88.5% | |
| Replies: median length; over 80; over 200 | 70; 14; 0 | 71; 49; 1 | |
| Failed calls | 0 | 0 | |

Notes on the table:

- **Left out of the follow-ups:**
  - 15 by script: they edited a script that no task here held, because the brain had made the thing with a library action.
  - 2 by the labels: the target was written after the line reached the brain.
  - 14 by the labels: no task here held the target.
- **Never reached the orchestrator:** four labelled follow-ups that the brain answered as talk (#127, #461, #505, #660).
- **Edits and stops:** expected never, because RUNNING NOW is unknown in this replay and the prompt says so.
  - Gemini chose none.
  - DeepSeek chose two anyway: #56 `stop green_block_changer, chunk_changer` for "disable all custom scripts", and #595 `edit super_saiyan_blue_eclipszr: duration=55` for "set Super Saiyan Blue to 55 seconds".
- **Every miss** with the line, the answer and the truth: `taskcall_scores/gemini-v3.md` and `taskcall_scores/deepseek-v3.md`.

### One thing, one task

The follow-up scores are read from the replay's own chains. The chains are built from what the orchestrator answered: a conversation task joins the chain of the task its line was given. So a model that piles everything into one task gets credit for naming that pile.

This measure does not depend on the chains: it sets each task the replay made against the scripts that its conversation tasks wrote or edited (`tasks.jsonl`). Some lumps are the conversation's own: one of its tasks edited several scripts at once, such as T63 (death gaze, Gatling Gun and Infinity Gauntlet) and T144 ("no cooldowns on the current powers").

**Gemini's lumps:**
- `ice_staff_krown` (28 asks): ice_beam, ice_staff, ice_walk. Its one real miss, #517, put the Ice Beam Staff into the Ice Staff task, and every later Ice Beam change followed it there.
- `frost_walker_boots_krown` (11 asks): ice_walk, ice_beam, hollow_purple, ice_audit. Hollow Purple came from #513: the brain gave one part to a line that held two of the conversation's tasks.
- `death_gaze_eclipszr` (8 asks): death_gaze, gatling_gun, infinity_gauntlet. This one is the conversation's own: T63, a single task, edited all three.
- `hollow_purple_eclipszr` (14 asks): hollow_purple, plus the beam clash added to it.
- Three small ones.

**DeepSeek's lumps:**
- `infinity_shield_eclipszr` (29 asks): nine scripts.
- `ice_walker_krownjm` (43 asks): five scripts.
- `immortality_eclipszr` (18 asks): hollow_purple and horror_mode.
- `sniper_nest_styrkaeopteryx` (13 asks): galick_gun.
- `one_shot_trident_krownjm` (13 asks): horror_mode, keep_effects and nuke.
- Three more.

## The misses

### Follow-ups by script

**Gemini, 4:**
- **#403** "remove all damage" → new `no_damage_all`. The conversation's implementer happened to reuse keep_effects, which the replay had given to `restore_kit_krown`. A new rule is defensible.
- **#513** "this ice thing is still breaking" → `frost_walker_boots_krown`. That is right for its first task, T113. The miss is the line's second task, T114 (Hollow Purple), which the brain gave no part of its own.
- **#629** Super Saiyan Blue flight → `super_saiyan_blue_eclips`, and **#654** Spirit Bomb → `spear_bomb_eclipszr`. Both name the power's own task, which is right. The script truth wanted `no_cooldowns_all`, the only task here that touched those scripts: T144 ("remove cooldowns from the current powers", #619) edited both, while the powers themselves came from library actions. A task that cuts across powers is a blind spot of the script truth.

**DeepSeek, 5:**
- **#168** darkness → `one_shot_trident_krownjm`.
- **#403** all damage → `infinity_shield_eclipszr`.
- **#513**, the same as Gemini's.
- **#525** Hollow Purple range → `infinity_shield_eclipszr`.
- **#654** Spirit Bomb → `meteor_toss_eclipszr`.

### New tasks taken for follow-ups, by script

**Gemini, 2:**
- **#239** "a barrier around me" → `infinity_power_eclipszr`. The brain's own instruction was "Check and fix Eclipszr's Infinity Gauntlet".
- **#517** "a staff that shoots like Hollow Purple, an ice beam" → `ice_staff_krown`: a different staff.

**DeepSeek, 3:** #239 and #517 the same as Gemini, and **#338** "give my sniper nest a barrier" → `sniper_rifle_eclipszr`.

### Follow-ups by the labels

Both models missed one: **#525** "make it have infinite range" (T118, Change to T114). Both named their Hollow Purple task. The replay had given T114 to the ice task on #513, the brain-part artefact above.

### Where no task held the target (14, by the labels)

These are follow-ups to things the brain made with library actions. Gemini handled the Galick Gun this way:
- T117 was the galick_gun action.
- #544 "remove the cooldown" became a new `galick_gun_styrka`.
- #546, #550, #573, #618 and later lines all followed it up.

DeepSeek sent the same lines to `sniper_nest_styrkaeopteryx`.

### New tasks taken for follow-ups, by the labels

Gemini had 42 and DeepSeek 50. Most are the label artefact:
- #330 "make the range of the ice staff infinite" (T81);
- #545 "remove all exclusions from ice_walk" (T124);
- #291 "remove the rain", whose rain came from the horde task;
- #609, #613 and #631: sounds added to the Galick Gun and Hollow Purple.

All are listed in the score files.

## Sample names and descriptions

**Gemini:**
- `galick_gun_styrka`, 12 asks. "Galick Gun for Styrkaeopteryx: right-click and hold 1.5 s to charge and fire a 150-block beam dealing 30 hearts strictly where aimed, carving a tunnel, with no cooldown."
- `hollow_purple_eclipszr`, 14 asks. "Hollow Purple for Eclipszr: right-click fires an erasing purple beam with dramatic sounds and a shouted on-screen title, clashing with the Galick Gun when beams cross."
- `gatling_gun_eclipszr`, 2 asks. "A Gatling Gun for Eclipszr: hold right-click to rapidly fire piercing projectile beams with knockback and damage, safe from damaging Eclipszr."

**DeepSeek:**
- `gatling_gun_eclipszr`, 2 asks. "A Gatling Gun for Eclipszr: hold right-click to fire a rapid stream of piercing beams, 24-block range, each beam dealing 5 damage, never hitting Eclipszr."
- `ice_castle_stairs_krownjm`, 1 ask. "KrownJM's ice castle now has a gigantic ice staircase descending from its yard all the way down to bedrock, with every block in the way cleared so the path is fully walkable."
- `sniper_nest_styrkaeopteryx`, 13 asks, a lump. It began as "A sniper nest for Styrkaeopteryx built far away from Eclipszr's nest, with a ladder, protected lookout and a name sign." It ended as "Styrkaeopteryx has a Galick Gun: right-click charges 1.5 seconds with purple swirls, then fires a thick purple beam up to 150 blocks …"

**Other names:**
- Gemini: `green_to_cobwebs_krown`, `immortal_eclipszr`, `castle_styrka`, `exploding_trident_krown`, `thousand_mobs_horde_krown`, `ban_wither_wand_krown`, `spear_bomb_eclipszr`, `nuke_countdown_krown`, `bedrock_stairs_krown`.
- DeepSeek: `green_block_changer`, `immortality_eclipszr`, `pretty_house_styrkaeopteryx`, `meteor_toss_eclipszr`, `elsa_ice_boss_eclipszr`.
- DeepSeek did not shorten long player names. Gemini mostly did, as the prompt asks (`krown`, `styrka`; `eclipszr` stays whole).

## Sample replies

The orchestrator writes the line's one chat line, on the line's first implementer part. Its input includes the brain's action and talk parts.

| # | The line, its other parts | The brain's reply then | Gemini | DeepSeek |
|---|---|---|---|---|
| 212 | "I said give me affinity … take me now creative … the power of Day"; talk: take me now creative | Eclipszr: only Krown can change game modes; your protective power is coming. | Eclipszr: keeping you in survival, but the Power of Day will shield all harm | Eclipszr: the Power of Day is coming — it will shield you from all harm without changing your mode. |
| 273 | "Back to the music, the music stopped." | On it — getting the dragon's boss music playing again. | on it — cranking the boss music back up for everyone | Music is back on until the dragon falls. |
| 438 | "remove the Wither Wand, ban the Wither Wand"; talk: ban it | Krown: removing the Wither Wand, and no more wither wands this session. | Krown: wither wand is gone and banned for good | Krown: Wither Wand is gone and its summoning is disabled for good. |
| 618 | "increase the range of Galick Gun and make it 30 hearts of damage" | Styrkaeopteryx: longer range and 30 hearts per hit coming up. | Styrkaeopteryx: on it — boosting your Galick Gun's range and bumping damage to 30 hearts. | Galick Gun range is going up and damage hits 30 hearts. On it, Styrkaeopteryx. |
| 661 | "build an ice castle where I'm standing … gigantic stairs to bedrock"; action: ice_castle | Krown: ice castle is up; huge stairs down to bedrock coming next. | Ice castle going up, with gigantic stairs carved all the way down to bedrock. | Krown: castle is up, and the stairs to bedrock are on the way. |

Replies that name "scripts": Gemini 3 (#56, #121, #477) and DeepSeek 2 (#56, #472). In every one the player had talked about scripts. DeepSeek's #273 says the music "is back on" before the implementer has run.

## Every prompt version scored

The prompts are in `taskcall_prompts/`.

| Version | What changed | Runs | Follow-ups by script | New taken, by script | Labels: follow-ups / new taken | A call: median, p90 | Cost |
|---|---|---|---|---|---|---|---|
| v1 | the first: follow-up or new, name, description, brief | 15-part pilots: Gemini low, DeepSeek low | none in the first 15 parts | none | none / 0 of 6 both | Gemini 3.0, 4.1 s; DeepSeek 14.3, 27.6 s, 1 call over the 30 s timeout | $0.04, $0.01 |
| v2 | + `stop` (copies under RUNNING NOW); the replay notes moved from the rules to the tail | Gemini low: 135 of 168 parts (its $0.80 cap) | 30 of 33, 90.9% | 2 of 7 | 15 of 16 / 36 of 57 | 2.8, 4.7 s | $0.81 |
| v2 | | DeepSeek low: 42 parts, stopped when the design changed | 3 of 3 | 2 of 4 | 2 of 2 / 8 of 24 | 7.7, 26.2 s | $0.07 |
| v3 | + `edit` (copy and values), + `reply` (the line's chat line, covering its action and talk parts), the description says how a player uses the thing | full: Gemini low, DeepSeek off | 37 of 41; 36 of 41 | 2 of 9; 3 of 9 | 18 of 19 / 42 of 65; 18 of 19 / 50 of 65 | 3.0, 5.0 s; 3.6, 5.0 s | $1.18; $0.16 |

No rule was fitted to a particular line. The examples in the prompt (Frost Bow, Alex, Sam) are not from the playtest. The OpenRouter spend was $2.27 in all, against the raised cap of $2.70.

**The prompt's key rules:**
- **A follow-up** is anything about a thing a task made: change it, fix it, add to it, give it back, undo, remove or turn it off. It goes to that task however big the change, whether the task is open, done or failed.
- **New** is a thing no task made, even one like an earlier thing.
- **Find the task by what the thing is**, using the descriptions and the conversation's `AI task` and `AI result` rows. Never pick a task because it came last.
- **One thing, one task.** Never give a change to a different task that only made something similar. On a tie, take the task made first.
- **"Existing", "fix" or "again"** in the brain's instruction mark a follow-up. The brain does not name tasks.
- **`AI action` rows are not tasks.** Changing what only an action made is new work.
- **Edit and stop** apply only to a copy shown under RUNNING NOW.
- **A name** is the thing, then whose: `frost_bow_alex`. Never the bare name of a library action.
- **The description** says what the thing is now, for whom, and how a player uses it. Each follow-up rewrites it.
- **The brief** is one to three sentences. A follow-up's brief says only what to change.
- **The reply** follows the brain's chat rules and covers the whole line.

## How the replay works, and what it cannot show

- **The conversation** is built as `playtest.py` builds it and cached at checkpoints as the brain's is. The conversation's own tasks appear under the names the replay gave them: `AI task: <name>: new: …`, `AI result: <name> done: …`. A T id that no orchestrator call took shows as `AI action: …`. T ids in the brain's instructions were taken out, so the brain names no task.
- **Attribution:** after each line, its conversation tasks are given to the names its parts answered. A line where the brain gave one part for two of the conversation's tasks puts both into one task, whatever the orchestrator does (#513).
- **Not known on 2026-10-08:** RUNNING NOW, and what each task made. A task's status comes from the playtest's reports. Tasks the conversation never made have status "unknown".
- **Not shown here:** the shipped orchestrator would see the copies, so edits and stops could be measured there. The live test on realmc remains: a name on the script, the action and the copy; a follow-up by name resumes the same implementer; a stop by name.

## Run it

```sh
(cd brain && uv sync --frozen --inexact)
PY=brain/.venv/bin/python; DB=DIR/vfg/vfg.db; LINES=FULL/lines.jsonl   # lines.py's full run
$PY integration/replay/taskcall.py --db $DB --lines $LINES --out OUT-G --model google/gemini-3.8-flash \
    --reasoning low --version v3 --limit 15 --budget 0.1            # a pilot: the first 15 parts
$PY integration/replay/taskcall.py --db $DB --lines $LINES --out OUT-G --model google/gemini-3.8-flash \
    --reasoning low --version v3 --budget 1.3                       # goes on to all 168
$PY integration/replay/taskcall.py --db $DB --lines $LINES --out OUT-D --model deepseek/deepseek-v4-flash \
    --reasoning off --version v3 --budget 0.3
python3 integration/replay/taskcall_score.py OUT-G OUT-D            # OUT/score.md, score.json
```
