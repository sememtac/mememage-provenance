"""Mnemonic-style API tokens.

The ``MINT_API_TOKEN`` bearer that gates dashboard and admin
endpoints can be any opaque string — server-side auth is plain
string equality. Hex blobs work but read as intimidating noise to
non-technical users. A word-phrase token like
``vacant orbit beacon rust hammer drift quartz nimble silver clutch onyx tide``
carries comparable entropy (9 bits per word from a 512-word list ×
12 words = 108 bits, well above the threshold for an HTTPS bearer)
while being readable, dictate-able, and visually distinctive.

The token is stored verbatim — same string the user sees, same
string in ``.env``, same string in ``Authorization: Bearer …``. No
encoding round-trip at runtime. Old hex tokens keep working
unchanged because nothing in the auth path checks format.
"""

from __future__ import annotations

import secrets

from mememage.wordlist import WORDS


def generate_word_token(num_words: int = 12) -> str:
    """Return a concatenated string of ``num_words`` random words.

    No separator between words — pasting and URL-encoding stay clean
    (a space would render as ``+`` in a query string), and shells
    don't need quoting. 3-7 letter words from a 512-word list keep
    boundary parsing unambiguous in practice.

    Args:
        num_words: how many words. Default 12 = ~108 bits of entropy
            from the 512-word list. Increase for higher-stakes
            deployments (16 words = 144 bits, 20 = 180 bits, etc.).

    Each word is independently sampled via :mod:`secrets` so the
    sequence is cryptographically random — same source the rest of
    the codebase uses for identifier generation.
    """
    if num_words < 4:
        raise ValueError("num_words must be >= 4 (minimum 36 bits of entropy)")
    return "".join(secrets.choice(WORDS) for _ in range(num_words))


_WORDSET = set(WORDS)


def looks_like_word_token(token: str) -> bool:
    """Heuristic: does this string look like a generated word token?

    Accepts both forms:
      - new (no separator): ``boardflagwordtale…`` — greedy left-to-right
        parse into known words; True when the whole string consumes
        cleanly and yields at least 4 words.
      - legacy (space-separated): ``board flag word tale …`` — kept for
        the tokens generated before the no-separator switch.
    """
    if not token or not isinstance(token, str):
        return False
    # Legacy form (still in some .env files).
    if " " in token:
        parts = token.split()
        return len(parts) >= 4 and all(p in _WORDSET for p in parts)
    # No-separator form. Greedy max-length match at each position so
    # ambiguous suffixes (rare in the curated list) prefer the longer
    # word; fall back to shorter only if the longer doesn't lead to a
    # full parse — but with 3-7 letter words and no 2-letter outliers,
    # the greedy walk is sufficient.
    pos = 0
    count = 0
    max_len = max(len(w) for w in _WORDSET)
    while pos < len(token):
        matched = False
        for end in range(min(len(token), pos + max_len), pos + 2, -1):
            if token[pos:end] in _WORDSET:
                pos = end
                count += 1
                matched = True
                break
        if not matched:
            return False
    return count >= 4
