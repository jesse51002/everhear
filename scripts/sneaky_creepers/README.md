Makes creepers turn invisible near players but gives them a longer fuse and a loud hiss, until stopped.
Usage: sneaky_creepers [player=@a] [invisible_within=10] [hiss_within=4] [fuse=3]
Ends: when stopped

- player: near whom. invisible_within: creepers that close are invisible; hiss_within: that close they hiss loudly (0: never). All live.
- fuse: seconds (normally 1.5); it reaches each creeper once, when first seen, as the tag vfg_<instance>_fuse.

On stop: puts the fuse back to normal and removes the tag.
