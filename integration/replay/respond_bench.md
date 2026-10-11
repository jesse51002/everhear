# Respond's prompt, rewritten: does it promise, and does it hand up?

Prototype_Jev_Routing's _Test_ asks for this: "Respond's rewritten prompt, on the inputs D sends it from the 2026-10-08 whole asks: no reply promises an action, and every ask for something now, or for a change to what is going on, is handed up." `respond_bench.py` runs Respond's prompt on main (`old`) and each rewrite on the same 92 inputs, with the same context, on Respond's production model, plus 20 invented lines. `respond_score.py` scores them, and every reply was read by hand. These are model calls only; nothing ran in a game.

## Verdict

**Neither the old prompt nor the new one meets the spec's bar on every run.**

- DeepSeek V4 Flash with reasoning off, at its default temperature, answers the same input differently from run to run.
- Every version promised or offered something in at least one run, and missed at least one ask.

**The rewrite (v5, now `brain/prompts/respond.md`) halves the promises, and hands up most of the asks the old prompt answered.** Over three runs of each on all 92 inputs:

- **Promises and offers, read by hand: 8 against old's 16.** That is 2.7 a run against 5.3, and 1 reply in 20 against 1 in 14.
- **The held-out second half:**
  - asks handed up: 16 of 18 (89%) against 8 of 18 (44%);
  - inputs whose speaker wanted a change in the game: 19 of 21 (90%) against 8 of 21 (38%).
- **The first half, which the wording was tuned on:**
  - asks: 19 of 33 (58%) against 16 of 33 (48%);
  - wanted a change: 21 of 30 (70%) against 14 of 30 (47%).
- **The invented set, two runs of each: 36 of 40 against 30 of 40.**
  - Every rule the game would have to enforce went up: 6 of 6, against old's 2 of 6. Old once answered one with a promise ("Lava's off the hook from now on").
  - Rules and preferences about the future only were answered 8 of 10 times, against old's 6 of 10.

**What it costs: more talk goes to the brain.**

- v5 hands up 33 of 69 `talk_to_ai` inputs, against old's 6, and 44 of 156 `noise` inputs, against 16.
- In a session like 2026-10-08's, that is about 22 more brain calls (37 hand-ups a run against 15), about $0.19 at $0.0085 a call.
- Each of those lines takes the brain's time on top of Respond's.
- The spec accepts this: "A wrong hand-up costs about a second; a missed ask costs the player their ask."

**`reason` is now required** (`brain/brain/respond.py`):

- Left optional, the model sometimes skipped it, wrote `hand_up` first and decided with no reason at all: 16 of 88 answers in two runs of v3's prompt, and 2 of 44 of v1's.
- Every one of those decisions was made without the deciding-first step that gate_bench.md measured.
- Once `reason` was required, it was written first in all 460 answers.

**What remains, plainly:**

- **Offers are most of the promises:** 5 of v5's 8 are offers ("want me to …?", "if you want"), and 3 are promises ("I'll make it happen").
- **By my reading, v5 failed to hand up a clear ask for something now 6 times in its 3 runs, on 5 inputs; old 17 times.** In each case it read the ask as a question it could answer or as unclear speech, and did not hand it up.
- A prompt alone does not get a no-reasoning model to zero. Under _What is wrong or risky_, a guard in code is the next step, for Jesse to decide.

## The promises, read by hand

**A promise** is a reply that says the AI will do, make, change, fix, watch or protect something, or that something is coming, when no open task shows it under way. **An offer** proposes to do something if the player wants ("want me to …?", "say the word"). Both are counted.

**Not counted:**

- **Status of work the prompt shows under way.** An open task with "an implementer is on it": "Castle's still rising, give it a minute", "Queen should be showing up any second now". The brain says the same for pressing lines (brain.md, FOLLOW-UPS).
- **The AI's past actions the conversation shows.**
- **Acknowledging a rule for later.** "Got it, Sam. No more guns for Jordan today."
- **Borderline, listed but not counted.** The answer to the AI's consent question (FF1V1Z), in every version: "I won't let anyone hurt you without a real yes", "your life is safe from Eclipszr". Also old's "There you go, boss man" (NXVEWZ), v4's "I've got the plans for it" (V5TWND), and v5's "maybe i keep the sky warm for you" (RQSSB7).

| Version | First half, each run | Second half, each run | All runs | Replies said | Promises a run |
|---|---|---|---|---|---|
| old | 0, 4, 1 | 1, 6, 4 | **16** | 230 | 5.3 |
| v1 | 5 | - | 5 | 29 | (one half) |
| v2 | 2 | - | 2 | 32 | (one half) |
| v3 | 1, 1 | - | 2 | 58 | (one half) |
| v4 | 1, 0 | 3, 4 | **8** | 116 | 4.0 |
| v5 | 1, 0, 2 | 4, 1, 0 | **8** | 164 | 2.7 |

**v5, every one:**

- `06:28:56` TFJFMK, "How … Is it possible to increase the lightning's damage?" → "Lightning damage is fixed by Minecraft, but I can hook extra damage onto the strike if you want—just say the word." This is also a missed ask.
- `06:24:09` 7DZGBF, "So what happened to the castle?…" → "Glad you like it — the obsidian one's got your name on it, right?" There is no obsidian castle in the conversation.
- `06:42:55` 9W9794, "…why did the dragon die instantly?…" → "Next dragon I'll keep alive til the music drops."
- `07:09:17` 4W6A9A, "That's not what I said. You can you could…" → "tell me what you actually meant and I'll make it happen."
- `07:11:34` 8JXEP2, "Why is there massive box over there?" → "want me to pass it along if it's in the way?"
- `07:16:30` E1PFWX, "I don't know if it breaks blocks yet." → "Want it to blast craters too?"
- `07:27:56` 9BE9TH, "Can make music for each of the attacks? Is that possible?" → "Want a theme for one favorite attack first?" This is also a missed ask.
- `07:06:45` KEFT7T, "why things step on your eyes to go invisible?" → "I can sort out who's invisible if you want — just point at them for me."

**v4:**

- 7DZGBF, "The big obsidian one can happen whenever you want it."
- 4W6A9A, "Just tell me straight and I'll fix it."
- AEVA7W, "Want your own copy?" The brain had just said "Want your own?" to another player: this is the copying v5's rule names.
- DJA4ZC, "Say the word if you want that changed."
- 6PNV2F, "Want me to beef 'em up next time?"
- 9KKTDT, "Want one made for you?"
- 71TJRG, "Want me to pull you to Krown?"
- RQSSB7, "If you want safety while you build, say the word."

**old:**

- EEEHXQ, "Tell me what you want and I'll see it's handled."
- BPDCZ5, "Give it a sec, Styrka — building takes a minute." Nothing was being built.
- 7DZGBF, "that obsidian upgrade has your name on it."
- 9W9794, "Stick close, I'll watch your back."
- 1759JG, "Toss a lead and I'll point you to 'em if you want."
- 9BE9TH, three times: "want me to give each one its own theme? I can set that up next." / "Yeah, I can add theme music to the big attacks. Want distinct tracks for each?" / "want it epic or goofy?"
- 6YC3SA, "Want me to clear them again or fix the time?"
- 4W6A9A, "Just tell me what you meant and I'll sort it."
- 71TJRG, "say the word and I'll ping you on a map."
- K2KVTB, "I'll announce the second it's ready."
- 5DD0BM, "Want it stopped?"
- 6PNV2F, "Want the stakes raised?"
- QE65VC, "if it's gone, I can toss one back."
- RGPW3X, "want me to make you go Super Saiyan Blue?"
- On the invented set: i18, "Rules are rules. Lava's off the hook from now on."

**v1 to v3, on the first half:**

- v1:
  - "I'll keep it spooky but not cruel."
  - "I'll call out if something big spawns."
  - "Happy to spawn something though — a house, a meteor, your call."
  - "Want it tried?"
  - "You good with it, or want extras?"
- v2: "Want me to keep it out of your ears?" and "Wanna summon another? I can redo it."
- v3: "I might be able to hook something, but I won't promise anything until I see the exact ask" and "Just say the word when you want me in".

**Claims of something done that was not.** These are not promises and are not counted. Fabricated details, such as coordinates and causes, are common in every version, and I did not check each against the conversation; only the suspicious ones:

- v4: "Fixed — it now only hits what's in the beam line" (Q28XHW). The fix task was not yet open.
- old: "I turned them off, but the ones left are still roaming" (PT0C2B). Nothing turned the horde off.

## The asks it did not hand up

**The 17 asks and the 17 inputs that wanted a change**, 20 inputs in all, with how often each version handed each up over its runs (old and v5 three runs, v4 two):

| Input | Half | Label | Wanted a change | Text | old | v4 | v5 |
|---|---|---|---|---|---|---|---|
| `05:26:39` PF8NPP | first | request | no | Why was the same server? Oh, can you hear me, Claude? If you can hear me, say, put something in the chat. | 2 of 3 | 0 of 2 | 1 of 3 |
| `06:07:06` FF1V1Z | first | answer_to_ai | yes | Yes. … I just say yes to? … No, Claude, I was not saying yes to Eclipse are killing me. | 0 of 3 | 0 of 2 | 2 of 3 |
| `06:09:05` M82VE3 | first | request | no | Hey, Claude, show me the copyright claims that are given to my private identity when I'm being recorded without permission. | 0 of 3 | 0 of 2 | 0 of 3 |
| `06:14:04` 7R3N3G | first | request | yes | Can you teach me about both of us to Stirka? Also Matthew … are you recording? … | 2 of 3 | 1 of 2 | 1 of 3 |
| `06:18:09` 3JDWFZ | first | request | yes | I think I need to make the orchestrator do things on his own. … Is this your spooky shit? What the I heard something. … | 0 of 3 | 0 of 2 | 1 of 3 |
| `06:18:37` EAAZN4 | first | request | yes | … Hey Claude, can you add skinwalkers like? Copies of us … | 3 of 3 | 2 of 2 | 3 of 3 |
| `06:28:56` TFJFMK | first | request | yes | How … Is it possible to increase the lightning's damage? Mm, I guess. How | 2 of 3 | 2 of 2 | 2 of 3 |
| `06:29:26` DZXAKX | first | request | no | Princess open at a castle gate. Okay, can I go check out? | 0 of 3 | 0 of 2 | 0 of 3 |
| `06:35:11` J2E6XG | first | talk_to_ai | yes | Hey, it's killing everything you look at. Bro, who's setting me on fire? | 0 of 3 | 0 of 2 | 1 of 3 |
| `06:37:03` TDNF0P | first | request | yes | … Claude, where's my eye being? Where's my dead being, Claude? … | 3 of 3 | 2 of 2 | 3 of 3 |
| `06:42:03` V2GKHD | first | request | yes | Oh God, can I clear all the mouse please? … | 2 of 3 | 2 of 2 | 3 of 3 |
| `06:47:20` 3SAQM8 | first | request | yes | The fancy isn't tied Where's my black hole club? He said you're giving me a black hole. | 2 of 3 | 2 of 2 | 3 of 3 |
| `06:47:41` HEX1GJ | first | noise | yes | Also why is it not this barely doing the | 0 of 3 | 2 of 2 | 2 of 3 |
| `06:57:53` PT0C2B | second | talk_to_ai | yes | Why is there twenty million creeper or things? | 0 of 3 | 0 of 2 | 3 of 3 |
| `07:00:34` QE65VC | second | request | yes | Also, where is my ice bag? I wore my eyes dog bag. | 0 of 3 | 2 of 2 | 3 of 3 |
| `07:20:46` RGPW3X | second | request | yes | You know where a super sent is? Can I make it a super sent? … Super Sane. Cyan, super cyan? | 1 of 3 | 2 of 2 | 2 of 3 |
| `07:27:56` 9BE9TH | second | request | yes | Can make music for each of the attacks? Is that possible? | 0 of 3 | 2 of 2 | 2 of 3 |
| `07:32:45` WNRHFY | second | request | yes | … drop a nuke in ten minutes once you have the countdown in the chat … | 3 of 3 | 2 of 2 | 3 of 3 |
| `07:39:50` Q28XHW | second | request | yes | … That claw made Gallic gun destroy everything in the map. … Did you fix Gallic gun killing everyone …? | 3 of 3 | 1 of 2 | 3 of 3 |
| `07:40:37` P6DD35 | second | request | yes | Where's my spirit bomb bus? … It broke. The spare bot is broken. … | 1 of 3 | 1 of 2 | 3 of 3 |

**v5's misses, by my reading, on what the spec asks.**

**Missed: an ask for something now, or for a change.** 6 answers:

- TFJFMK, once: replied with an offer.
- 9BE9TH, once: replied with an offer.
- RGPW3X, once: "Say that again, Eclipszr — didn't catch what you want to make super." It asked back instead of handing up while unsure.
- 7R3N3G, twice: "teach me about both of us to Stirka" is most likely "teleport both of us to Styrka". Its reasons call it unintelligible, yet it answered the recording part.
- HEX1GJ, once: a complaint cut off mid-word. It asked for the rest.

**Not an ask for something now, though the label says so.**

- **PF8NPP**: the ask is for a chat line, which the reply is.
- **M82VE3**: a real-world request, declined.
- **FF1V1Z**: a "no" to the AI's consent question, so nothing is to happen. The new prompt hands answers to the AI up, and v5 did so twice.
- **3JDWFZ**: a question whether the spooky sounds are the AI's. The hindsight `change` answer is at 0.32.

**Unclear.** v5 answered both every time, as old did.

- **DZXAKX**: "Princess open at a castle gate" may be "please open the castle gate", or a player asking to go and look.
- **J2E6XG**: "who's setting me on fire?" is a question only the live game can answer, which the prompt says to hand up.

**Old's misses, by the same reading: 17 answers.**

- TFJFMK once, 9BE9TH three times, RGPW3X twice, 7R3N3G once, HEX1GJ three times.
- QE65VC three times: an item the AI gave, gone.
- P6DD35 twice: "It broke".
- V2GKHD once, 3SAQM8 once.
- Also DZXAKX, J2E6XG and PT0C2B every time.

## The prompt, version by version

**Every rule is the spec's, or was forced by a bench result**:

| Version | What changed | Why | First half: asks, wanted a change (each run) | Promises (first half, each run) | Invented, right of 20 |
|---|---|---|---|---|---|
| old | main's `respond.md` (9f32e06) | | 6, 5, 5 of 11; 6, 4, 4 of 10 | 0, 4, 1 | 14, 16 |
| v1 | The spec's five points, its examples block, and the group sentence ("A player's line can hold several pieces…"). Kept: the persona, one line, no markdown, never mention the brain or Jev, standing rules, the owner, a game among friends, and the old hand-up rules for complaints, earlier work and questions only the live game answers | the spec, _Respond's prompt_; the shared plan | 6 of 11; 7 of 10 | 5 | 18 |
| v2 | "Never promise **or offer** anything: you only talk, and you neither change nor watch the game", with "I'll keep an eye out" and "want me to?" beside the spec's three; "Acknowledging a rule or a preference for later is not a promise" | v1's first-half replies: "I'll keep it spooky…", "I'll call out if something big spawns", "Happy to spawn something…", "Want it tried?" | 5 of 11; 4 of 10 | 2 | 18 |
| v3 | "A line you could only answer with a promise or an offer goes up."; an ask "as a question whether it can be done"; "An answer to a question the AI asked, such as yes, no or a choice: the brain acts on it" as its own rule | v2's offer on 3JDWFZ and its answer to TFJFMK as knowledge; the spec's _Whole asks_: D sends Respond "asks put as questions, and answers to the AI" | 5, 7 of 11; 5, 7 of 10 | 1, 1 | 18 |
| v4 | v3's prompt; `reason` required in the schema | v1 skipped `reason` in 2 of 44 answers and v3 in 16 of 88, deciding with no reason | 5, 6 of 11; 6, 7 of 10 | 1, 0 | 18, 19 |
| **v5** | "The AI's lines in the conversation promise and offer because the brain can act; you cannot, so never copy them", with "whenever you want it" added to the list; "Say that something is done, fixed or on its way only when the conversation or OPEN TASKS shows it" | v4's offers copy the brain's own lines (AEVA7W: "Want your own?" → "Want your own copy?"), and its first-half "The big obsidian one can happen whenever you want it"; old's "building takes a minute" with nothing building, and v4's false "Fixed —" | 6, 7, 6 of 11; 5, 8, 8 of 10 | 1, 0, 2 | 19, 17 |

**Two caveats on how the wording was set:**

- **v5's rule was written after reading v4's second-half rows**, so the second half is held out for v4, not fully for v5. The same pattern is in the first half: the brain's own "…want that?" at 06:18:37, and v4's "whenever you want it". The rule names a cause, not an input.
- **The invented set was written before the first run and never changed.** No rule was added for an invented line. Every version answers i12 ("ugh I'm starving and I've got nothing to eat"), a hint the spec's points do not name, and it was left so.

`brain/prompts/respond.md` is `respond_prompts/v5.md`, byte for byte. `brain.respond.Response` has v5's schema: the bench checks it builds the same JSON schema.

## Results

### The playtest inputs, three runs pooled

**The inputs:**

- 92 inputs: 16 request, 1 answer_to_ai, 23 talk_to_ai, 0 context, 52 noise; 10 merged.
- First half: 44 inputs, 11 asks, 10 that wanted a change. Second half: 48 inputs, 6 asks, 7 that wanted a change.
- *Wanted a change*: the hindsight labels' `change` member says the speaker wanted something changed in the game (`yes_and_done` or `yes_not_done`, labels_build.py), whatever the label. It is the nearest the labels come to the spec's "an ask for something now, or for a change to what is going on".

| Version | Half | Runs | request | answer_to_ai | talk_to_ai | noise | **Asks** | **Wanted a change** | Replies said | `reason` first | Latency median, p90 | Cost |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| old | first | 3 | 16 of 30 | 0 of 3 | 4 of 33 | 11 of 66 | **16 of 33 (48%)** | **14 of 30 (47%)** | 101 | 132 of 132 | 1.38 s, 2.19 s | $0.158 |
| old | second | 3 | 8 of 18 | - | 2 of 36 | 5 of 90 | **8 of 18 (44%)** | **8 of 21 (38%)** | 129 | 144 of 144 | 1.77 s, 2.97 s | $0.398 |
| v4 | first | 2 | 11 of 20 | 0 of 2 | 6 of 22 | 17 of 44 | **11 of 22 (50%)** | **13 of 20 (65%)** | 54 | 88 of 88 | 1.56 s, 2.54 s | $0.100 |
| v4 | second | 2 | 10 of 12 | - | 10 of 24 | 14 of 60 | **10 of 12 (83%)** | **10 of 14 (71%)** | 62 | 96 of 96 | 1.55 s, 2.16 s | $0.218 |
| **v5** | first | 3 | 17 of 30 | 2 of 3 | 12 of 33 | 24 of 66 | **19 of 33 (58%)** | **21 of 30 (70%)** | 77 | 132 of 132 | 1.42 s, 2.57 s | $0.152 |
| **v5** | second | 3 | 16 of 18 | - | 21 of 36 | 20 of 90 | **16 of 18 (89%)** | **19 of 21 (90%)** | 87 | 144 of 144 | 1.78 s, 2.94 s | $0.374 |

**Each run on its own:**

| Version | First half: asks / wanted a change | Second half: asks / wanted a change |
|---|---|---|
| old | 6/6, 5/4, 5/4 | 3/3, 2/2, 3/3 |
| v4 | 5/6, 6/7 | 4/4, 6/6 |
| v5 | 6/5, 7/8, 6/8 | 5/6, 6/7, 5/6 |

**About the table:**

- No call failed.
- No answer carried a reply beside a hand-up.
- Every answer had a reply or a hand-up, none silent.
- The second half's prompts are longer (the conversation grows to 47K tokens), hence the latency and the cost.

### The invented set, line by line

**The invented set:**

- 20 invented lines, none from the playtest and none one of the prompts' own examples, each with a short conversation before it.
- The world they were said in: a running copy or an effect where the line is about one, night for the one about morning, an open task for the build under way.
- Speakers Sam (the owner), Riley and Jordan.
- Old, v4 and v5 ran twice each.

| Line | What it tests | Wants | Said | old | v4 | v5 |
|---|---|---|---|---|---|---|
| i01 | future rule | answer | Riley: “AI can you call me Captain Riley from now on” | 1 of 2 | 2 of 2 | 2 of 2: “Aye aye, Captain Riley.” |
| i02 | future rule | answer | Sam: “don't make any more weapons for Jordan today, he's had enough” | 0 of 2 | 1 of 2 | 1 of 2 |
| i03 | future rule | answer | Jordan: “keep your messages shorter from now on, they cover half my screen” | 1 of 2 | 2 of 2 | 2 of 2: “Got it, short and sweet.” |
| i04 | future rule | answer | Sam: “and don't build anything near spawn unless I say it's ok” | 2 of 2 | 2 of 2 | 1 of 2 |
| i05 | future rule | answer | Jordan: “next time I ask for a sword make it netherite, diamond is boring” | 2 of 2 | 2 of 2 | 2 of 2: “Netherite next time, got it.” |
| i06 | knowledge | answer | Jordan: “how do you make a smithing table again” | 2 of 2 | 2 of 2 | 2 of 2 |
| i07 | knowledge | answer | Riley: “AI do villagers need beds to breed” | 2 of 2 | 2 of 2 | 2 of 2 |
| i08 | knowledge | answer | Sam: “what's better on an axe, sharpness or smite” | 2 of 2 | 2 of 2 | 2 of 2 |
| i09 | banter | answer | Jordan: “AI you're honestly the best teammate on this server” | 2 of 2 | 2 of 2 | 2 of 2 |
| i10 | banter | answer | Riley: “good morning AI, did you sleep well” | 2 of 2 | 2 of 2 | 2 of 2 |
| i11 | now | up | Jordan: “could I get like a stack of torches, it's so dark down here” | 2 of 2 | 2 of 2 | 2 of 2 |
| i12 | now | up | Riley: “ugh I'm starving and I've got nothing to eat” | 0 of 2 | 0 of 2 | 0 of 2: “grab some berries and head north, or ask Sam to share” |
| i13 | now | up | Sam: “can you skip to morning, there are way too many mobs out” | 2 of 2 | 2 of 2 | 2 of 2 |
| i14 | change | up | Riley: “the ice thing is wrecking my farm, all my crops are ice now” (ice_walk running) | 2 of 2 | 2 of 2 | 2 of 2 |
| i15 | change | up | Riley: “this speed is way too much, I keep running off cliffs” (keep_effects running) | 2 of 2 | 2 of 2 | 2 of 2 |
| i16 | change | up | Sam: “oh and for the castle, put a moat around it” (castle task open) | 2 of 2 | 2 of 2 | 2 of 2 |
| i17 | change | up | Jordan: “the iron golem you gave me … he keeps wandering off, make him stay with me” | 2 of 2 | 2 of 2 | 2 of 2 |
| i18 | game rule | up | Sam: “from now on nobody loses their stuff when they die” | 1 of 2 (“Lava's off the hook from now on.”) | 2 of 2 | 2 of 2 |
| i19 | game rule | up | Jordan: “no more phantoms for the rest of the game please” | 1 of 2 | 2 of 2 | 2 of 2 |
| i20 | game rule | up | Riley: “every time someone gets blown up by a creeper, hit them with lightning lol” | 0 of 2 | 2 of 2 | 2 of 2 |
| | | | **Right** | **30 of 40** | **37 of 40** | **36 of 40** |

**By point**, over two runs:

| Point | old | v4 | v5 |
|---|---|---|---|
| Future rule or preference, answered | 6 of 10 | 9 of 10 | 8 of 10 |
| Knowledge, answered | 6 of 6 | 6 of 6 | 6 of 6 |
| Banter, answered | 4 of 4 | 4 of 4 | 4 of 4 |
| Something now, handed up | 4 of 6 | 4 of 6 | 4 of 6 |
| A change to what is going on, handed up | 8 of 8 | 8 of 8 | 8 of 8 |
| A rule the game would enforce, handed up | 2 of 6 | 6 of 6 | 6 of 6 |

The future rules v5 handed up (i02 and i04, once each) cost a brain call, not an ask. Every acknowledgement it wrote is a few words in the rule's own terms.

## How it was run

**The inputs** (`respond_bench.py`, `playtest`):

- Every whole-ask input (`whole_asks_2026-10-08.jsonl`) that D2 with Jev's new state sends to Respond, walked with `trees_questions.route_d(…, "D2")` from the recorded answers (`whole_asks_bench.py run new D2`): 92 of 770.
- A merged input is its lines joined with " … ", said at its last line's time, as the gather call would feed it.

**The context** is the brain's at the input's moment, built as `gate_bench.py` builds it:

- `playtest.Clock`: every line to the brain or Respond is a live read, and the 60 s batch timer takes in the rest.
- The playtest's AI chat, tasks and results; who is online; the open tasks.
- It differs from the gate bench in one way: the classes are D2's own outcomes, not Jev today's.
  - Every input D2 keeps is one LINE row: `action_oriented` (brain), `conversational` (Respond) or `context` (shown to no model).
  - Its text is the input's, fed when Jev judged its last line.
  - A dropped input is not in it, and a failed D2 call is `context`, as the filter fails open.
  - So the conversation is the one D2 would have built, merged asks included.
- The old prompt and every rewrite saw exactly the same context: all 92 dry prompts of old and v5 match byte for byte from the menu on.

**The model** is Respond's production model, as `brain/brain/config.py` sets it:

- `~deepseek/deepseek-v4-flash-latest`, reasoning off, providers parasail, wafer, phala, together, venice (fastest first), 20 s timeout, one call at a time.
- The owner is the PC's `VFG_OWNER` (KrownJM) for the playtest, and Sam for the invented set.

**Caveats:**

- **The bar is a hand judgement, mine alone.** The promise definition is above, and every reply a player would see is listed run by run in the scorer's `score.md` for re-reading. The ⚑ mark there is only a word pointer.
- **The labels are hindsight.** "Wanted a change" is one member answer of three, some at low confidence (3JDWFZ 0.32, FF1V1Z 0.19). Asks by label count PF8NPP (a chat ask) and M82VE3 (a real-world ask), which no Respond should hand up.
- **Run-to-run noise is large.** Promises a run range 0–6 for old and 0–4 for v5. Asks handed up move by 1–2 of 11 between runs of one version. Hence three runs of old and v5. v1 and v2 ran once, and v3 and v4 twice, on the first half only.
- **The context is a replay, not the session.**
  - The AI's rows are 2026-10-08's conversation agent and orchestrator, whose own lines promise and offer freely ("on it — …", "…want that?"). The brain's do too (brain.md, THE REPLY).
  - Old against Respond B's recorded gate-bench run (nearly the same prompt, context from Jev today's classes) agrees on 82–84% of the 62 single-line inputs both ran. That is how much the context and the run alone move a hand-up.
- **The gather call itself is not run here.** The merged inputs stand in for its groups. The group sentence in `brain.md` is not benched; the `live` test covers it.
- **Status of work under way is not counted as a promise.** "Give it a minute" about an open task is how the brain answers pressing too. A stricter reading would count them; every version writes them.

**`brain.md`'s "Ask before you build"** was checked against the spec's _Also decided_ and left as it is. It already says to never guess on an unclear ask, to answer that part as talk with one short question while nothing runs, when to ask (garbled, two meanings, who, what or where missing), and why it matters.

## What is wrong or risky

- **The spec's bar is not met.** A prompt alone will not get DeepSeek with reasoning off to zero promises.
  - The next step is a guard in code, not built here.
  - A reply that matches an offer or a promise ("want me to", "I'll", "say the word", "if you want", "on it", "coming up") would be handed up instead of said.
  - It is deterministic, and a false match costs one brain call.
  - It is a design change beyond the spec's prompt rewrite, so it is Jesse's call.
- **More talk goes to the brain**, about 22 calls and $0.19 a session. A `talk_to_ai` line Respond hands up gets the brain's slower answer.
- **Answers to the AI go up, "no" included.** The brain asked the question, so this is its call to make. It costs a brain call where a "got it" would do.
- **The model makes up game facts in every version:** coordinates, causes, an "obsidian castle". This is outside the spec's bar. The rule "say that something is done, fixed or on its way only when the conversation or OPEN TASKS shows it" covers only part of it.
- **`reason` is now required.**
  - An answer without one fails validation: `structured()` asks once more, and a second failure is a hand-up (the loop's `LlmError` path).
  - None happened in 460 calls.
- **Three runs are few for numbers this noisy.** A fourth run would move each count by a few.

## Cost and time

**OpenRouter: $1.71 of the $2 budget**, every call's own `usage.cost`, summed from the rows.

| What | Calls | Cost |
|---|---|---|
| old: three runs of all 92, two of the invented set | 316 | $0.576 |
| v1, v2: the first half and the invented set, once each | 128 | $0.134 |
| v3: the first half twice, the invented set once | 108 | $0.113 |
| v4: two runs of all 92, two of the invented set | 224 | $0.340 |
| v5: three runs of all 92, two of the invented set | 316 | $0.545 |
| a 4-line pilot and one row of a run stopped to save budget | 5 | $0.003 |

**Time:**

- A first-half run takes about 1 minute (1.3–1.5 s a call) and a second-half run about 1.5–2 minutes. A call costs $0.0002–0.0066 as the conversation grows.
- The prompts grow from 3.5K to 47K tokens; 76% of all prompt tokens were cached.

## Run it

```sh
(cd brain && uv sync --frozen --inexact)
PY=brain/.venv/bin/python; L=integration/replay
DB=DIR/vfg/vfg.db                     # the playtest's database (README.md)
D2=WHOLE_OUT/new/D2/rows.jsonl        # whole_asks_bench.py run new D2: D2's recorded answers
# the OpenRouter key as structured() reads it: OPEN_ROUTER_API in the environment
$PY $L/respond_bench.py run old playtest --db $DB --d2 $D2 --out OUT/runs --limit 4      # a pilot
for RUN in runs repeat third; do                                                          # three runs
  for V in old v5; do
    $PY $L/respond_bench.py run $V playtest --db $DB --d2 $D2 --out OUT/$RUN --half first
    $PY $L/respond_bench.py run $V playtest --db $DB --d2 $D2 --out OUT/$RUN --half second
  done
done
for V in old v4 v5; do for RUN in runs repeat; do $PY $L/respond_bench.py run $V invented --out OUT/$RUN; done; done
$PY $L/respond_bench.py run v5 playtest --db $DB --d2 $D2 --out OUT/dry --dry             # the prompts, no calls
python3 $L/respond_score.py OUT/runs OUT/repeat OUT/third --gate-b GATE_OUT/B/rows.jsonl --to OUT/score.md
```

**The runs behind this page:**

- old, v4 and v5 as in the loop above, with v4 on two runs (`runs`, `repeat`), not three.
- v1, v2 and v3 on the first half in `runs`, and v3 again in `repeat`.
- The invented set: once for v1 to v3, twice for old, v4 and v5.

**Where the data and the key live:**

- The data was read from `$SP/data/` (the scratchpad copies of the playtest database, D2's whole-ask rows, the gate bench's B rows), and the rows are in `$SP/respond/{runs,repeat,third}/`.
- `--budget` (default $0.60) stops a run past that much in its file.
- The key is never printed or written.
