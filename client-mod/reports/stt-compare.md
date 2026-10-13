# Speech-to-text comparison

901 utterances, 27.0 min of speech in 907 segments cut by the mod's VAD after its 48 kHz path; 2 speech-to-text threads, on AMD Ryzen AI 5 340 w/ Radeon 840M.

- **Minecraft set**: 229 utterances (5 recordings; 6 Minecraft phrases and 8 hard ones in the 8 Kitten voices, 2 takes each). Its players: Steve, Alex, SteveJM.
- **Addressing set**: `stt-addressing.txt (committed, invented)`, 84 lines, each said by Kokoro in 4 voices as it speaks (clean) and in 4 others 1.25× as fast with pink noise 10 dB under the speech (hard): 672 utterances. Players: Pyrothane, DuskJR, Steve_123, Mirelle, Tobble. Phrases: fire ball, frost nova, blizzard aura, ember shield, destroy everything, freeze, thunder step, ice aura.
- **Hotwords** (the mod's list, for a run with hotwords): "AI", "brain", the players' names, the phrases and the command words, each with its own boost. A term is *through* when the transcript says it as often as the reference (letters compared, spaces and case ignored: "Dusk JR" is DuskJR); a *false* hotword is one said more often than in the reference.

| Model | WER Minecraft | WER addressing: clean / hard / no-name lines | AI | brain | names | incantations | command words | false per 100 no-name lines: addressing / Minecraft | STT per segment, median / p90 | added over greedy, median / p90 | load | RSS after load / peak |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Moonshine base | 14.5% | 5.2% / 23.8% / 4.2% | 79% (127/160) | 75% (24/32) | 41% (33/80) | 67% (80/120) | 76% (91/120) | 0.0 / 0.6 | 41 / 57 ms | - | 0.5 s | 221 / 323 MB |
| Moonshine tiny | 20.0% | 7.5% / 26.2% / 5.0% | 74% (119/160) | 66% (21/32) | 35% (28/80) | 58% (70/120) | 66% (79/120) | 0.0 / 0.0 | 22 / 31 ms | - | 0.3 s | 91 / 118 MB |
| Parakeet 110M, greedy (0.3.4's default) | 8.8% | 4.8% / 12.9% / 1.7% | 91% (145/160) | 81% (26/32) | 41% (33/80) | 78% (94/120) | 70% (84/120) | 0.0 / 0.0 | 31 / 42 ms | - | 0.6 s | 205 / 270 MB |
| Parakeet 110M + hotwords | 7.9% | 1.7% / 8.3% / 1.5% | 96% (154/160) | 94% (30/32) | 69% (55/80) | 88% (106/120) | 91% (109/120) | 0.3 / 0.6 | 38 / 58 ms | +6 / +20 ms | 0.6 s | 201 / 273 MB |
| Parakeet 0.6B v2, greedy | 5.2% | 3.5% / 8.8% / 0.9% | 93% (149/160) | 81% (26/32) | 54% (43/80) | 82% (98/120) | 88% (106/120) | 0.0 / 0.6 | 110 / 150 ms | - | 1.1 s | 861 / 942 MB |
| Parakeet 0.6B v2 + hotwords (the mod's default) | 4.5% | 1.6% / 5.6% / 0.8% | 98% (156/160) | 91% (29/32) | 71% (57/80) | 93% (112/120) | 95% (114/120) | 0.0 / 0.6 | 112 / 148 ms | +2 / +11 ms | 1.1 s | 861 / 951 MB |

No-name lines: 320 in the addressing set, 181 in the Minecraft set. WER is word errors (substitutions, deletions, insertions) over reference words, after lowercasing, dropping punctuation, numbers as words, joining spelled letters ("T P" = "tp") and a player's name said in parts. The names, incantations and command words columns are the addressing set's. STT time is per segment on 2 threads, one warm-up decode left out; "added over greedy" is, segment by segment, the time with hotwords less the same model's greedy time: what the line's last stretch waits longer. Memory is resident memory added by loading (the mod's VAD and the natives included), after loading and at the peak while transcribing.

## Against the proposed bar (Parakeet 0.6B v2 + hotwords (the mod's default))

- Fewer word errors than 110M: 3.8% against 8.9% over both sets — **pass**
- "AI" through in ≥95% of the lines holding it: 98% (156/160) — **pass**
- The players' names through in ≥95% of the lines holding them: 71% (57/80) — **FAIL**
- ≤1 false hotword in 100 lines with no name: 0.2 per 100 (1 in 501 lines) — **pass**

## False hotwords by kind (both sets; in no-name lines / in all lines)

| Model | AI | brain | names | incantations | command words |
|---|---|---|---|---|---|
| Moonshine base | 0 / 2 | 0 / 2 | 0 / 0 | 0 / 1 | 1 / 4 |
| Moonshine tiny | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| Parakeet 110M, greedy (0.3.4's default) | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| Parakeet 110M + hotwords | 0 / 0 | 0 / 1 | 0 / 0 | 0 / 1 | 2 / 2 |
| Parakeet 0.6B v2, greedy | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 1 / 1 |
| Parakeet 0.6B v2 + hotwords (the mod's default) | 0 / 0 | 0 / 3 | 0 / 0 | 0 / 0 | 1 / 1 |

## Each term, through (addressing set)

| Term | moonshine-base | moonshine-tiny | 110m | 110m+hw | 0.6b | 0.6b+hw |
|---|---|---|---|---|---|---|
| AI:AI | 79% (127/160) | 74% (119/160) | 91% (145/160) | 96% (154/160) | 93% (149/160) | 98% (156/160) |
| BRAIN:brain | 75% (24/32) | 66% (21/32) | 81% (26/32) | 94% (30/32) | 81% (26/32) | 91% (29/32) |
| NAME:Pyrothane | 38% (6/16) | 44% (7/16) | 25% (4/16) | 81% (13/16) | 69% (11/16) | 94% (15/16) |
| NAME:DuskJR | 69% (11/16) | 50% (8/16) | 81% (13/16) | 100% (16/16) | 94% (15/16) | 94% (15/16) |
| NAME:Steve_123 | 94% (15/16) | 81% (13/16) | 100% (16/16) | 100% (16/16) | 100% (16/16) | 100% (16/16) |
| NAME:Mirelle | 0% (0/16) | 0% (0/16) | 0% (0/16) | 0% (0/16) | 0% (0/16) | 25% (4/16) |
| NAME:Tobble | 6% (1/16) | 0% (0/16) | 0% (0/16) | 63% (10/16) | 6% (1/16) | 44% (7/16) |
| PHRASE:fire ball | 100% (24/24) | 100% (24/24) | 100% (24/24) | 96% (23/24) | 96% (23/24) | 96% (23/24) |
| PHRASE:frost nova | 63% (10/16) | 38% (6/16) | 63% (10/16) | 88% (14/16) | 81% (13/16) | 94% (15/16) |
| PHRASE:blizzard aura | 38% (3/8) | 38% (3/8) | 50% (4/8) | 63% (5/8) | 63% (5/8) | 88% (7/8) |
| PHRASE:ember shield | 25% (2/8) | 0% (0/8) | 100% (8/8) | 100% (8/8) | 63% (5/8) | 100% (8/8) |
| PHRASE:destroy everything | 100% (8/8) | 75% (6/8) | 100% (8/8) | 100% (8/8) | 100% (8/8) | 100% (8/8) |
| PHRASE:freeze | 63% (20/32) | 66% (21/32) | 78% (25/32) | 94% (30/32) | 84% (27/32) | 100% (32/32) |
| PHRASE:thunder step | 63% (5/8) | 75% (6/8) | 75% (6/8) | 75% (6/8) | 88% (7/8) | 88% (7/8) |
| PHRASE:ice aura | 50% (8/16) | 25% (4/16) | 56% (9/16) | 75% (12/16) | 63% (10/16) | 75% (12/16) |
| COMMAND:unbind | 96% (23/24) | 96% (23/24) | 88% (21/24) | 96% (23/24) | 100% (24/24) | 100% (24/24) |
| COMMAND:incantations | 83% (20/24) | 67% (16/24) | 25% (6/24) | 100% (24/24) | 100% (24/24) | 100% (24/24) |
| COMMAND:aura | 53% (17/32) | 25% (8/32) | 69% (22/32) | 78% (25/32) | 72% (23/32) | 84% (27/32) |
| COMMAND:cooldown | 69% (11/16) | 88% (14/16) | 100% (16/16) | 100% (16/16) | 100% (16/16) | 100% (16/16) |
| COMMAND:mobs | 63% (5/8) | 50% (4/8) | 63% (5/8) | 75% (6/8) | 50% (4/8) | 88% (7/8) |
| COMMAND:spell | 100% (8/8) | 88% (7/8) | 88% (7/8) | 100% (8/8) | 100% (8/8) | 100% (8/8) |
| COMMAND:spells | 88% (7/8) | 88% (7/8) | 88% (7/8) | 88% (7/8) | 88% (7/8) | 100% (8/8) |

Minecraft set, its players' names: moonshine-base 47/48; moonshine-tiny 47/48; 110m 48/48; 110m+hw 48/48; 0.6b 48/48; 0.6b+hw 48/48; 

## False hotwords

### Moonshine base — 8

- spells: "spells" in "Where we actually are, and how the theories that people argue about in the journals and in the literature actually could be implemented in the world. if at all. And this spells over into normative arguments made by other scholars." (meant: "where we actually are and how the theories that people argue about in the journals and in the literature actually could be implemented in the world if at all and this spills over into normative arguments made by other scholars")
- AI: "AI" in "AI, can you remove the cooldown on all my spells? AI, can you remove the cooldown on all my spells?" (meant: "AI, can you remove the cooldown on all my spells?")
- cooldown: "cooldown" in "AI, can you remove the cooldown on all my spells? AI, can you remove the cooldown on all my spells?" (meant: "AI, can you remove the cooldown on all my spells?")
- spells: "spells" in "AI, can you remove the cooldown on all my spells? AI, can you remove the cooldown on all my spells?" (meant: "AI, can you remove the cooldown on all my spells?")
- AI: "AI" in "Hey AI, I want to soothe with fire on. Hey AI, I want to soothe with fire on." (meant: "Hey AI, I want a sword with fire on it.")
- brain: "brain" in "Brain, give me 64 hours. Brain, give me 64 hours. Brain, give me" (meant: "Brain, give me sixty four arrows.")
- cooldown: "cooldown" in "Who set the cooldown to ten seconds? Who set the cooldown to ten seconds?" (meant: "Can you set the cooldown to ten seconds?")
- freeze: "freeze" in "Freeze. Freeze. Freeze, it's not working." (meant: "Freeze! Freeze! It's not working.")

### Moonshine tiny — 0


### Parakeet 110M, greedy (0.3.4's default) — 0


### Parakeet 110M + hotwords — 4

- spells: "spells" in "where we actually are and how the theories that people argue about in the journals and in the literature actually could be implemented in the world if at all. And this spells over into normative arguments made by other scholars." (meant: "where we actually are and how the theories that people argue about in the journals and in the literature actually could be implemented in the world if at all and this spills over into normative arguments made by other scholars")
- freeze: "freeze" in "Set the time today. AI freeze." (meant: "Set the time to day, AI, please.")
- brain: "brain" in "Give Steve some brain AI." (meant: "Give Steve_123 some bread, AI.")
- cooldown: "cooldown" in "I think it might not have cooldown yet." (meant: "I think it might not have cooled down yet.")

### Parakeet 0.6B v2, greedy — 1

- spells: "spells" in "where we actually are and how the theories that people argue about in the journals and in the literature actually could be implemented in the world. if at all. And this spells over into normative arguments made by other scholars." (meant: "where we actually are and how the theories that people argue about in the journals and in the literature actually could be implemented in the world if at all and this spills over into normative arguments made by other scholars")

### Parakeet 0.6B v2 + hotwords (the mod's default) — 4

- spells: "spells" in "where we actually are and how the theories that people argue about in the journals and in the literature actually could be implemented in the world. if at all. And this spells over into normative arguments made by other scholars." (meant: "where we actually are and how the theories that people argue about in the journals and in the literature actually could be implemented in the world if at all and this spills over into normative arguments made by other scholars")
- brain: "brain" in "AI make it stop brain." (meant: "AI, make it stop raining.")
- brain: "brain" in "Okay brain, turn off the brain." (meant: "Okay brain, turn off the rain.")
- brain: "brain" in "Okay brain, turn off the brain." (meant: "Okay brain, turn off the rain.")

## The hotword lists

Parakeet 110M + hotwords:

- [Entry[text=AI, kind=AI], Entry[text=brain, kind=BRAIN], Entry[text=Alex, kind=NAME], Entry[text=Steve, kind=NAME], Entry[text=Steve JM, kind=NAME], Entry[text=fire ball, kind=PHRASE], Entry[text=frost nova, kind=PHRASE], Entry[text=blizzard aura, kind=PHRASE], Entry[text=ember shield, kind=PHRASE], Entry[text=destroy everything, kind=PHRASE], Entry[text=freeze, kind=PHRASE], Entry[text=thunder step, kind=PHRASE], Entry[text=ice aura, kind=PHRASE], Entry[text=unbind, kind=COMMAND], Entry[text=incantation, kind=COMMAND], Entry[text=incantations, kind=COMMAND], Entry[text=aura, kind=COMMAND], Entry[text=cooldown, kind=COMMAND], Entry[text=mobs, kind=COMMAND], Entry[text=spell, kind=COMMAND], Entry[text=spells, kind=COMMAND]]
- [Entry[text=AI, kind=AI], Entry[text=brain, kind=BRAIN], Entry[text=Dusk JR, kind=NAME], Entry[text=Mirelle, kind=NAME], Entry[text=Pyrothane, kind=NAME], Entry[text=Steve, kind=NAME], Entry[text=Tobble, kind=NAME], Entry[text=fire ball, kind=PHRASE], Entry[text=frost nova, kind=PHRASE], Entry[text=blizzard aura, kind=PHRASE], Entry[text=ember shield, kind=PHRASE], Entry[text=destroy everything, kind=PHRASE], Entry[text=freeze, kind=PHRASE], Entry[text=thunder step, kind=PHRASE], Entry[text=ice aura, kind=PHRASE], Entry[text=unbind, kind=COMMAND], Entry[text=incantation, kind=COMMAND], Entry[text=incantations, kind=COMMAND], Entry[text=aura, kind=COMMAND], Entry[text=cooldown, kind=COMMAND], Entry[text=mobs, kind=COMMAND], Entry[text=spell, kind=COMMAND], Entry[text=spells, kind=COMMAND]]

_Every utterance with errors is in the full report, `build/stt-compare/report.md`, after a run._
