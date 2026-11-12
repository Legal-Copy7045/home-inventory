"""Comparing item names for voice (no Home Assistant imports; pure and fast).

``normalize`` turns a name into comparable words:

* lower case, accents and punctuation removed, apostrophes dropped
  ("Pat's" -> "pat"), "&" -> "and", leading "the / my / a / an" ignored;
* measurements made uniform: "50 ft", "50-foot", "50'" -> "50ft";
  "18 volt" -> "18v"; "2.5 mm" -> "2.5mm" (different sizes stay different);
* hyphenated words split ("self-propelled" -> "self propelled"), but a
  hyphen next to a digit joins ("WD-40" -> "wd40", "HRX-217" -> "hrx217");
* plurals made singular by explicit rules - never a blanket "drop the s":
  irregular forms (shelves, knives, feet...), "-ies" -> "-y",
  "-sses/-xes/-ches/-shes/-zzes" -> drop "es", and a plain final "s" only
  when the word doesn't end in ss/us/is/ous.  Words that are singular
  though they end in "s" (glass, compass, status, chassis, lens, gas...)
  and words that are only ever plural (pliers, scissors, glasses...) are
  left alone, so "Safety glasses" never becomes "glass".  Words with
  digits (model numbers) are never changed.

``key`` / ``same_thing`` compare two names: equal words, or - for names
with numbers - equal once spaces are ignored ("HRX 217" == "HRX217").
"""

from __future__ import annotations

import re
import unicodedata

_ARTICLES = {"the", "my", "a", "an", "our", "your"}

# Always plural, or plural forms that mean something else than the singular.
_INVARIANT = {
    "pliers", "scissors", "tongs", "tweezers", "shears", "clippers", "loppers", "secateurs", "snips",
    "nippers", "cutters", "binoculars", "goggles", "glasses", "spectacles", "headphones", "earphones",
    "earbuds", "jeans", "trousers", "shorts", "pants", "overalls", "pyjamas", "pajamas", "leggings",
    "tights", "suspenders", "braces", "clothes", "news", "series", "species", "means", "corps",
    "chassis", "lens", "gas", "bus", "plus", "bias", "canvas", "atlas", "iris", "christmas", "physics",
    "electronics", "mathematics", "athletics", "gymnastics", "billiards", "outdoors", "odds", "ends", "thanks", "premises", "headquarters", "always", "towards",
    "perhaps", "this", "his", "is", "was", "has", "does", "yes", "us", "its",
}
_IRREGULAR = {
    "children": "child", "men": "man", "women": "woman", "people": "person", "feet": "foot", "teeth": "tooth",
    "mice": "mouse", "geese": "goose", "dice": "die", "knives": "knife", "wives": "wife", "lives": "life",
    "leaves": "leaf", "shelves": "shelf", "halves": "half", "calves": "calf", "loaves": "loaf", "wolves": "wolf",
    "scarves": "scarf", "hooves": "hoof", "thieves": "thief", "potatoes": "potato", "tomatoes": "tomato",
    "heroes": "hero", "echoes": "echo", "volcanoes": "volcano", "batteries": "battery", "indices": "index",
    "appendices": "appendix", "matrices": "matrix", "vertices": "vertex", "criteria": "criterion",
    "cacti": "cactus", "fungi": "fungus", "radii": "radius", "axes": "axe", "gases": "gas", "buses": "bus",
    "lenses": "lens", "irises": "iris", "atlases": "atlas", "canvases": "canvas", "quizzes": "quiz",
}
# Singulars ending in "-ie" / "-che" (so "movies" -> "movie", not "movy").
_IE = {"movie", "cookie", "zombie", "hoodie", "beanie", "onesie", "selfie", "brownie", "rookie", "pixie",
       "talkie", "necktie", "bootie", "goalie", "freebie", "budgie", "calorie", "prairie", "nightie", "smoothie"}
_CHE = {"cache", "niche", "headache", "moustache", "mustache", "avalanche", "quiche", "cliche", "microfiche"}
# Units after a number, in all their spellings -> one short form.
_UNITS = {
    "ft": "ft", "foot": "ft", "feet": "ft", "'": "ft",
    "in": "in", "inch": "in", "inches": "in", '"': "in",
    "mm": "mm", "cm": "cm", "m": "m", "meter": "m", "meters": "m", "metre": "m", "metres": "m",
    "km": "km", "v": "v", "volt": "v", "volts": "v", "w": "w", "watt": "w", "watts": "w", "kw": "kw",
    "a": "a", "amp": "a", "amps": "a", "ah": "ah", "mah": "mah",
    "l": "l", "litre": "l", "litres": "l", "liter": "l", "liters": "l", "ml": "ml",
    "gal": "gal", "gallon": "gal", "gallons": "gal", "qt": "qt", "quart": "qt", "quarts": "qt",
    "oz": "oz", "ounce": "oz", "ounces": "oz", "lb": "lb", "lbs": "lb", "pound": "lb", "pounds": "lb",
    "kg": "kg", "g": "g", "gram": "g", "grams": "g", "psi": "psi", "nm": "nm", "hp": "hp", "cc": "cc",
    "pk": "pk", "pack": "pk", "pc": "pc", "pcs": "pc", "piece": "pc", "pieces": "pc",
}
_NUM = r"\d+(?:[.,/]\d+)?"
# After a space these are ordinary words too ("Box 3 in shed", "2 a day"),
# so "3 in" is only a size when written together ("3in").
_SPACED_UNIT_WORDS = {"in", "a", "m", "g", "l", "w", "v"}


def singular(word: str) -> str:
    """One word made singular by explicit rules (see module doc)."""
    if any(c.isdigit() for c in word) or len(word) <= 3 or word in _INVARIANT:
        return word
    if word in _IRREGULAR:
        return _IRREGULAR[word]
    if word.endswith("ies") and len(word) > 4:
        return word[:-1] if word[:-1] in _IE else word[:-3] + "y"
    if word.endswith("ches") and word[:-1] in _CHE:
        return word[:-1]
    if word.endswith(("sses", "xes", "ches", "shes", "zzes")):
        return word[:-2]
    if word.endswith(("ss", "us", "is", "ous", "ics")):
        return word
    if word.endswith("s"):
        return word[:-1]
    return word


def _ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def normalize(text: str) -> str:
    s = _ascii((text or "").replace("\u2019", "'").replace("\u2018", "'")).lower()
    s = s.replace("&", " and ")
    s = re.sub(r"(\w)'s\b", r"\1", s)                      # possessive: pat's -> pat
    s = re.sub(rf"({_NUM})\s*(['\"])", lambda m: f"{m.group(1)}{_UNITS[m.group(2)]} ", s)  # 50' 3"
    s = s.replace("'", "")                                   # o'brien -> obrien
    s = re.sub(r"(?<=\d)-(?=[a-z])|(?<=[a-z])-(?=\d)|(?<=\d)-(?=\d)", "", s)  # wd-40, hrx-217, 50-ft
    s = re.sub(r"[^a-z0-9.,/]+", " ", s)                     # other punctuation and hyphens: word breaks
    s = re.sub(r"(?<!\d)[.,/]|[.,/](?!\d)", " ", s)          # keep 2.5 and 1/2, drop other dots/slashes
    words = s.split()
    out: list[str] = []
    i = 0
    while i < len(words):
        w = words[i]
        m = re.fullmatch(rf"({_NUM})([a-z]+)", w)
        if m and m.group(2) in _UNITS:                       # 50ft, 18volt
            out.append(m.group(1) + _UNITS[m.group(2)])
        elif re.fullmatch(_NUM, w) and i + 1 < len(words) and words[i + 1] in _UNITS \
                and words[i + 1] not in _SPACED_UNIT_WORDS:  # 50 ft
            out.append(w + _UNITS[words[i + 1]])
            i += 1
        else:
            out.append(singular(w))
        i += 1
    while len(out) > 1 and out[0] in _ARTICLES:
        out.pop(0)
    return " ".join(out)


def key(text: str) -> str:
    """What two names of the same thing share: the normalized words, with
    spaces ignored when there's a number in it ("HRX 217" == "HRX217")."""
    n = normalize(text)
    return n.replace(" ", "") if any(c.isdigit() for c in n) else n


def same_thing(a: str, b: str) -> bool:
    return key(a) == key(b)
