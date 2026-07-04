"""
Song naming — derives a unique, deterministic song name from a star's identity.

Every conceived star gets a song. The song name is derived from the content hash
(the star's mathematical fingerprint). Same hash, same name, always. The naming
draws from three traditions:

- **Italian musical expression** — the universal language of scores. Tempo markings,
  dynamics, and character words that musicians worldwide understand.
- **Japanese aesthetic concepts** — wabi-sabi, mono no aware, yūgen. The beauty of
  impermanence, the profound grace of things. These words have no English equivalent.
- **Sanskrit/cosmic** — svara (tone), rasa (essence), akasha (ether). The oldest
  musical and philosophical vocabulary.

Combined with musical forms (nocturne, requiem, vespers) and cosmic objects
(nebula, corona, perihelion). The result reads like a classical composition title
that happens to describe what a lonely star sounds like.

Architecture: byte-level mapping from the hash, computable in both Python (mint
time) and JavaScript (decoder) with identical results. The seed is offset from
the constellation naming seed so the two never correlate.

The song name is NOT in _HASH_INCLUDED — it's derived from the content hash,
so including it would be circular. Like constellation names, it's supplementary
metadata that enriches but doesn't define.
"""

import hashlib

# ═══════════════════════════════════════════════════════════
#  WORD POOLS
#  Each pool is indexed by hash bytes. The pools are ordered
#  identically in Python and JavaScript — do not reorder.
# ═══════════════════════════════════════════════════════════

# First word: character/mood
WORD_A = [
    # Italian expression (the universal language of music)
    "Adagio", "Lento", "Grave", "Largo", "Sereno",
    "Dolce", "Morendo", "Lacrimosa", "Luminoso", "Celeste",
    "Perduto", "Eterno", "Sospiro", "Profondo", "Remoto",
    "Errante",
    # Japanese aesthetic concepts
    "Yūgen", "Komorebi", "Mono", "Mugen", "Shinrin",
    "Ukiyo", "Aware", "Nagare", "Hotaru", "Kasumi",
    # Sanskrit/cosmic
    "Svara", "Rasa", "Dhyana", "Akasha", "Bindu",
    "Prana",
]

# Second word: form/substance
WORD_B = [
    # Musical forms
    "Nocturne", "Requiem", "Vespers", "Litany", "Canticle",
    "Hymnal", "Aubade", "Elegy", "Threnody", "Chorale",
    "Reverie", "Sarabande",
    # Cosmic objects/phenomena
    "Nebula", "Corona", "Perihelion", "Apogee", "Meridian",
    "Eclipse", "Solstice", "Zenith", "Parallax", "Umbra",
    "Liminal", "Penumbra",
]

# Optional third fragment: qualifier (empty strings = no qualifier)
WORD_C = [
    "", "", "", "", "",  # ~31% chance of no qualifier
    "in Deep Water", "at the Threshold", "for the Unheard",
    "of First Light", "beyond the Veil", "in Amber",
    "at Perihelion", "of Distant Fire", "for the Departed",
    "in Still Air", "of Frozen Light",
]


def name_from_hash(content_hash: str) -> str:
    """Derive a song name from a content hash.

    Deterministic: same hash always produces the same name.
    Uses a SHA-256 sub-hash with a fixed salt to avoid correlation
    with constellation naming (which uses the constellation_hash).

    Args:
        content_hash: The 16-hex-char content hash of the record.

    Returns:
        A song name like "Morendo Nocturne" or
        "Yūgen Perihelion at the Threshold".
    """
    # Sub-hash with salt — prevents correlation with other hash-derived names
    sub = hashlib.sha256(
        (content_hash + ":song").encode()
    ).digest()

    # Byte 0 → first word
    a = WORD_A[sub[0] % len(WORD_A)]
    # Byte 1 → second word
    b = WORD_B[sub[1] % len(WORD_B)]
    # Byte 2 → optional qualifier
    c = WORD_C[sub[2] % len(WORD_C)]

    if c:
        return f"{a} {b} {c}"
    return f"{a} {b}"
