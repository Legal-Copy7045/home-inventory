"""Forgiving word search, the same rules as the web app's ``match.ts``.

Every word typed must match some word of the text, in any order: a word that
starts with it, or one with the same stem (plurals; or a different ending
after at least four shared letters, so "shelf" finds "Shelving").
"""

from __future__ import annotations

import re
import unicodedata

_SPLIT = re.compile(r"[^a-z0-9#]+")


def words(s: str) -> list[str]:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return [w for w in _SPLIT.split(s) if w]


def stem(w: str) -> str:
    if len(w) > 4 and w.endswith("ves"):
        return w[:-3] + "f"
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and re.search(r"(ss|x|ch|sh)es$", w):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not re.search(r"(ss|us|is)$", w):
        return w[:-1]
    return w


def _common(a: str, b: str) -> int:
    i = 0
    while i < len(a) and i < len(b) and a[i] == b[i]:
        i += 1
    return i


def word_matches(q: str, t: str) -> bool:
    if t.startswith(q):
        return True
    sq, st = stem(q), stem(t)
    if st.startswith(sq) or sq == st:
        return True
    c = _common(sq, st)
    return c >= 4 and c >= min(len(sq), len(st)) - 1


def matches_words(query: str, text: str) -> bool:
    qs = words(query)
    if not qs:
        return True
    ts = words(text)
    return all(any(word_matches(q, t) for t in ts) for q in qs)
