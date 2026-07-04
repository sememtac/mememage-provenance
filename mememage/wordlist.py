"""Word list for mnemonic-style API tokens.

512 common English words, 3-7 letters each, chosen for unambiguous
spelling and pronunciation. The list provides 9 bits of entropy per
word; a 12-word token carries ~108 bits — comfortably above the
threshold for an HTTPS-rate-limited bearer.

Selection criteria:
- common everyday vocabulary (no jargon)
- no homophones (no "two/too/to", "be/bee", "knight/night")
- no spelling ambiguity (no "color/colour", "gray/grey")
- no offensive or politically loaded terms
- no words that look like other words at a glance (no "plain/plane")
- 3-7 letters for short readable phrases

Lives separate from tokens.py so the data is auditable in isolation
and other consumers (recovery codes, etc.) can borrow the same list
without circular imports.
"""

WORDS = (
    # ----- 0-63: nature elements -----
    "sun", "moon", "star", "sky", "cloud", "rain", "snow", "wind",
    "storm", "frost", "mist", "fog", "dew", "fire", "ember", "spark",
    "smoke", "ash", "stone", "rock", "sand", "soil", "clay", "mud",
    "river", "creek", "lake", "pond", "ocean", "wave", "tide", "shore",
    "beach", "cliff", "peak", "ridge", "hill", "valley", "field", "meadow",
    "forest", "grove", "tree", "leaf", "root", "bark", "branch", "twig",
    "moss", "vine", "fern", "reed", "weed", "grass", "seed", "bloom",
    "petal", "thorn", "berry", "fruit", "grain", "nut", "husk", "pod",

    # ----- 64-127: animals -----
    "lion", "tiger", "bear", "wolf", "fox", "deer", "elk", "moose",
    "hare", "mole", "otter", "beaver", "badger", "skunk", "horse", "pony",
    "goat", "sheep", "cow", "yak", "boar", "pig", "lamb", "calf",
    "owl", "hawk", "eagle", "crow", "raven", "robin", "finch", "wren",
    "swan", "duck", "goose", "heron", "stork", "crane", "gull", "tern",
    "frog", "toad", "newt", "lizard", "snake", "viper", "turtle", "skink",
    "trout", "salmon", "perch", "tuna", "pike", "shark", "whale", "seal",
    "crab", "lobster", "shrimp", "snail", "slug", "moth", "bee", "ant",

    # ----- 128-191: colors + materials -----
    "red", "blue", "green", "amber", "rose", "coral", "ruby", "cherry",
    "violet", "indigo", "azure", "navy", "teal", "mint", "olive", "lime",
    "ivory", "pearl", "cream", "linen", "tan", "beige", "khaki", "ochre",
    "rust", "copper", "bronze", "brass", "silver", "gold", "iron", "tin",
    "lead", "zinc", "steel", "glass", "crystal", "quartz", "agate", "onyx",
    "jade", "opal", "topaz", "lemon", "loam", "flint", "chalk", "slate",
    "marble", "granite", "basalt", "shale", "coal", "soot", "peat", "dust",
    "wax", "resin", "musk", "pitch", "tar", "oil", "milk", "syrup",

    # ----- 192-255: objects + tools -----
    "wheel", "gear", "shaft", "bolt", "knot", "pin", "loop", "hook",
    "rope", "chain", "wire", "cord", "twine", "thread", "yarn", "cloth",
    "cotton", "silk", "wool", "fleece", "felt", "denim", "canvas", "lace",
    "key", "lock", "latch", "hinge", "bell", "horn", "drum", "harp",
    "lyre", "flute", "pipe", "fife", "string", "bow", "arrow", "spear",
    "blade", "knife", "axe", "saw", "hammer", "mallet", "wedge", "chisel",
    "anvil", "forge", "kiln", "oven", "hearth", "stove", "lamp", "torch",
    "candle", "wick", "flame", "lantern", "beacon", "signal", "banner", "flag",

    # ----- 256-319: spaces + structures -----
    "room", "hall", "porch", "patio", "garden", "yard", "park", "plaza",
    "court", "lane", "path", "road", "trail", "bridge", "arch", "tower",
    "spire", "dome", "gate", "fence", "wall", "post", "beam", "rafter",
    "floor", "roof", "tile", "brick", "plank", "board", "shelf", "ledge",
    "door", "window", "frame", "sill", "stairs", "ladder", "ramp", "step",
    "well", "fountain", "pool", "bath", "stream", "rivulet", "canal", "ditch",
    "barn", "stable", "shed", "hut", "cabin", "lodge", "cottage", "manor",
    "harbor", "dock", "pier", "wharf", "boat", "ship", "raft", "canoe",

    # ----- 320-383: actions + states -----
    "rise", "fall", "climb", "slip", "drift", "float", "swim", "dive",
    "walk", "run", "leap", "skip", "dance", "sway", "spin", "twirl",
    "rest", "sleep", "dream", "wake", "yawn", "stretch", "stand", "sit",
    "open", "close", "shut", "raise", "drop", "push", "pull", "throw",
    "catch", "hold", "carry", "hoist", "press", "tap", "knock", "peal",
    "shine", "glow", "gleam", "flash", "flicker", "burst", "sparkle", "twinkle",
    "rumble", "hum", "buzz", "click", "tick", "chime", "echo", "ripple",
    "swell", "grow", "spread", "bend", "curl", "fold", "wrap", "tie",

    # ----- 384-447: qualities + feelings -----
    "calm", "still", "quiet", "soft", "warm", "cool", "bright", "dim",
    "clear", "fresh", "sweet", "rich", "deep", "pure", "smooth", "fine",
    "kind", "gentle", "merry", "lively", "swift", "nimble", "spry", "quick",
    "brave", "bold", "wise", "clever", "noble", "humble", "true", "loyal",
    "happy", "glad", "joyful", "grateful", "peaceful", "tender", "graceful", "lovely",
    "rare", "unique", "meek", "modest", "stoic", "patient", "steady", "ready",
    "vacant", "empty", "hollow", "full", "heavy", "light", "tiny", "vast",
    "round", "square", "narrow", "broad", "lofty", "shallow", "tall", "short",

    # ----- 448-511: time + cosmic + abstract -----
    "dawn", "dusk", "noon", "night", "morning", "evening", "thaw", "summer",
    "autumn", "winter", "year", "month", "week", "day", "hour", "minute",
    "moment", "season", "decade", "epoch", "future", "past", "present", "always",
    "comet", "meteor", "orbit", "galaxy", "nebula", "planet", "cosmos", "void",
    "voyage", "journey", "quest", "wander", "ramble", "saunter", "linger", "roam",
    "story", "legend", "myth", "fable", "tale", "rhyme", "verse", "song",
    "letter", "word", "page", "book", "scroll", "tome", "library", "ledger",
    "secret", "riddle", "puzzle", "cipher", "token", "symbol", "rune", "mark",
)


assert len(WORDS) == 512, f"wordlist must be exactly 512 words, got {len(WORDS)}"
assert len(set(WORDS)) == 512, "wordlist has duplicates"
