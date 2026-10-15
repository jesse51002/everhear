# Respond's prompt, rewritten: does it promise, and does it hand up?

Prototype_Jev_Routing's _Test_ asks for this: "Respond's rewritten prompt, on the inputs D sends it from the 2026-10-08 whole asks: no reply promises an action, and every ask for something now, or for a change to what is going on, is handed up." `respond_bench.py` runs Respond's prompt on main (`old`) and each rewrite on the same 92 inputs, with the same context, on Respond's production model, plus 20 invented lines. `respond_score.py` scores them, and every reply was read by hand. These are model calls only; nothing ran in a game.

**The conversation agent of Prototype_Conversation_And_Router, on DeepSeek V4.1 Flash, was benched on 2026-10-13, before and after four fix rounds: its results are the last section, _The conversation agent on DeepSeek V4.1 Flash_.** Everything before it is Prototype_Jev_Routing's bench of Respond on V4 Flash.

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

**A guard in code now hands up any reply that promises or offers** (`brain.respond.promises()`, under _The guard_):

- Measured on every reply the runs recorded, with no new call.
- It catches 41 of the 42 promises and offers judged by hand across all versions, and all 21 of a reviewer's probe promises.
- It fires on 11 of the 680 replies I had judged fine. 7 are status lines about another player's task, which by design pass only for the speaker's own.
- v5 with the guard keeps none of its 8.
- It hands up 2 more asks: 20 of 33 and 17 of 18.
- Its other hand-ups are about 3 brain calls a session.

**`reason` is now required** (`brain/brain/respond.py`):

- Left optional, the model sometimes skipped it, wrote `hand_up` first and decided with no reason at all: 16 of 88 answers in two runs of v3's prompt, and 2 of 44 of v1's.
- Every one of those decisions was made without the deciding-first step that gate_bench.md measured.
- Once `reason` was required, it was written first in all 540 answers: 460 on the playtest inputs, 80 on the invented set.

**What remains, plainly:**

- **Offers are most of the promises:** 5 of v5's 8 are offers ("want me to …?", "if you want"), and 3 are promises ("I'll make it happen").
- **By my reading, v5 failed to hand up a clear ask for something now 6 times in its 3 runs, on 5 inputs; old 17 times.** In each case it read the ask as a question it could answer or as unclear speech, and did not hand it up.
- A prompt alone does not get a no-reasoning model to zero, hence the guard.

## The guard

**`brain.respond.promises(reply, tasks, who)` runs on every reply Respond means to say.**

- When the reply offers or promises something in the game, the answer becomes a hand-up: the reply is dropped, and `reason` gains ` · handed up in code: an offer` (or `a promise`). The brain's "handed this line up to you" shows that note.
- The log gets one line with the blocked reply, `respond guard #<seq> a promise: "Coming right up!"`. It goes to the log only, never into a prompt.
- This is the spec's point 1, "Respond never promises what it can't do itself", in code, as the brain writes its own line when an action fails.

**It is tuned for catching a promise over never handing up a fine reply.**

- A missed promise tells a player something will happen that never does.
- A wrong hand-up costs about a second and a brain call, and the player still gets one reply.
- So a rule acknowledged with an action verb ("Sure, I'll turn the chatter down") goes up too, and the brain acknowledges it.

**The patterns**, on the reply lower-cased with curly apostrophes made straight. "The AI does" is a list of verb stems in any form ("make", "makes", "making", "makin'"): make, give, build, spawn, summon, set, fix, turn, get, put, add, bring, pull, teleport, tp, clear, swap, change, upgrade, boost, beef, buff, toss, drop, hook, sort, redo, layer, handle, do, craft, stop, remove, kill, whip, start, move, send, grab, enchant, repair, freeze, delete, destroy, undo, cancel, rebuild, reset, restore, keep, heal, protect, shrink, double, spin, cook, conjure, unleash. The only exception is the idiom "I'll give you that." ending a sentence.

- **An offer, always:**
  - "want me to", but not "don't want me to";
  - "shall I", "should I" + a verb, "say the word";
  - "want it / one / them / that / the / a / some / more / another / extras / your own … ?" as the reply's **last** sentence. An offer waits for the player's yes, so "Want the recipe? Three glass, five obsidian and a nether star." is a knowledge answer and passes: the reply answers its own question;
  - "I can" + a verb; "I could" + a verb + an object ("I could make you one", not "I could get used to this");
  - "I might / may / could be able to" + a verb; "happy to" + a verb;
  - "let me" + a verb, but not "let me put it this way", "let me get this straight", "let me turn that around";
  - "has / got your name on it", "whenever you want it".
- **A promise, always:**
  - "I'll / I will / I shall / I'm gonna / I'm going to / I am going to / imma" (+ just, also, go, be, now, then…) + a verb, "take care of", "see it", "call out", "point you", "ping you", "announce";
  - "gonna" + a verb opening a clause;
  - "I'm / we're" (+ just, now, already, busy) + a verb's -ing form;
  - a clause opening with a verb's -ing form that says "now" ("Okay, making it rain now!"), but not "from now on";
  - "on it" opening the reply or a clause, or after "I'm", "yep", "sure", "aye", "got it", "you got it", "no problem"… ("Aye, on it, captain!"), but not "put a sign on it";
  - "will do", "consider it done", "leave it to me", "done" opening a clause, "right away";
  - "I'll / I've got your back", "I'll watch your back", but not the advice "watch your back for ghasts";
  - "give me a sec / second / minute / moment".
- **A promise unless the line's speaker has a task open:**
  - the phrases: "coming (right) up", "coming right at ya", "give it / her / him / them a sec / minute / moment / beat", "hang tight", "one sec", "on its / their way", "'s / is / are on the way" (not "on the way back"), "it's coming", "should / will be done / ready in / soon";
  - an open task is a line of the tasks text in `Tasks.show()`'s shape (`OPEN_TASK`), and only one for the speaker counts;
  - "Night's coming up", "Hang on, you lost everything?" and "The sun will be up soon" are not among these phrases.

**On the 722 replies the runs recorded** (all versions, all runs, the invented set; `respond_bench.py guard`):

- **Caught: 41 of the 42 promises and offers judged by hand:**
  - all 17 of old's on the playtest;
  - all 5 of v1's, both of v2's, both of v3's, all 8 of v4's and all 8 of v5's.
- **Missed: 1.** Old's invented "Rules are rules. Lava's off the hook from now on.": no first person, no offer, no phrase.
- **Fires on 11 of the 680 replies I had judged fine.** 7 are status lines, now handed up because the open task was another player's. 4 are first-person banter:
  - "The castle's coming up over east, give it a minute. And congrats on the seer gig…" (old, XNZSZH): Eclipszr asked; the castle was Styrkaeopteryx's.
  - "Seer of the east, I like it. Castle's still going up, give it a sec." (v1, XNZSZH): the same.
  - "Castle's still rising, give it a minute. Seer of what, exactly?" (v4, XNZSZH): the same.
  - "A castle's being put together for Styrkaeopteryx, give it a sec. …" (v3, XNZSZH): the same.
  - "Not sure yet, still building the clash. Give it a sec." (old, 7JG3ZA): Styrkaeopteryx asked; the clash was Eclipszr's.
  - "No worries, Styrk — castle's coming, just give it a sec." (old, V5TWND): Krown's ice castle, Styrkaeopteryx speaking.
  - "Hey, it's coming along — don't be sorry." (v5, V5TWND): the same.
  - "Probably because the mountain builder was churning through blocks, but I'm on it now." (old, 4YF56H): the spec's own "on it", and Respond claims to be doing it.
  - "Krown, you're the main character. Just keep the chaos coming and I'll keep up." (v4, EEEHXQ): "I'll keep".
  - "…but you're the one I'm keeping an eye on." (old, EEEHXQ): "I'm keeping".
  - "No copyright claims are here, but I can send a very scary legal letter to whoever pressed record." (old, M82VE3): "I can send".
- **Rule acknowledgements in the recorded rows:** none fires. Every invented acknowledgement passes ("Got it, Sam. No more guns for Jordan today.", "Aye aye, Captain Riley.", "Netherite next time, got it."), and so does "Got it, keeping it short from now on".

**On the reviewer's probes** (`guard_probe.py`, 22 replies that should be said and 21 promises):

- **All 21 promises caught**, among them "Will do!", "Consider it done.", "I'm gonna make it rain!", "Okay, making it rain now!", "Aye, I'll be makin' it rain, matey!", "One storm, coming right at ya!" and "Done! Enjoy the sword.".
- **"Coming right up, Steve!", "Hang tight, it's on its way." and "Give it a sec!" now go up** when the open task is another player's. They pass when it is the speaker's.
- **14 of the 22 pass**, among them:
  - "I could get used to this pirate life", "Let me put it this way…", "Want the recipe? Three glass…";
  - "You don't want me to spoil the ending", "Watch your back for ghasts", "Hang on, you lost everything in lava?";
  - "Night's coming up", "Grab some food on the way back", "The sun will be up soon".
- **8 go up**: 7 acknowledgements with an action verb, as decided, and one joke.
  - The acknowledgements: "I'll clear it with you first…", "I'll get your okay…", "I'll set that as the rule…", "Aye, I'll keep it going, matey!", "Arr, I'll turn pirate from here on", "Sure, I'll turn the chatter down", "I'll change my style to shorter lines".
  - The joke: "I could fix you a pep talk, but you're doing great." It has the shape of a real offer, "I could <verb> you a …".

**Small talk, after the orchestrator's probe of 30 everyday replies** (2026-10-11): the first tuning handed up 11 of them, among them "I'm doing great, thanks for asking!", "Getting dark now, better find shelter.", "Done already? That was fast." and "Okay, I'll stop talking now.". So an -ing verb counts only when it is done to something ("spawning some cows now", "getting you a sword"), "done" only on its own ("Done!"), "right away" never as a question, and the AI's own talk is never a promise ("I'll keep quiet", "I'll stop talking", "I'll turn the chatter down", "I'll change my style"). Those replies are in `brain/tests/test_loop.py`'s table. On the recorded replies this changed one: "It's the withers we're clearing — hang tight…" (v5, 7EGRQ9) is said now.

**The cost to watch is persona speech.** Under a standing pirate persona, "Arr, I'll …" replies with an action verb go up and the brain answers them. The recorded session had no persona, so it does not measure this.

**v5 with the guard**, its three runs pooled, against v5 alone:

| Half | Asks handed up | Wanted a change, handed up | All hand-ups | The guard's hand-ups | Promises and offers left |
|---|---|---|---|---|---|
| first | 19 → **20 of 33** | 21 → **22 of 30** | 55 → 58 of 132 | 3 | 3 → **0** |
| second (held out) | 16 → **17 of 18** | 19 → **20 of 21** | 57 → 63 of 144 | 6 | 5 → **0** |

- **The asks now handed up** are TFJFMK ("Is it possible to increase the lightning's damage?") and 9BE9TH ("Can make music for each of the attacks?"). v5 had answered each with an offer.
- **v5 still misses 4 asks**, none with a promise.
- **The extra hand-ups:** 9 over three runs, 3 a session.
  - 8 are the hand-judged promises and offers (2 of them on those asks).
  - 1 is a status line about another player's task, which the guard now hands up by design.
  - In all, about 2.3 brain calls and $0.02 a session beyond the asks.

**Every version with the guard**, pooled over its runs (without → with):

| Version | Half | Runs | Asks | Wanted a change | All hand-ups | The guard's own | Judged promises left |
|---|---|---|---|---|---|---|---|
| old | first | 3 | 16 → 17 of 33 | 14 → 14 of 30 | 31 → 39 of 132 | 8 | 0 |
| old | second | 3 | 8 → 13 of 18 | 8 → 13 of 21 | 15 → 29 of 144 | 14 | 0 |
| v1 | first | 1 | 6 → 8 of 11 | 7 → 8 of 10 | 15 → 21 of 44 | 6 | 0 |
| v2 | first | 1 | 5 → 6 of 11 | 4 → 5 of 10 | 12 → 14 of 44 | 2 | 0 |
| v3 | first | 2 | 12 → 12 of 22 | 12 → 12 of 20 | 30 → 33 of 88 | 3 | 0 |
| v4 | first | 2 | 11 → 11 of 22 | 13 → 13 of 20 | 34 → 37 of 88 | 3 | 0 |
| v4 | second | 2 | 10 → 10 of 12 | 10 → 10 of 14 | 34 → 41 of 96 | 7 | 0 |
| v5 | first | 3 | 19 → 20 of 33 | 21 → 22 of 30 | 55 → 58 of 132 | 3 | 0 |
| v5 | second | 3 | 16 → 17 of 18 | 19 → 20 of 21 | 57 → 63 of 144 | 6 | 0 |

**What a replay of recorded answers cannot show:**

- **How the brain answers what the guard hands up.**
- **Phrasings this session never produced, persona speech above all.** The prompt still says the same thing first.

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

`brain/prompts/respond.md` is `respond_prompts/v5.md` except for one sentence, changed after the bench and not re-run.

- The group sentence now reads "…joined with " … ", the newest last: Jev joined the earlier pieces that go with it, so read them as one line".
- v5 said "the ask last … read them as one ask", which contradicted the opening's "talk, not an ask".
- `brain.respond.Response` has v5's schema: the bench checks it builds the same JSON schema.

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

- **The spec's bar is not met by the prompt alone.**
  - With the guard, no hand-judged promise is left in v5's recorded runs, but the guard's phrase list comes from one session.
  - v5 still answered 4 asks in its three runs without handing them up.
- **More talk goes to the brain**, about 22 calls and $0.19 a session. A `talk_to_ai` line Respond hands up gets the brain's slower answer.
- **Answers to the AI go up, "no" included.** The brain asked the question, so this is its call to make. It costs a brain call where a "got it" would do.
- **The model makes up game facts in every version:** coordinates, causes, an "obsidian castle". This is outside the spec's bar. The rule "say that something is done, fixed or on its way only when the conversation or OPEN TASKS shows it" covers only part of it.
- **`reason` is now required.**
  - An answer without one fails validation: `structured()` asks once more, and a second failure is a hand-up (the loop's `LlmError` path).
  - None happened in 540 calls.
- **The guard hands up persona speech and acknowledgements that use an action verb**, "Arr, I'll turn the chatter down", by decision. Under a standing persona that may be many replies a session, each a brain call instead of a cheap reply. The recorded session had no persona, so the cost is not measured.
- **The guard's status phrases pass only for the speaker's own open task**, so a player asking after a friend's build gets the brain's answer, not Respond's.
- **Three runs are few for numbers this noisy.** A fourth run would move each count by a few.

## Cost and time

**OpenRouter: $1.71 of the $2 budget**, every call's own `usage.cost`, summed from the rows. The guard was measured on the recorded rows: no call.

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
$PY $L/respond_bench.py guard OUT/runs OUT/repeat OUT/third --db $DB --d2 $D2              # the guard, no calls
```

**The runs behind this page:**

- old, v4 and v5 as in the loop above, with v4 on two runs (`runs`, `repeat`), not three.
- v1, v2 and v3 on the first half in `runs`, and v3 again in `repeat`.
- The invented set: once for v1 to v3, twice for old, v4 and v5.

**Where the data and the key live:**

- The data was read from `$SP/data/` (the scratchpad copies of the playtest database, D2's whole-ask rows, the gate bench's B rows), and the rows are in `$SP/respond/{runs,repeat,third}/`.
- `--budget` (default $0.60) stops a run past that much in its file.
- The key is never printed or written.

## The conversation agent on DeepSeek V4.1 Flash

Prototype_Conversation_And_Router's _Test_: "The conversation agent on DeepSeek V4.1 Flash, on the Respond bench and the night's lines to the AI: it hands off every line that needs something done, promises nothing not done, and answers within 3 s at p90; its hand-off rate and how often the promise check fires are set beside V4 Flash's." The agent is `front`: `brain.respond.Respond` and its `Response` as built for that spec, its prompt `brain/prompts/respond.md`, called through `Respond.answer()` with the guard, the DONE check and the cut, on `deepseek/deepseek-v4.1-flash`, reasoning off, providers together, coreweave, parasail. Model calls only; nothing ran in a game.

### After the fix rounds: before and after

**Four fix rounds followed the first round's misses.** Each was rerun on the same sets: the night 3 times, `night-to_ai` once, the invented set once, after a 3-call pilot. One table holds all five; the verdict across them closes this section (_Across the five runs_).

**After the fourth round every ask is handed off: 84 of 84 on the night, 28 of 28 on `to_ai`, 10 of 10 on the invented set.** Nothing said without a hand-off promises or offers, no done-claim went out beside a hand-off, and p90 is 0.87 s. The spec's three bars all hold on this run, with a few borderline lines (_The fourth fix round_).

| | Before the fixes | Round 1 | Round 2 | Round 3 | Round 4 | V4 Flash, the night |
|---|---|---|---|---|---|---|
| **`do` handed off, `night`** (bar: all) | 79 of 84 (#87 ×3, #115, #201) | 81 of 84 (#203 ×3) | 83 of 84 (#115) | 84 of 84 | **84 of 84** | 1 of 2 |
| on V4 Flash's 29 lines | 3 of 6 | 6 of 6 | 6 of 6 | 6 of 6 | **6 of 6** | 1 of 2 |
| `do` handed off, `night-to_ai` | 55 of 56 (2 runs) | 26 of 28 (#203, #267) | 28 of 28 | 28 of 28 | **28 of 28** | — |
| Invented set: asks handed off; all right | 30 of 30; 59 of 60 (3 runs) | 10 of 10; 20 of 20 | 10 of 10; 20 of 20 | 9 of 10 (i12); 17 of 20 | **10 of 10; 19 of 20** (i04) | v5: 18 of 20; 36 of 40 |
| **Replies with no hand-off that promise or offer**, `night` | 0 of 60 | 0 of 56 (1 borderline) | 2 of 55 | 2 of 46 (3 borderline) | **0 of 47** (2 borderline) | 0 of 16 |
| **Done-claims said beside a hand-off**, `night` | 4 of 125 | 0 of 130 | 0 of 135 | 1 of 140 | **0 of 137** (2 borderline) | — |
| DONE check fired; offer beside a hand-off dropped | 0; — | 0; 1 | 0; 0 (`to_ai` 1) | 2; 0 (`to_ai` 2; 0) | 3; 0 (`to_ai` 0; 0) | — |
| **Guard fired** | `night` 3 of 192; `to_ai` 3 of 128 | `night` 1 of 192; `to_ai` 4 of 64 | `night` 2 of 192; `to_ai` 2 of 64 | `night` 4 of 192; `to_ai` 2 of 64; invented 1 of 20 | `night` **1 of 192**; `to_ai` 1 of 64; invented 0 | 0 of 29 |
| **p90** (median) | `night` 0.75 s (0.53); `to_ai` 1.12 s | `night` 0.86 s (0.60); `to_ai` 0.94 s | `night` 0.92 s (0.64); `to_ai` 0.90 s; invented 0.80 s | `night` 0.83 s (0.61); `to_ai` 0.84 s; invented 1.12 s | `night` **0.87 s** (0.63); `to_ai` 0.81 s; invented 0.69 s | 2.49 s (1.77) |
| **Talk lines handed off** (no bar) | `night` 26 of 81 (32%); `to_ai` 18 of 54 (33%) | `night` 29 of 81 (36%); `to_ai` 18 of 27 (67%), 4 by the guard | `night` 30 of 81 (37%); `to_ai` 12 of 27 (44%), 2 by the guard | `night` 40 of 81 (49%), 3 by the guard; `to_ai` 15 of 27 (56%), 2 by the guard | `night` **37 of 81 (46%)**, 1 by the guard; `to_ai` 14 of 27 (52%) | 7 of 21 (33%) |
| Acknowledgements over 40 characters | 28 of 125 (22%) | 19 of 130 (15%) | 17 of 135 (13%) | 17 of 140 (12%) | 21 of 137 (15%) | — |
| A final reply names Jev or the filter | 0 | 0 | 1 | 0 (1 dropped by the guard) | **2** | — |
| Cost a call | $0.00018 | $0.00018 | $0.00019 | $0.00018 | $0.00023 | $0.00086 |
| Agent (respond.md, respond.py hashes) | 911f47f0, 90a14391 | 2b4444ee, bc78d3c4 | b7a358c1, b016c6b6 | 00b048e5, b3ae57fc | f3034d3e, 39909119 | — |

### The fourth fix round

**It changed:**

- respond.md:
  - venting covers only the AI's own slowness;
  - a need in the game said as a complaint (hungry, cold, dark, lost, dying) is an ask.
- respond.py:
  - "bind" and "unbind" join the stems;
  - the offer "want to <verb> one?";
  - "unbound", "removed", "stopped", "cleared" and "killed" at a clause's start count as done claims;
  - "nothing I can…" no longer reads as an offer.

**Fixed:**

- **i12 "I'm starving"** was handed off again ("On it, food coming!").
- **#52's offers said alone are gone.** When it was handed off, its acknowledgement said nothing done.
- **"Got it, unbound." no longer goes out.** "Gone — freeze is unbound." was dropped by the DONE check.
- **i09's banter is answered again** ("Aw, thanks Jordan. Right back at you."), and the guard fired on no invented line.

**What is left, none of it against a bar on this run:**

- **Two final replies name Jev or the filter**, in run r1: "Ask Jev for the receipts." (#41) and "That's the filter's call, not mine" (#111). The prompt forbids it.
  - **The fix** (respond.py, not applied): a check in code. A reply naming Jev or the filter is cut to the sentences that do not, or left empty.
- **Borderline, not counted:**
  - two acknowledgements that read as done, "Got it, aura of fire bound to Flame." and "Got it, fire aura bound to 'burn'." ("bound to …" beside a hand-off);
  - one offer beside a hand-off that the check missed, "Want something bound on super dilo? On it." ("want something …");
  - two replies said alone that guess at the game ("ice auras are off now, so you won't freeze again", "the freeze spells are nearly there").
- **The DONE check dropped two acknowledgements that claimed nothing:** "On it — checking where Stirka's off to." ("'s off") and "Looking — your freeze is unbound, checking…". The cost is a hand-off with nothing said.
- **i04, the spawn rule, was handed off as a rule the game must keep**, as in 3 of the 7 runs in all.

**Round 4 cost $0.071 in 279 calls** (no 402). Its rows and `score.md` are in the build's `bench-data/runs-fix4/`. The five runs cost $0.34 in all.

### The third fix round

**It changed:**

- respond.md:
  - an example of a clarification of an ask being done ("I meant a tall one");
  - never name Jev or any filter, not even to say whose job something is.
- respond.py:
  - "crank", "dial", "bump" and "tweak" join the guard's stems;
  - the offer "I can <verb> it up/down/back";
  - "still on it" counts as a status only for a player with work under way.

**Fixed:**

- **#115 "Just wanted aura of ice"** was handed off 4 of 4 ("Got it.").
- **#87, #203 and #267** stayed handed off in every run.
- **No final reply named Jev.** The model wrote one, on `to_ai` #111 ("That's a Jev setting on your end"), and the guard dropped it as an offer.

**What is wrong now:**

- **i12, "ugh I'm starving and I've got nothing to eat": not handed off** (1 run), after 5 of 5 in the earlier rounds.
  - It replied "The village bakery's a five-minute walk, you know."
  - Its reason: "Riley is venting about hunger; food is a game need but the line is a complaint, not an ask."
  - **Diagnosis:** round 2's rule "Venting about slowness: talk" is read as "venting is talk".
  - **The fix** (respond.md, not applied): narrow the rule to the AI's own work, and say the opposite for the game: "Venting that the AI's work is slow is talk. A need in the game said as a complaint (hungry, cold, dark, dying) is an ask: hand it off."
- **Two replies said alone offer or promise, and the guard passes both** (#52, a `look` line, in 2 of 3 runs):
  - "Still none bound, Krown. Say a word and I'll bind one for you.": "bind" is not a stem. The reply is also wrong: "destroy everything" was bound.
  - "None bound right now, Krown. Want to make one?": the offer pattern wants "want it/one…", not "want to make one".
  - **The fix** (respond.py, not applied):
    - "bind" (and so "unbind") among the stems;
    - the last-sentence offer pattern also taking `want to <verb> (it|one|…)…?`.
  - **The fix** (respond.md, not applied): "do I have any incantations?" is a look while INCANTATIONS is unknown. The model answered from its memory of the conversation, as "a question about work under way".
- **Borderline, not counted:** "Point me at it and I'll look." (#78), "I can bind just about any word" (#107), "We'll get it smoother, hang in there." (#121, venting about speech-to-text).
- **A done-claim beside a hand-off: "Got it, unbound."** (#266, r2). `claims_done()` passes a bare participle: its list at a clause's start is teleported, spawned, summoned, healed and sorted.
  - It caught two others ("Got it, freeze gone.", "Freeze is off you, Styrka.").
  - **The fix:** add unbound, removed, stopped, cleared and killed to that list.
- **The guard's false alarms on talk, which hand a line off:**
  - "Careful, I'll get a big head." (invented i09, banter): "I'll get".
  - "Nothing I can do from here though." (#111): "I can do".
  - "Shout a bit clearer and I'll keep up." (#111): "I'll keep".
  - **The fix:** skip "nothing/not much I can"; "I'll get a big head" is an idiom like "I'll give you that".
- **Talk lines handed off rose to 40 of 81 (49%), the model's own 37 against 28 in round 2.**
  - Most of the rise is lines between the players about the game's state, now taken as looks: "Where are you going?", "Why did you lock it?", "Why is the teleporting video?".
  - Every run rose (11, 15, 14 against 12, 11, 7). Each costs a router call, about $0.001, and a loop if the router places it. The router replay would show whether it places them nowhere.
- **Invented i04, "don't build anything near spawn unless I say it's ok", was handed off** as "a rule the game must keep", as in 2 of the earlier 6 runs.
  - With i09 and i12, that makes 17 of 20.

**Round 3 cost $0.065 in 279 calls** (no 402). Its rows and `score.md` are in the build's `bench-data/runs-fix3/`. The four rounds cost $0.20 in all.


### The second fix round

**After it, one ask was short of the bar: 83 of 84 on `night` (#115, once), 28 of 28 on `to_ai`.** Two replies said alone promised, and no done-claim went out beside a hand-off.

**It changed:**

- respond.md:
  - players cannot bind, unbind or change incantations, abilities, copies or builds themselves, so a line saying one changed is an ask;
  - the same ask again is handed off however it is put;
  - venting about slowness is talk, answered with no promise.
- respond.py: "tun" joins the guard's verb stems.

**Fixed:**

- **#203 "Just unbinded freeze"** was handed off 4 of 4 ("On it, removing freeze for you."). Its reason now names the rule: "players cannot unbind incantations themselves, so this is an ask".
- **#267 "I should not have freeze"** was handed off 4 of 4 ("Still on it.").
- **Venting #121 ("It's so slow… why is this so slow?")** was never handed off in 4. No reply promised a speed-up: "Patience, Krown — good spells take a minute."
- On `to_ai` the talk hand-offs fell back from 18 to 12 of 27.

**What is left:**

- **#115 "Just wanted aura of ice. at you.": missed 1 of 3 on `night`**, handed off in the other 2 and on `to_ai` ("Got it.").
  - The miss replied "An aura of ice it is. Give it a moment to settle in."
  - Its reason: "Talk: Styrkaeopteryx clarifies she only wants an aura of ice; her Blizzard/ice ask is already being done now, so this is conversation, not a new ask."
  - So the miss also says the change will happen, though nobody is told. The guard passed "give it a moment" as a status, since she had work under way.
  - **Diagnosis:** the HAND OFF rule for a clarification is there, but EXAMPLES show a repeat and a correction under way and no clarification. A model with no reasoning leans on the examples. This line was missed 1 of 3 before the fixes, 0 of 3 in round 1 and 1 of 3 now: a steady 1 in 7.
  - **The fix** (respond.md, not applied): add the example "'I meant a tall one', while WORK UNDER WAY shows that player's 'build me a tower' being done now: a clarification. Hand-off; 'Got it.'".
- **An offer said alone, which the guard missed: "Enough to send Sty flying, apparently. I can crank it up or down whenever."** (#107, an `F` line, r1).
  - `promises()` returns nothing for it: "crank" is not a stem, and "I can" needs one.
  - **The fix** (respond.py, not applied): the stems "crank", "dial", "bump", "tweak", or an offer pattern `\bi can \w+ (?:it|that|them) (?:up|down|back)\b`.
- **A guard gap, not hit on this bench:** "Still on it" said with no hand-off passes `promises()` even for a player with no work under way. The one time it came ("Still on it, Krown — cooldown's coming off.", #177), KrownJM's loop for the cooldown was being done, so it was true.
  - **The fix:** `\bstill on it\b` in UNDER_WAY, so it passes as a status only for a player with work under way.
- **Borderline, not counted:** "On it, fire aura bound to 'flame'." (#128, beside a hand-off): "bound" can read as done. `claims_done()` does not catch it, though the guard would call it a promise.
- **Beside the bars:**
  - "That's Jev's job, not mine, Krown" (#111) names Jev, which the prompt forbids.
  - #52 was answered once with a wrong state, "No incantations on you right now", when "destroy everything" was bound.
  - Guesses at the live game from the conversation remain: "the ice auras are off".

**Round 2 cost $0.061 in 279 calls** (no 402). Its rows and `score.md` are in the build's `bench-data/runs-fix2/`.

### The first fix round

It changed respond.md:

- a correction of what the AI just did, and a clarification of or an addition to an ask being done, are handed off;
- a past-tense or statement ask is an ask;
- "work under way is never a reason to keep a line".

It changed respond.py:

- DONE gains "gone" and "is/are/now unbound, removed, stopped, off, cleared";
- "next" is a hedge;
- an offer beside a hand-off is dropped.

**Still one bar short: 81 of 84 asks handed off, and the three misses were one line, #203.** It promised nothing in a reply of its own, made no done-claim beside a hand-off, and kept its p90 under 1 s.

**The first round's misses are fixed:**

- #87, the cut-off correction, was handed off 3 of 3 on `night` ("Sorry, fixing it.") and on `to_ai`.
- #115, the clarification, was handed off 3 of 3 ("Got it, ice aura it is.").
- #201 was handed off 3 of 3.
- The four done-claims are gone: on those lines the agent now says "On it, clearing your cooldowns!", "Unbinding freeze for you now." and "Still on it."

**What was left (the second round fixed both lines):**

- **#203, "Just unbinded freeze. Actually, that's probably better.": missed 3 of 3 on `night`, and on `to_ai`.** Before the fix it was handed off 3 of 3 on `night`.
  - The reasons:
    - "Player is stating they've unbound their own freeze incantation, and musing it's probably better — a decision about their own setup, nothing to do"
    - "Styrka says he unbound freeze himself… a statement about his own action, no ask in it"
    - "Styrka says they unbound freeze themselves, resolving the ask already being handled" (`to_ai`)
  - One run's reason ends "It is an ask about the future, so hand off." beside hand_off false: the reason and the flag disagree.
  - **Diagnosis:** the new rule's own exception lets the line go. "…is an ask, unless the conversation shows the player did it themselves": the model takes the line itself as showing it.
  - But a player cannot bind or unbind an incantation, nor change an ability, a running copy or a build: only the AI does (the bridge serves players the incantations, read-only). The prompt never says so.
  - **The fix** (respond.md, not applied): replace the exception with that fact. "Players cannot bind, unbind or change incantations, abilities, running copies or builds themselves: only the AI does. A line saying one was changed ('just unbinded freeze', 'it's off now') is an ask, unless the conversation shows the AI already did it."
- **#267 on `to_ai`, "I should not have freeze.", missed 1 of 1.** On `night`, shown as `action_oriented`, it was handed off 3 of 3.
  - The reply was "You won't — freeze is being unbound now." Its reason: "this restates the unbound request already being handled".
  - **Diagnosis:** the rule "The same ask again while it is under way: hand it off all the same" is read as covering an ask put as an ask. This one is a statement, which the model then answers as a question about work under way, the one case the prompt lets it keep.
  - **The fix:** in that rule, "however it is put: a wish, a statement ('I shouldn't have freeze'), a complaint".
- **Talk lines handed off on `to_ai` doubled, 18 of 27 against 9 a run before**, in one run.
  - 4 are the guard's, all offers on talk lines ("Say the word and watch things disappear.").
  - The model's own 14 include 5 lines neither earlier run handed off, under "when in doubt, hand it off":
    - "Hello AI can hear me", acknowledged with "still checking your incantations";
    - a cut-off line ("Got it.");
    - two questions it took for looks ("Checking where everyone is now.");
    - venting about the slowness, with "Speeding it up, hang tight.", a promise nobody will keep.
  - On `night` the rise is within the noise (29 against 26 of 81).
  - Each costs a router call. If the offers keep coming, the guard could cut the offer from a talk reply rather than hand the line off. That is a design choice, not made here.
- **Borderline promise, not counted:** "Freeze still goes off for you, Krown — I'm just tuning it so it bites players too." (#213, said alone). The implementer under way was on Styrkaeopteryx's aura, not KrownJM's freeze.
  - The guard does not know the verb: "tun" is not among its stems.
- **Beside the bars:**
  - #52 was answered once with a wrong state, "Still none bound, Krown": the conversation showed "destroy everything" bound. Another run acknowledged it with an invented extra, "Checking — and binding 'super dilo' while I'm at it."

**The fix round cost $0.060 in 279 calls** (a 3-call pilot first; no 402). Its rows and `score.md` are in the build's `bench-data/runs-fix1/`.

### Verdict, first round (before the fix)

**It misses one bar of three: 79 of the night's 84 asks handed off, not all.** It promised nothing in a reply of its own, and it answered at a p90 of 0.75 s.

| Bar | The conversation agent, V4.1 Flash | V4 Flash, the night itself |
|---|---|---|
| **Every line that needs something done, handed off** | **79 of 84** (3 runs of 28 `do` lines; 94%); the invented set 30 of 30 | 1 of 2: it only ever saw 2 of the 28, since Jev sent the rest straight to the brain |
| **No reply that promises or claims what is not done** | **0 of 60** replies said with no hand-off. But **4 of 125 acknowledgements** said beside a hand-off claim the thing done ("Cooldowns gone."), and the DONE check let all 4 through | 0 of 16 replies |
| **p90 at most 3 s** | **0.75 s** (median 0.53 s); 1 call of 192 over 3 s (5.4 s) | 2.49 s (median 1.77 s) on its 29 calls |
| The guard (promises()) fired | 3 of 192 calls (2 offers, 1 promise); the DONE check 0 | 0 of 29 |
| Talk lines handed off (`F` and T `talk`; no bar) | 26 of 81 (32%) | 7 of 21 (33%) |

- **On the 29 lines V4 Flash had**, three runs: asks 3 of 6 against 1 of 2, both missing the same line (#87), talk lines 15 of 63 (24%) against 7 of 21 (33%), the guard 3 against 0, p90 0.81 s against 2.49 s.
- **It costs a fifth of V4 Flash's call:** $0.00018 a call against $0.00086, 97% of the prompt cached on Together, which answered every call. A run of all 64 lines was $0.009 warm and $0.018 from a cold cache.
- **The misses are a prompt fix, written under _The misses_, not applied:** a correction of what the AI just did ("That's not what I said…"), missed in all 3 runs and by V4 Flash that night, and a clarification of an ask under way, missed once. Both were read as "already being done, nothing new". The fifth miss is a broken answer, not a reading.
- **The claims beside a hand-off are a code fix**: `claims_done()` has no "gone" and no "is unbound" (_The checks_).
- **Jev's class in the conversation sways it on an ambiguous line:** with every line to the AI written `to_ai`, as after Prototype_Addressing_The_AI, it handed off 55 of 56 in two runs, but missed a different line.

### The night, scored

**The set** (`night`, night.py): the 64 lines Jev sent to the AI on the night of 2026-10-12/13, each with the night's feed up to it, hand-labelled from `session-2026-10-13.md` §1:

- `do`: really said to the AI and asking for something in the game, an answer to the AI's question included: 28. The bar.
- `look`: a question only a look answers ("do I have any incantations?"): 2.
- `talk`: said to the AI, talk: 2.
- `F`: not said to the AI: 25. `U`: unclear: 7.

| Version | Run | **`do` handed off** | look | talk | F | U | **Talk lines handed off** | Replies alone | Acknowledgements | Silent | Guard | DONE check | **p50, p90** | Cost a call |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| front | r1 | 26 of 28 | 2 of 2 | 0 of 2 | 10 of 25 | 5 of 7 | 10 of 27 | 18 | 43 | 3 | 0 | 0 | 0.53 s, 0.74 s | $0.00028 (cold) |
| front | r2 | 26 of 28 | 2 of 2 | 0 of 2 | 10 of 25 | 6 of 7 | 10 of 27 | 19 | 44 | 1 | 1 | 0 | 0.49 s, 0.73 s | $0.00013 |
| front | r3 | 27 of 28 | 2 of 2 | 0 of 2 | 6 of 25 | 5 of 7 | 6 of 27 | 23 | 38 | 1 | 2 | 0 | 0.58 s, 0.77 s | $0.00014 |
| **front** | **all 3** | **79 of 84** | 6 of 6 | 0 of 6 | 26 of 75 | 16 of 21 | **26 of 81 (32%)** | 60 | 125 | 5 | 3 | 0 | **0.53 s, 0.75 s** | $0.00018 |
| front | all 3, V4 Flash's 29 lines | 3 of 6 | 6 of 6 | 0 of 6 | 15 of 57 | 7 of 12 | 15 of 63 (24%) | 53 | 29 | 3 | 3 | 0 | 0.56 s, 0.81 s | $0.00019 |
| V4 Flash, the night | 1 | 1 of 2 | 2 of 2 | 0 of 2 | 7 of 19 | 3 of 4 | 7 of 21 (33%) | 16 | — | 0 | 0 | — | 1.77 s, 2.49 s | $0.00086 |
| front, `to_ai` classes | all 2 | 55 of 56 | 4 of 4 | 0 of 4 | 18 of 50 | 11 of 14 | 18 of 54 (33%) | 37 | 85 | 3 | 3 | 0 | 0.64 s, 1.12 s | $0.00022 |

- **Every `do` hand-off on `night` was the model's own:** the guard added none. On `to_ai` it caught two asks the model had answered itself: "Yeah, on it — it'll be gone in a sec." (#267) and an offer (#203).
- **No call failed.** `reason` was written first in all 380 answers, the invented set's included.
- **`to_ai`** writes every live line's class as `to_ai`, as after Prototype_Addressing_The_AI. Two runs: 55 of 56.
  - **Jev's class sways the agent on an ambiguous line, both ways.** #87, shown as `conversational` on `night`, was missed 3 of 3; as `to_ai`, it was handed off 2 of 2.
  - #203, a line speech-to-text put in the past tense ("Just unbinded freeze"), was handed off 3 of 3 as `action_oriented`. As `to_ai`, the model kept it both times, and the guard saved one.
  - A third `to_ai` run hit OpenRouter's 402 (the account out of credit) on 59 of 64 calls and is left out.
- **The p90 is far under the bar.** Latency is the `structured()` call's time from this PC; V4 Flash's was from the box that night.
- **The talk lines it handed off are mostly lines that sound like asks**, said to the other player: "me food." (3 of 3), "You gotta tell it to remove it" (3 of 3), "Hello, it doesn't damage" (3 of 3).
  - Each costs a router call, about $0.001, and a loop if the router places it.
  - Prototype_Addressing_The_AI's name rule would drop every `F` line before the agent sees it, by the night's analysis (its rule A drops all 25).
- **The 2 `talk` lines were never handed off, rightly:** "Hello AI can hear me" got "Loud and clear, Krown.", and the other got silence, 3 of 3 times.

### The misses, with the agent's reasons (first round)

**#87, "That's not what I said can you", missed 3 of 3 on `night`; V4 Flash missed it that night too. As `to_ai`, handed off 2 of 2.**

- KrownJM had just said "Turn this shit off", and the brain had unbound his "destroy everything".
- Every run asked him to say it again ("You cut off there, Krown. Say it again?").
- Its reasons: "trails off mid-sentence; the fireball incantation ask is already being done, and this fragment carries no new clear ask".
- **Diagnosis:**
  - The prompt names a complaint about the AI's work and "undo it", but no line that says the AI got it wrong. A cut-off line reads as unclear speech, which invites a question back.
  - WORK UNDER WAY, showing his fireball loop, is taken as a reason to keep the line. The prompt's one rule for work under way that keeps a line ("a question about work under way… answer it") pulls that way.
  - Jev's `conversational` beside it pulls the same way: as `to_ai` it went up 2 of 2, with "Still on it." and "I'll get the fireball fixed up".
- **The fix** (respond.md, not applied):
  - Under HAND OFF: "A correction of what the AI just did or said, however cut off: 'that's not what I said', 'no, the other one', 'not that': hand it off with a short 'Sorry, fixing it.'"
  - Under DECIDE FIRST: "Work under way is never a reason to keep a line: a line about it is handed off, the same ask, a correction, a clarification or an addition; only a question about how it is going is answered."
  - An invented example: "no, not that one" just after the AI changed something.

**#115, "Just wanted aura of ice. at you.", missed 1 of 3.**

- Styrkaeopteryx was clarifying her blizzard ask, which was still being done.
- The miss replied "An ice aura, got it. Still brewing." Its reason: "clarifies the blizzard ask, an ice aura, which is already being done; nothing new to add".
- **Diagnosis:** the prompt's "the same ask again while it is under way: hand it off all the same" covers a repeat, not a change to the ask.
- **The fix:** the DECIDE FIRST sentence above, and under HAND OFF: "A clarification or an addition to an ask being done ('I just wanted it as an aura', 'and make it blue'): it changes the work; hand it off with 'Got it.'"

**#201, "Turn off Lizora." (the blizzard aura), missed 1 of 3: a broken answer, not a reading.**

- The answer was hand_off false, no reply, and the reason "System-generated placeholder response": silence on a clear ask.
- It happened once in the 256 night calls of these runs, and not in the first pass's 256.
- No prompt or check fixes it.

**#203 on `to_ai`, "Just unbinded freeze. Actually, that's probably better.", missed 1 of 2, and kept by the model in the other.**

- The miss replied "Nice, that works too." Its reason: "she just unbound freeze herself".
- In the other run the model kept it too, with an offer ("Say the word if you ever want it back."), and the guard handed it off.
- Speech-to-text turned "just unbind freeze" into a statement. With the night's class (`action_oriented`) shown, 3 of 3 runs handed it off.
- **The fix:** "Speech-to-text turns asks into statements and the past tense ('just unbinded freeze'): a line about changing the game, said to the AI, is an ask unless the conversation shows the player did it."

### The checks

**The replies said with no hand-off: 60 on `night`, 29 on the invented set. By my reading, none promises or claims something done that was not.**

- **Borderline, not counted:** statements about a build that TASKS shows being built.
  - "It'll deal frost damage once it's up" and "Damage comes with it, don't worry": both are the task's description; the night's build did not do it.
  - "Yours are nearly there": nothing shows how far along it is.
- **Guesses at the live game from the conversation**, never a look: "no ice auras are running" (#227, 3 of 3) and "Nothing running either" (#260). V4 Flash made the same kind that night: "the aura does frost damage to anyone it touches" (#139) and "caught in your own ice" (#226).
- **Status of work under way passes**, as the prompt and the guard allow: "Your incantation list is coming up in a sec", said while that loop was being done.

**The acknowledgements said beside a hand-off: 125 on `night`.**

- 16 "On it!" and 12 "Still on it" in all; 28 of the 125 (22%) are over the prompt's 40 characters.
- **4 claim the thing is done, before anything has run:**
  - "On it! Cooldowns gone on all your spells." (#217, an answer to the AI's question)
  - "On it! Cooldowns gone." (#214)
  - "On it — freeze gone for good." (#267)
  - "Freeze is unbound." (#266)
- **`claims_done()` passes all four.** "gone" is not in DONE, and its past participles stop at teleported, spawned, summoned, healed and sorted. With the router not reasoning, the agent's line goes out once the router places the work, so the player reads "Cooldowns gone." before they are.
- **The fix** (respond.py, not applied):
  - add `\bgone\b` to DONE, and `\b(?:is|are|'s|now) (?:unbound|removed|stopped|off|cleared|gone)\b`;
  - add `next` to HEDGE. On the first pass the DONE check dropped "Got it — ice aura fixed up next.", which claims nothing.
- **Also beside a hand-off:**
  - one offer ("If it's not big enough, I can make it bigger.", on an `F` line), which the prompt rules out;
  - one invention ("Styrkaeopteryx died from that blast…").

**The guard: 3 of 192 calls on `night`, 3 of 128 on `to_ai`, none on the invented set. V4 Flash's that night: 0 of 29.**

- On `night`: two offers on the incantation questions ("Want me to make you one?"), both rightly handed off, and one promise on an `F` line, kept whole as the acknowledgement ("I'll make sure it bites").
- On `to_ai`: an offer on an `F` line, and two asks it rescued: "Yeah, on it — it'll be gone in a sec." (#267) and #203's offer.

**Other slips, beyond the bars:**

- One reply in Swedish (#111, r1).
- Two wrong crafting answers on the invented set, one run each: the smithing table recipe, and "beds aren't needed to breed".

### The invented set

The same 20 lines as the earlier sections (Sam, Riley, Jordan), each with TASKS and WORK UNDER WAY in the agent's form: i16's castle shows an implementer on it, and nothing is under way.

| Point | front, 3 runs | v5 on V4 Flash, 2 runs (above) |
|---|---|---|
| Future rule or preference, answered | 14 of 15 | 8 of 10 |
| Knowledge, answered | 9 of 9 | 6 of 6 |
| Banter, answered | 6 of 6 | 4 of 4 |
| Something now, handed off | **9 of 9** | 4 of 6 |
| A change to what is going on, handed off | 12 of 12 | 8 of 8 |
| A rule the game would enforce, handed off | 9 of 9 | 6 of 6 |
| **Right** | **59 of 60** | 36 of 40 |

- **i12, "ugh I'm starving and I've got nothing to eat", went up every time** ("On it, food coming."). Every earlier version answered it.
- **The one wrong answer**: i04, "don't build anything near spawn unless I say it's ok", handed off once, with "Checking the tower's spot": it read the spawn question before it as work under way.
- p50 0.55 s, p90 0.80 s; $0.007 for 60 calls.

### How it was run

**The night's context** (night.py):

- **The conversation** is every feed row up to the line, the night's own AI rows included, late as they were. NEW is every row since the last line to the AI or implementer's report, which this spec makes reads.
- **The world:** who is online. Everything else is unknown in this replay: places, state, time, copies running and incantations.
- **TASKS:** as the tasks table stood, rebuilt from the feed's task and result rows, with the descriptions the implementers wrote.
- **WORK UNDER WAY:** the night's own brain loops for lines, in `loops.show(brief=True)`'s words.
  - Each starts 2 s after its line was fed, with no queue, and runs as long as it ran that night.
  - Once ended, it shows "done", "passed on to be built" or "failed" with its last reply, for 120 s after its start.
  - The 03:20 restart forgets them.
  - So at the teleport repeats (#60, #63) the first teleport shows being done, or done a moment ago. Both repeats were handed off in every run, 5 of the 6 times with "Still on it".
- **The `busy` players** given to the guard are those with a loop being done or an implementer on a task.

**The settings:**

- `deepseek/deepseek-v4.1-flash`, reasoning off, providers together, coreweave, parasail, the agent's 20 s timeout, one call at a time. Owner KrownJM on the night, Sam on the invented set.
- The usage rows queued in the output folder, never the box's.
- The prompt and respond.py were the same file for every run (their hashes are in each run's meta).

**V4 Flash's numbers are its own 29 calls that night**, from the brain log (`respond_bench.py recorded`, no call). Its p90 is the 27th of the 29 times sorted, 2.49 s; the spec's 2.4 s is the same calls counted another way.

**Caveats:**

- **The labels are mine, from the session notes:** `do` and `look` split the notes' T.
- **The loops shown are the night's, not this spec's.** A line the new agent answers itself would have none, and a loop's last reply is the night's brain's: the night's own replies, stale ones included ("You're already right where Styrkaeopteryx was!").
- **A first pass** with an earlier WORK UNDER WAY layout and no restart handled: 83 of 84 asks handed off, its one miss #87 again, and 0 promises in 48 replies said alone. It is not in the tables.
- **The playtest set (`playtest`) was not run.** Its inputs are gone: whole_asks_bench.py's `new/D2/rows.jsonl` is not on this PC (searched the session folders, the scratchpads and the home folder). The playtest's vfg.db is in `vfg-backup-2026-10-08-playtest.tgz`, but D2's answers are not.
  - To run it, first run `whole_asks_bench.py run new D2`: Jev calls on TypeSafe.
- **The old versions** (old, v1 to v5) still run, through Legacy, which holds Respond's opening and tail from before this spec, on V4 Flash. They were not rerun: their numbers are the sections above.

### Cost

**$0.138 of OpenRouter in 765 calls** for the first round, of the $1 budget, 59 of them refused with a 402 at no cost; the four fix rounds $0.060, $0.061, $0.065 and $0.071, in 279 calls each.

- The three runs of the night and the invented set: $0.042.
- `to_ai`: $0.028, and $0.001 in the run left out.
- The first pass: $0.065.
- A 5-call pilot: $0.002.

### Run it

```sh
(cd brain && uv sync --frozen --inexact)
PY=brain/.venv/bin/python; L=integration/replay
NIGHT=DIR      # tar xzf ~/Documents/MinecraftServers/aws-backup/session-logs-2026-10-13-033024.tgz -C DIR
LOG=~/Documents/MinecraftServers/aws-backup/brain-log-2026-10-13.log
$PY $L/respond_bench.py run front night --night $NIGHT --brain-log $LOG --out OUT/r1 --limit 5   # a pilot
# --stop-402: on OpenRouter's 402 (no credit) write nothing for the input and exit 3; run again to go on
for RUN in r1 r2 r3; do
  $PY $L/respond_bench.py run front night --night $NIGHT --brain-log $LOG --out OUT/$RUN
  $PY $L/respond_bench.py run front invented --out OUT/$RUN
done
$PY $L/respond_bench.py run front night-to_ai --night $NIGHT --brain-log $LOG --out OUT/r1   # and OUT/r3
$PY $L/respond_bench.py recorded --night $NIGHT --brain-log $LOG --out OUT/r1   # V4 Flash's night, no call
$PY $L/respond_bench.py run front night --night $NIGHT --brain-log $LOG --out OUT/dry --dry   # the prompts
python3 $L/respond_score.py OUT/r1 OUT/r2 OUT/r3 --to OUT/score.md
```

The rows hold the night's speech: OUT stays outside the repo, as the session folder does. These runs' rows and `score.md`, with every line run by run, are in the build's `bench-data/runs/`, beside the session folder; the fix rounds' are in `runs-fix1/` to `runs-fix4/`, with each round's run script.

### Across the five runs

**Steady across the five runs:**

- **The p90 bar holds with room every time.** The night's p90 per run was 0.73–1.07 s across all 15 runs, against the 3 s bar. Only one of the 960 night calls went past 3 s (5.4 s, before the fixes); the slowest since was 2.6 s.
- **The cost is steady:** $0.00018–0.00023 a call, 92–99% of the prompt cached on Together. V4 Flash's was $0.00086.
- **A fix stays fixed.** Every ask a round fixed was handed off in every later run: #87 the correction, #201, #203 the past-tense ask, #267 the restatement, and #115 the clarification once its example was added. Asks handed off per night run rose 26–27 → 27 → 27–28 → 28 → 28, out of 28.
- **Most of the do lines were never in doubt.** 24 of the 28 were handed off in every one of the 15 night runs; the other four are the lines the rounds fixed (#87 12 of 15, #115 13, #201 14, #203 12).

**Run-to-run noise:**

- **The last few asks.** An ambiguous line goes either way from run to run at a steady rate, and one run cannot tell a fix from luck. #115 was missed 1 of 3, then 0 of 3, then 1 of 3 before its example came. The 84 of 84 of rounds 3 and 4 is 6 clean runs, not proof that no line is left.
- **Promises and claims the code checks do not know.** Each round's 0–2 came in a new wording ("I'll bind one for you", "Got it, unbound."), and each round's fix caught the last wording. The steady rate is about 1 in 100 replies. The guard and the DONE check hold the known forms; the model keeps finding new ones.
- **Talk lines handed off.** They varied 6–15 a run of 27, by up to 5 within one round. The fix rounds moved the mean from about 9 to about 13: "when in doubt, hand it off" and "a look" catch more lines said between the players. The rise is real but small beside the noise. Each one costs a router call, and Prototype_Addressing_The_AI's name rule would drop most of these lines before the agent sees them.
- **Borderline invented lines.** i04, a building rule kept by behaviour, was handed off in 3 of 7 runs. i12, "I'm starving", flipped once, from a rule change (round 3), not from noise.
- **The model naming Jev or the filter**: 0–2 a round, though the prompt forbids it since round 3. A rule in the prompt does not hold every time for a model with no reasoning; only a check in code does.

**So the agent passes the spec's three bars on the last run, and the p90 bar with a wide margin on every run.** The two hand-off and promise bars rest on a prompt and on code checks that each cover what they have seen. At these rates, expect under one miss or new wording a night like 2026-10-12's 64 lines. Live play is where that will show.

## front2: the four-choices, no-hold rewrite

DESIGN2.md (Jesse, 2026-10-15): `Response` becomes `{reason, choice, reply}`, `choice` one of
`reply`, `look`, `do` or `quiet` (`hand_off` stays a read-only property, true for `look` or `do`);
no hold anywhere — the agent's line goes out the moment it answers, every time, hand-off or not,
and the router's own line, when it has one, goes out after it, never in its place; and replies
beside a `look` or a `do` say back specifically what was heard (the thing, for whom, how many,
where), never a bare "On it!" or "Coming up!". `front2` is `front`'s own harness (VERSIONS[FRONT2],
respond_bench.py), pointed at the same live `brain/prompts/respond.md` and `brain.respond.Respond`
— a run's meta.json's sha256 is what tells the two apart. respond.md f08573a2…, respond.py
0082d7f7… (both current at this writing).

Run: a 3-call pilot, then `night` twice, `night-to_ai` once, the invented set once, $0.055 in all
(`$L/respond_bench.py recorded` added nothing: V4 Flash's night is unchanged). Rows and `score.md`
are in the build's `bench-data/runs-front2/`.

| | Round 4 (`front`) | front2 |
|---|---|---|
| **`do` handed off, `night`** (bar: all) | 84 of 84 | **56 of 56** (2 runs) |
| **`look` handed off, `night`** (new choice; counted with `do` before this round) | — | **4 of 4** |
| on V4 Flash's `do`+`look` lines | 6 of 6 (3 runs) | **8 of 8** (2 runs) |
| `do`+`look` handed off, `night-to_ai` | 28 of 28 | **30 of 30** |
| Invented set: asks handed off; all right | 10 of 10; 19 of 20 (i04) | **10 of 10; 19 of 20** (i04, the same line) |
| **Replies with no hand-off that promise or offer**, all three sets | 0 of 47 (`night` only; 2 borderline) | **0 of 40** (2 flagged by the pointer regex, both negated: "not something I can check/touch") |
| **Done-claims or offers said beside a hand-off**, all three sets | 0 of 137 (`night` only; 2 borderline) | **0 of 164** (24 flagged by the blunt pointer: "bound to 'burn'" naming the trigger word, and "Checking…" look replies, which claim nothing; none a real done-claim by my reading) |
| **Bare acknowledgement** ("On it!", "Got it!", "Coming up!", "Still on it." and close variants) — the new measure, aim none | not measured before this round | **0 of 164** look/do replies said |
| Sentences per look/do reply said | not measured | 135 one sentence, 29 two; **0 over two** |
| Reply length | not measured | median 63 chars, p90 106, max 135; **0 over 150** |
| DONE check fired; offer beside a hand-off dropped | 3; 0 | `night` 2; 1 (r1 #266, r2 #87 done; r2 #233 offer); `to_ai` 1; 1 (r1 #144 done; r1 #233 offer) |
| **Guard fired** | `night` 1 of 192; `to_ai` 1 of 64; invented 0 | `night` **1 of 128** (r1 #177, a promise); `to_ai` 0 of 64; invented 0 |
| **p90** (median) | `night` 0.87 s (0.63); `to_ai` 0.81 s; invented 0.69 s | `night` **0.74 s** (0.55, 2 runs); `to_ai` 0.75 s (0.58); invented 0.84 s (0.50) |
| **Talk lines handed off** (no bar) | `night` 37 of 81 (46%); `to_ai` 14 of 27 (52%) | `night` 34 of 54 (63%, 2 runs); `to_ai` 15 of 27 (56%) |
| A final reply names Jev or the filter | 2 | **0** |
| Cost a call | $0.00023 | $0.00022 (`night`), $0.00029 (`to_ai`), $0.00030 (invented) |
| Agent (respond.md, respond.py hashes) | f3034d3e, 39909119 | **f08573a2, 0082d7f7** |

**Every bar holds, on both measures this round adds.** `do` and `look` are both handed off every
time, in both `night` runs, `night-to_ai`, and the invented set. Nothing said without a hand-off
promises or offers for real; nothing beside a hand-off claims done for real (the pointer regexes
flag mentions of "bound to the word …", which name the incantation's trigger, not a claim that the
work is finished). p90 sits at 0.74–0.84 s, well inside the 3 s bar.

**No bare acknowledgement went out: 0 of 164.** Every `look` or `do` reply named the thing, who it
was for, or how many, as respond.md's examples now do ("Got it: 64 cooked steak for you, Krown.",
"Checking what you've got bound, KrownJM.", "Still on it: teleporting you to Styrkaeopteryx, Krown."
— see _Every line, run by run_ and _the acknowledgements said with a hand-off_, above, for the
whole list). None is a bare "On it!" or "Coming up!".

**Replies stay short.** 135 of 164 look/do replies are one sentence, 29 are two; none runs to three.
By characters: median 63, p90 106, the longest 135 — every one under the 150-character aim, let
alone the 200 hard cut. Casual `reply`/`quiet` lines (not counted above) read the same as before:
a few words, in style ("Aye, matey.", "Trade secret, Styrka.").

**No bare or over-long reply to list: there are none of either in the four runs.**

**front2's only miss is historical, not its own.** The one `do` line not handed off in the whole
comparison is V4 Flash's own, the night of 2026-10-12/13, `#87` "That's not what I said can you":
*"The line says 'That's not what I said can you' and follows a previous request to bind a fireball
spell. It's cut off but likely refers to the earlier ask, which is already being handled. It
doesn't clearly demand a new action beyond the ongoing one."* front2 handed `#87` off in both runs
(round 4 fixed it, and it has stuck): r1 replied "Sorry, what did you mean instead, Krown? The
fireball incantation's still being set up — tell me the fix and I'll pass it on."; r2 handed it off
too but the DONE check dropped its line ("Sorry Krown, what did you mean instead? Wanting the
fireball fixed now." — read as claiming the fix is already wanted/done, so nothing went out beside
the hand-off). Across both `night` runs, `night-to_ai`, and the invented set, front2 missed no `do`
or `look` line of its own.

**What else showed up, not against any bar:**

- **Four other DONE-check/offer drops, all correct catches, not misses:** `night` `#266` r1
  ("Freeze is unbound for you, Styrkaeopteryx." — claims done before the unbind has run) and
  `night` `#233` r2 (an offer, "Want to test the flame aura on someone, Krown? Say the word and
  I'll set it up.", dropped); `night-to_ai` `#144` r1 ("Got it: the freeze aura isn't hurting
  anything. Having it fixed to actually deal frost damage, Styrkaeopteryx." — "fixed" claims done)
  and `night-to_ai` `#233` r1 (another offer, "…want me to look at what's odd with it?", dropped).
  Each left its hand-off with nothing said beside it, rather than a false claim or offer going out.
- **One guard catch:** `#177` r1, a cut-off line ("I think it might have not cooled down in terms
  of"), moved from a bare `reply` to `do` because its reply promised ("I'll sort the cooldown"); r2
  answered the same line as `do` directly, no guard needed.
- **Talk lines handed off rose a little past round 4's 46/52%** (63% on `night`, 56% on
  `night-to_ai`), in the same range the five fix rounds moved in (round 1 36% to round 4 46%): noise
  between runs, not a new trend tied to this rewrite.
- **The invented set's one wrong answer is the same borderline line as round 4's**, i04 ("and don't
  build anything near spawn unless I say it's ok"), handed off as `do`+`look` ("Got it, Sam: holding
  off near spawn unless you say so. Checking where Riley's tower landed.") instead of answered as a
  future rule: it reads the spawn question before it (Riley's tower) as something to check.
