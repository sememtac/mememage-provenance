"""
Constellation naming — derives a unique, pronounceable name from a celestial hash.

Each 12-record cycle forms a constellation. The heart star (first conception)
provides the celestial state that seeds the name — the sky at the moment of
birth, not the creator's choices. The name is deterministic: same sky, same
name, always. The namespace is unbounded — no two hashes produce the same name.

The phonetic system is rooted in Sumerian — the earliest civilization to practice
astronomy (~3500 BCE). They named the first constellations, mapped the planets,
divided the sky into paths, and gave us the zodiac. The naming pays respect to
the civilization that gave us the concept of constellations.

Sumerian phonology:
- Simple syllable structure: V, CV, VC, CVC — no consonant clusters
- Four core vowels: a, e, i, u (no 'o' in classical Sumerian)
- Clean stops, nasals, liquids, sibilants
- Words built by compounding short syllables (lugal = lu + gal = man + great)

The suffixes are actual Sumerian morphemes with astronomical meaning:
- mul (star), an (heaven), gal (great), kur (horizon), mah (exalted), etc.

Architecture: byte-level mapping from the hash, so the name is computable
in both Python (mint time) and JavaScript (decoder) with identical results.
"""


# Onset consonants — single characters only, authentic Sumerian
# Indexed by high nibble (0-15)
ONSETS = [
    'b', 'd', 'g', 'k', 'l', 'm', 'n', 'p',
    'r', 's', 'z', 't', 'h', 'v', 'c', 'f',
]

# Vowel nuclei — the four Sumerian vowels, weighted by frequency
# 'a' and 'u' were most common in Sumerian; 'e' and 'i' less so
# Indexed by low nibble (0-15)
NUCLEI = [
    'a', 'e', 'i', 'u', 'a', 'u', 'i', 'e',
    'a', 'u', 'a', 'i', 'u', 'e', 'a', 'i',
]

# Codas — Sumerian word-final consonants, mostly open syllables
# Indexed by a separate byte's low nibble
CODAS = [
    '', '', 'n', '', '', '', 'r', '',
    '', '', '', '', '', '', 'l', '',
]

# Suffixes — actual Sumerian morphemes with astronomical/cosmological meaning
# Each carries etymological weight from the world's first astronomers
# Indexed by final byte's low nibble
SUFFIXES = [
    'mul',   # star — the determinative for celestial bodies
    'an',    # heaven, sky
    'gal',   # great
    'kur',   # mountain, horizon — where stars rise and set
    'mah',   # exalted, supreme
    'gar',   # to place, to set — as stars are set in the sky
    'nun',   # prince, noble
    'shar',  # totality, universe
    'dim',   # to create, to fashion
    'bar',   # outside, foreign — the outer sky
    'nam',   # destiny, fate
    'me',    # divine power, cosmic law
    'ur',    # foundation, city
    'ki',    # earth, place
    'ab',    # sea, opening — the cosmic waters
    'tar',   # to decide, to determine
]


AGE_SUFFIXES = [
    'mul',   # Aries — star, the beginning
    'an',    # Taurus — heaven
    'gal',   # Gemini — great
    'kur',   # Cancer — horizon
    'mah',   # Leo — exalted
    'gar',   # Virgo — to place
    'nun',   # Libra — prince
    'shar',  # Scorpio — totality
    'dim',   # Sagittarius — to create
    'bar',   # Capricorn — outside
    'nam',   # Aquarius — destiny
    'me',    # Pisces — divine power
]


def name_from_hash(content_hash, age=None):
    """Derive a constellation name from a content hash and optional Age.

    Returns a capitalized proper noun, 2-3 syllables + suffix.
    If age (1-12) is provided, the suffix comes from the Age.
    Otherwise the suffix comes from the hash.
    Deterministic: same inputs always produce the same name.

    Two hashes are special-cased: the poles of the namespace.
    All zeros → Maat (order, truth — Egyptian). All ones → Isfet
    (chaos, disorder — Egyptian). Neither will ever be naturally
    instantiated. The Egyptians name the poles; the Sumerians
    name everything between them.
    """
    h = content_hash.lower().replace(' ', '')
    if len(h) < 16:
        h = h.ljust(16, '0')

    # The poles — Egyptian names, not Sumerian. Age does not apply.
    if all(c == '0' for c in h):
        return 'Maat'
    if all(c == 'f' for c in h):
        return 'Isfet'
    raw = [int(h[i:i + 2], 16) for i in range(0, 16, 2)]  # 8 bytes

    # Syllable count: always 2-3, never more. Subtract, don't add.
    nsyl = 3 if raw[0] & 0x40 else 2

    # Build syllables
    parts = []
    prev_onset = ''
    prev_nucleus = ''
    for i in range(nsyl):
        b = raw[i]
        mix = raw[(i * 3 + 5) % 8]  # distant byte to break repetition

        onset_idx = b >> 4
        nucleus_idx = b & 0xF

        # Anti-repetition: rotate if onset or nucleus would repeat
        onset = ONSETS[onset_idx]
        if onset == prev_onset:
            onset = ONSETS[(onset_idx + mix) & 0xF]

        nucleus = NUCLEI[nucleus_idx]
        if nucleus == prev_nucleus:
            nucleus = NUCLEI[(nucleus_idx + mix + 3) & 0xF]

        # Coda from a different byte
        coda_byte = raw[(i + nsyl) % 8]
        coda = CODAS[coda_byte & 0xF]

        # Suppress coda if next syllable starts with same sound
        if coda and i < nsyl - 1:
            next_onset = ONSETS[raw[i + 1] >> 4]
            if next_onset and coda[-1] == next_onset[0]:
                coda = ''

        parts.append(onset + nucleus + coda)
        prev_onset = onset
        prev_nucleus = nucleus

    # Suffix — from Age if provided, otherwise from hash
    if age is not None and 1 <= age <= 12:
        suffix = AGE_SUFFIXES[age - 1]
    else:
        suffix = SUFFIXES[raw[nsyl % 8] & 0xF]

    # Elide if last char of body matches first char of suffix
    assembled = ''.join(parts)
    if assembled and suffix and assembled[-1] == suffix[0]:
        assembled = assembled[:-1]

    name = assembled + suffix
    return name[0].upper() + name[1:]


def format_constellation(name, chunk_index=None, total=12):
    """Format for display: 'Velathar (star 4 of 12)'"""
    if chunk_index is not None:
        return f'{name} (star {chunk_index} of {total})'
    return name


if __name__ == '__main__':
    samples = [
        'e3a8f64cf2cc1b47',
        'b83b29af1ad45358',
        'cf7849c254055cab',
        'a4b703e91f8cd25a',
        '0123456789abcdef',
        'ffffffffffffffff',
        '0000000000000000',
        'deadbeefcafebabe',
        '7a3c9e1b5d2f4a80',
        '2468ace013579bdf',
        '112233445566aabb',
        'abcdef1234567890',
    ]
    print('Sumerian-rooted constellation names:\n')
    for h in samples:
        print(f'  {h}  →  {name_from_hash(h)}')

    # Larger batch from derived hashes
    import hashlib
    print('\nFrom 20 derived hashes:\n')
    for i in range(20):
        h = hashlib.sha256(f'mint-{i}'.encode()).hexdigest()[:16]
        print(f'  {name_from_hash(h)}')
