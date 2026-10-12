"""Makes Vosk's filler list, the common English words its grammar holds beside a player's phrases:
src/main/resources/com/voiceforgaming/vfg/kws/vosk-fillers.txt.

    uv run --with vosk tools/vosk_fillers.py --model <unpacked vosk-model-small-en-us-0.15> [--out FILE] [--n 5000]

The words are counted in Mozilla Common Voice's English sentences, which are CC0-1.0 (public domain):
server/data/en/sentence-collector.txt (61,513 sentences people wrote or took from public-domain books) and
server/data/en/wiki.en.txt (1,302,301 sentences from Wikipedia), from the common-voice repository at a pinned
commit, each checked by SHA-256. A word's score is its mean share of the words of each file, so the short,
conversational file counts as much as the long one. Only lower-case words of letters that the Vosk model knows
are kept (its own word list), most common first, until there are N. Then EVERYDAY, words of everyday speech and of
Minecraft that written sentences seldom hold ("yeah", "gonna", "i'm", "creeper"), picked by hand for the mod, are
added where the model knows them. The counted part is CC0 like its source; the whole list ships with the mod under
the repo's MIT license.
"""
import argparse, collections, hashlib, pathlib, re, sys, urllib.request

COMMIT = "a15d5c520200d0369cd9f5adf17fcbb4e54847b7"
BASE = f"https://raw.githubusercontent.com/common-voice/common-voice/{COMMIT}/server/data/en/"
SOURCES = {
    "sentence-collector.txt": "31ac8e200449ebd7aee4a8c6bd16d4dafd076cf11f7c3efe1035f0283712d136",
    "wiki.en.txt": "438e55e8bc91dfa6cbe098a7233702c6b177c5ae07137a01d66b9c75c9789ae5",
}
WORD = re.compile(r"[a-z]+(?:'[a-z]+)*")
# Everyday speech and Minecraft, which Common Voice's written sentences seldom hold; picked by hand for the mod.
EVERYDAY = """
yeah yep yup nope nah okay ok alright hey hi hello bye thanks um uh hmm huh oh ah aw oops wow whoa haha lol
gonna wanna gotta kinda sorta dude bro guys guy man buddy cool nice awesome sweet damn dang shoot crap sure maybe please sorry
wait stop go come look watch quick hurry run jump help
i'm don't it's that's can't you're let's i'll we're what's there's didn't isn't won't i've he's she's they're doesn't
wasn't couldn't wouldn't shouldn't we'll you'll i'd you've we've aren't haven't
minecraft creeper creepers zombie zombies skeleton skeletons spider spiders enderman endermen villager villagers pillager
witch slime ghast blaze wither dragon nether overworld portal diamond diamonds emerald emeralds iron gold netherite redstone
cobblestone stone wood planks pickaxe axe shovel sword bow arrow arrows shield armor helmet chestplate boots torch torches
chest furnace crafting craft mine mining build base house village cave lava water bucket bed spawn respawn teleport server
lag inventory potion enchant enchanted food hungry health hearts wheat farm cow pig sheep chicken horse wolf dog cat
""".split()
HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE.parent / "src/main/resources/com/voiceforgaming/vfg/kws/vosk-fillers.txt"


def fetch(name, sha, cache):
    path = cache / name
    if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != sha:
        print(f"downloading {BASE + name}", file=sys.stderr)
        data = urllib.request.urlopen(BASE + name, timeout=60).read()
        if hashlib.sha256(data).hexdigest() != sha:
            sys.exit(f"{name}: wrong SHA-256")
        path.write_bytes(data)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="the unpacked vosk-model-small-en-us-0.15 folder")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--n", type=int, default=5000)
    ap.add_argument("--cache", default=str(HERE.parent / "build/vosk-fillers"))
    a = ap.parse_args()
    cache = pathlib.Path(a.cache)
    cache.mkdir(parents=True, exist_ok=True)
    score = collections.Counter()
    for name, sha in SOURCES.items():
        counts = collections.Counter()
        with open(fetch(name, sha, cache), encoding="utf-8") as f:
            for line in f:
                counts.update(WORD.findall(line.lower().replace("’", "'")))
        total = sum(counts.values())
        for w, c in counts.items():
            score[w] += c / total / len(SOURCES)
    import vosk
    vosk.SetLogLevel(-1)
    model = vosk.Model(a.model)
    keep = []
    for w in sorted(score, key=lambda w: -score[w]):
        if w.isalpha() and model.vosk_model_find_word(w) >= 0:
            keep.append(w)
            if len(keep) == a.n:
                break
    counted = len(keep)
    taken = set(keep)
    for w in EVERYDAY:
        if w not in taken and model.vosk_model_find_word(w) >= 0:
            keep.append(w)
            taken.add(w)
    header = [
        f"# Common English words Vosk's small US English model knows: Vosk's grammar holds them beside the player's",
        "# phrases, so that ordinary speech has somewhere else to go. Made by tools/vosk_fillers.py: first the",
        f"# {counted} commonest, most common first, counted in Mozilla Common Voice's English sentences (CC0-1.0:",
        "# sentence-collector.txt and wiki.en.txt, github.com/common-voice/common-voice at",
        f"# {COMMIT}); then {len(keep) - counted} words of everyday speech and Minecraft picked by hand.",
    ]
    pathlib.Path(a.out).write_text("\n".join(header + keep) + "\n", encoding="utf-8")
    print(f"{len(keep)} words to {a.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
