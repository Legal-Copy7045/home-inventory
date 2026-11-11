"""GTIN (UPC-A, UPC-E, EAN-13, EAN-8, GTIN-14) validation and normalisation.

All product barcodes are stored twice: the raw value as scanned, and a
normalised 14-digit GTIN used for exact matching, so that a UPC-A label and
the same product's EAN-13 (leading zero) or UPC-E (compressed) label match.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_DIGITS = re.compile(r"^\d+$")


def check_digit(body: str) -> int:
    """GS1 mod-10 check digit for the digits *before* the check digit."""
    total = 0
    for i, ch in enumerate(reversed(body)):
        n = int(ch)
        total += n * 3 if i % 2 == 0 else n
    return (10 - total % 10) % 10


def is_valid_gs1(code: str) -> bool:
    return bool(_DIGITS.match(code)) and len(code) >= 2 and check_digit(code[:-1]) == int(code[-1])


def upce_to_upca(upce: str) -> str | None:
    """Expand an 8-digit (or 6/7 digit) UPC-E code to 12-digit UPC-A."""
    if not _DIGITS.match(upce):
        return None
    if len(upce) == 6:
        ns, body, chk = "0", upce, None
    elif len(upce) == 7:
        ns, body, chk = upce[0], upce[1:], None
    elif len(upce) == 8:
        ns, body, chk = upce[0], upce[1:7], upce[7]
    else:
        return None
    if ns not in "01":
        return None
    d1, d2, d3, d4, d5, d6 = body
    if d6 in "012":
        manufacturer, product = f"{d1}{d2}{d6}00", f"00{d3}{d4}{d5}"
    elif d6 == "3":
        manufacturer, product = f"{d1}{d2}{d3}00", f"000{d4}{d5}"
    elif d6 == "4":
        manufacturer, product = f"{d1}{d2}{d3}{d4}0", f"0000{d5}"
    else:
        manufacturer, product = f"{d1}{d2}{d3}{d4}{d5}", f"0000{d6}"
    body11 = ns + manufacturer + product
    computed = str(check_digit(body11))
    if chk is not None and chk != computed:
        return None
    return body11 + computed


@dataclass(frozen=True)
class Barcode:
    raw: str
    symbology: str  # upc_a | upc_e | ean_13 | ean_8 | gtin_14 | other
    gtin14: str | None  # None when not a valid GTIN
    valid: bool

    @property
    def display(self) -> str:
        if self.gtin14 is None:
            return self.raw
        if self.symbology == "ean_8":
            return self.gtin14[-8:]
        stripped = self.gtin14.lstrip("0")
        if len(stripped) <= 12:
            return self.gtin14[-12:]
        return self.gtin14[-13:] if self.gtin14[0] == "0" else self.gtin14

    @property
    def lookup_codes(self) -> list[str]:
        """Forms to try against external catalogs (EAN-13 first, then UPC-A)."""
        if self.gtin14 is None:
            return [self.raw]
        codes = []
        if self.symbology == "ean_8":
            codes.append(self.gtin14[-8:])
        codes.append(self.gtin14[-13:])
        if self.gtin14[:2] == "00":
            codes.append(self.gtin14[-12:])
        if self.gtin14[0] != "0":
            codes.append(self.gtin14)
        seen: list[str] = []
        for c in codes:
            if c not in seen:
                seen.append(c)
        return seen


def parse(value: str, hint: str | None = None) -> Barcode:
    """Parse a scanned or typed barcode.

    ``hint`` is the symbology reported by the scanner (e.g. ``upc_e``); it
    resolves the ambiguity between 8-digit EAN-8 and UPC-E codes.
    """
    raw = re.sub(r"[\s-]", "", (value or "").strip())
    if not _DIGITS.match(raw):
        return Barcode(raw=raw, symbology="other", gtin14=None, valid=bool(raw))
    n = len(raw)
    if hint and hint.lower().replace("-", "_") == "upc_e" and n in (6, 7, 8):
        upca = upce_to_upca(raw)
        if upca:
            return Barcode(raw=raw, symbology="upc_e", gtin14=upca.zfill(14), valid=True)
    if n == 8 and raw[0] in "01" and not is_valid_gs1(raw):
        # Could still be UPC-E
        upca = upce_to_upca(raw)
        if upca:
            return Barcode(raw=raw, symbology="upc_e", gtin14=upca.zfill(14), valid=True)
    if n == 8 and is_valid_gs1(raw):
        # Valid EAN-8; but an 8-digit code starting 0/1 may be UPC-E too.
        # EAN-8 takes precedence when its check digit validates.
        return Barcode(raw=raw, symbology="ean_8", gtin14=raw.zfill(14), valid=True)
    if n in (6, 7):
        upca = upce_to_upca(raw)
        if upca:
            return Barcode(raw=raw, symbology="upc_e", gtin14=upca.zfill(14), valid=True)
    if n == 12 and is_valid_gs1(raw):
        return Barcode(raw=raw, symbology="upc_a", gtin14=raw.zfill(14), valid=True)
    if n == 13 and is_valid_gs1(raw):
        return Barcode(raw=raw, symbology="ean_13", gtin14=raw.zfill(14), valid=True)
    if n == 14 and is_valid_gs1(raw):
        return Barcode(raw=raw, symbology="gtin_14", gtin14=raw, valid=True)
    if n == 11:
        # UPC-A typed without its check digit
        full = raw + str(check_digit(raw))
        return Barcode(raw=raw, symbology="upc_a", gtin14=full.zfill(14), valid=True)
    return Barcode(raw=raw, symbology="other", gtin14=None, valid=True)


def normalise(value: str | None, hint: str | None = None) -> str | None:
    if not value:
        return None
    return parse(value, hint).gtin14
