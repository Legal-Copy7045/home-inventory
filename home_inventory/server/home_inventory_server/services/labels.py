"""QR codes and printable PDF labels.

Every container and location has a permanent ``qr_code`` (8 characters,
unambiguous alphabet).  The QR payload is a link when the Home Assistant
address is known:

    https://<home assistant>/<app panel>/c/<code>   (container)
    https://<home assistant>/<app panel>/l/<code>   (location)

Scanning it with a phone's camera opens Home Assistant, which passes the
``/c/<code>`` tail to the App, and the App shows that container or location.
The base address comes from Settings, or else from the address the person
printing the label used to open Home Assistant.  Without either, the payload
is ``HINV:C:<code>`` / ``HINV:L:<code>``, which only the in-app scanner reads.

The scanner accepts all forms (including the older ``?c=<code>`` links), so
labels stay valid if the address changes.  The payload never contains names,
numbers or locations, which may change.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import segno
from reportlab.lib.pagesizes import A4, LETTER
from reportlab.lib.units import inch, mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

_CODE = r"([A-HJ-NP-Z2-9]{6,16})"
QR_PATTERNS = [
    re.compile(r"HINV[:/\-]([CL])[:/\-]" + _CODE, re.I),
    re.compile(r"/([cl])/" + _CODE + r"(?=$|[/?#])", re.I),
    re.compile(r"[?&#]([cl])=" + _CODE + r"\b", re.I),
]
KIND_NAMES = {"C": "container", "L": "location"}


def clean_base_url(base: str | None) -> str | None:
    if not base:
        return None
    base = base.strip().rstrip("/")
    if not re.match(r"^https?://[^\s/?#]+(/[^\s?#]*)?$", base, re.I) or len(base) > 500:
        return None
    return base


def qr_payload(code: str, base_url: str | None, kind: str = "C") -> str:
    kind = kind.upper()
    base = clean_base_url(base_url)
    if base:
        return f"{base}/{kind.lower()}/{code}"
    return f"HINV:{kind}:{code}"


def parse_qr_ref(text: str) -> tuple[str | None, str] | None:
    """Return (kind, code) where kind is "C", "L" or None (bare code)."""
    text = (text or "").strip()
    for pat in QR_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(1).upper(), m.group(2).upper()
    if re.fullmatch(r"[A-HJ-NP-Z2-9]{8}", text.upper()):
        return None, text.upper()
    return None


def parse_qr(text: str) -> str | None:
    """Container code from a label payload (or a bare code)."""
    ref = parse_qr_ref(text)
    if ref and ref[0] in (None, "C"):
        return ref[1]
    return None


def qr_svg(payload: str, scale: int = 6) -> bytes:
    buf = io.BytesIO()
    segno.make(payload, error="m").save(buf, kind="svg", scale=scale, border=2, xmldecl=False)
    return buf.getvalue()


def qr_png(payload: str, scale: int = 10) -> bytes:
    buf = io.BytesIO()
    segno.make(payload, error="m").save(buf, kind="png", scale=scale, border=2)
    return buf.getvalue()


@dataclass(frozen=True)
class Template:
    key: str
    name: str
    page: tuple[float, float]
    cols: int
    rows: int
    width: float
    height: float
    margin_left: float
    margin_top: float
    gap_x: float
    gap_y: float


TEMPLATES = {
    t.key: t
    for t in [
        # A4 sheets (UK/EU)
        Template("avery_l7160", "Avery L7160 – 21 per A4 (63.5×38.1 mm)", A4, 3, 7, 63.5 * mm, 38.1 * mm, 7.2 * mm, 15.1 * mm, 2.5 * mm, 0),
        Template("avery_l7163", "Avery L7163 – 14 per A4 (99.1×38.1 mm)", A4, 2, 7, 99.1 * mm, 38.1 * mm, 4.65 * mm, 15.15 * mm, 2.5 * mm, 0),
        Template("avery_l7165", "Avery L7165 – 8 per A4 (99.1×67.7 mm)", A4, 2, 4, 99.1 * mm, 67.7 * mm, 4.65 * mm, 13.1 * mm, 2.5 * mm, 0),
        # US Letter sheets
        # US Letter sheets (Avery's published dimensions).  Product numbers in
        # each name share one layout, e.g. 5523 WeatherProof = 5163 paper.
        Template("avery_5160", "Avery 5160 / 5520 WeatherProof / 8160 / 5960 – 30 per sheet (1×2⅝ in)",
                 LETTER, 3, 10, 2.625 * inch, 1 * inch, 0.1875 * inch, 0.5 * inch, 0.125 * inch, 0),
        Template("avery_5163", "Avery 5163 / 5523 WeatherProof / 8163 / 5963 – 10 per sheet (2×4 in)",
                 LETTER, 2, 5, 4 * inch, 2 * inch, 0.15625 * inch, 0.5 * inch, 0.1875 * inch, 0),
        Template("avery_5164", "Avery 5164 / 5524 WeatherProof / 8164 – 6 per sheet (3⅓×4 in)",
                 LETTER, 2, 3, 4 * inch, (10 / 3) * inch, 0.15625 * inch, 0.5 * inch, 0.1875 * inch, 0),
        Template("avery_22806", "Avery 22806 – 12 per Letter (2×2 in square)", LETTER, 3, 4, 50.8 * mm, 50.8 * mm, 15.9 * mm, 15.9 * mm, 15.9 * mm, 12.7 * mm),
        # Single label printers (one label per page)
        Template("avery_l7651", "Avery L7651 – 65 per A4 (38.1×21.2 mm), small drawers", A4, 5, 13, 38.1 * mm, 21.2 * mm, 4.75 * mm, 10.7 * mm, 2.5 * mm, 0),
        Template("single_62x29", "Label printer 62×29 mm (Brother DK-11209)", (62 * mm, 29 * mm), 1, 1, 62 * mm, 29 * mm, 0, 0, 0, 0),
        Template("single_62x100", "Label printer 62×100 mm (Brother DK-11202)", (62 * mm, 100 * mm), 1, 1, 62 * mm, 100 * mm, 0, 0, 0, 0),
        Template("single_4x6", "Shipping label 4×6 in", (101.6 * mm, 152.4 * mm), 1, 1, 101.6 * mm, 152.4 * mm, 0, 0, 0, 0),
    ]
}


@dataclass
class LabelData:
    code: str
    payload: str
    title: str
    number: str | None
    location: str | None
    contents: list[str] = field(default_factory=list)
    description: str | None = None
    # Optional arrow pointing to where the shelf/box is: one of ARROWS.
    arrow: str | None = None
    # Colour band down the left edge: which room it belongs in.
    band: tuple[float, float, float] | None = None
    band_text: str | None = None


THEMES = ("standard", "large", "drawer")

# Room colours: distinct, and dark enough for white text on them.
ROOM_COLOURS = [(0.80, 0.15, 0.15), (0.10, 0.40, 0.75), (0.15, 0.55, 0.20), (0.90, 0.50, 0.05), (0.50, 0.20, 0.60),
                (0.00, 0.55, 0.55), (0.65, 0.40, 0.15), (0.85, 0.20, 0.55), (0.30, 0.30, 0.30), (0.45, 0.55, 0.10)]


def room_colour(index: int) -> tuple[float, float, float]:
    return ROOM_COLOURS[index % len(ROOM_COLOURS)]


# Direction -> angle (degrees, anticlockwise from pointing right).
ARROWS = {"right": 0, "up_right": 45, "up": 90, "up_left": 135, "left": 180, "down_left": 225, "down": 270,
          "down_right": 315}


def _draw_arrow(c: canvas.Canvas, cx: float, cy: float, size: float, direction: str) -> None:
    """A bold filled arrow centred on (cx, cy), fitting a ``size`` square."""
    angle = ARROWS.get(direction)
    if angle is None:
        return
    half = size / 2 * (0.82 if angle % 90 else 0.95)  # diagonals fit the square too
    shaft = half * 0.34
    head_len = half * 0.95
    head_w = half * 0.9
    c.saveState()
    c.translate(cx, cy)
    c.rotate(angle)
    path = c.beginPath()
    path.moveTo(-half, -shaft)
    path.lineTo(half - head_len, -shaft)
    path.lineTo(half - head_len, -head_w)
    path.lineTo(half, 0)
    path.lineTo(half - head_len, head_w)
    path.lineTo(half - head_len, shaft)
    path.lineTo(-half, shaft)
    path.close()
    c.setFillGray(0)
    c.drawPath(path, stroke=0, fill=1)
    c.restoreState()


def _lines(c: canvas.Canvas, text: str, font: str, size: float, width: float) -> list[tuple[str, float]]:
    """Text broken at spaces into lines no wider than ``width`` - never inside a
    word.  A single word wider than the line gets a smaller size of its own."""
    out: list[tuple[str, float]] = []
    line = ""
    for word in text.split():
        trial = f"{line} {word}" if line else word
        if c.stringWidth(trial, font, size) <= width:
            line = trial
            continue
        if line:
            out.append((line, size))
        if c.stringWidth(word, font, size) <= width:
            line = word
        else:  # one very long word: shrink just this line
            ws = size
            while ws > 4 and c.stringWidth(word, font, ws) > width:
                ws -= 0.25
            out.append((word, ws))
            line = ""
    if line:
        out.append((line, size))
    return out


def _wrap(c: canvas.Canvas, text: str, font: str, size: float, width: float, max_lines: int,
          min_scale: float = 0.85) -> list[tuple[str, float]]:
    """Wrapped lines, at full size when they fit in ``max_lines``; otherwise a
    little smaller (down to ``min_scale``), and only then cut after the last
    whole word that fits, with "…"."""
    s = size
    while True:
        lines = _lines(c, text, font, s, width)
        if len(lines) <= max_lines or s <= size * min_scale:
            break
        s -= 0.25
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last, ls = lines[-1]
        words = last.split()
        while words and c.stringWidth(" ".join(words) + " …", font, ls) > width:
            words.pop()
        lines[-1] = ((" ".join(words) + " …") if words else "…", ls)
    return lines


def _draw_band(c: canvas.Canvas, x: float, y: float, w: float, h: float, d: LabelData) -> float:
    """The room's colour down the left edge, its name in white if it fits;
    returns the band's width."""
    if not d.band:
        return 0
    bw = max(3 * mm, min(6 * mm, w * 0.08))
    c.saveState()
    c.setFillColorRGB(*d.band)
    c.rect(x, y, bw, h, stroke=0, fill=1)
    if d.band_text and bw >= 4 * mm:
        size = min(bw * 0.62, 9)
        text = d.band_text
        while text and c.stringWidth(text, "Helvetica-Bold", size) > h - 3 * mm:
            text = text[:-1]
        c.setFillColorRGB(1, 1, 1)
        c.setFont("Helvetica-Bold", size)
        c.translate(x + bw / 2 + size * 0.35, y + h / 2)
        c.rotate(90)
        c.drawCentredString(0, 0, text)
    c.restoreState()
    return bw


def _draw_label(c: canvas.Canvas, x: float, y: float, w: float, h: float, d: LabelData, theme: str = "standard") -> None:
    """Number (large), title, description, contents; where it is at the foot.
    Text wraps between words and keeps its size where it can.

    ``large``: bigger text, no contents list, for reading across a garage.
    ``drawer``: number and name only, filling the label, for small drawers."""
    band = _draw_band(c, x, y, w, h, d)
    x, w = x + band, w - band
    if theme == "drawer":
        _draw_drawer(c, x, y, w, h, d)
        return
    large = theme == "large"
    if large:
        d = LabelData(**{**d.__dict__, "contents": [], "description": None})
    pad = 2.5 * mm
    portrait = h > w * 1.3
    arrow = d.arrow if d.arrow in ARROWS else None
    qr_size = min(h - 2 * pad, (w - 2 * pad) * (0.9 if portrait else 0.45))
    if portrait and arrow:
        qr_size = min(qr_size, (w - 2 * pad) * 0.58)  # QR on the left, arrow beside it
    qr_img = ImageReader(io.BytesIO(qr_png(d.payload, scale=8)))
    if portrait:
        qx = x + pad if arrow else x + (w - qr_size) / 2
        qy = y + h - pad - qr_size
        tx, ty, tw = x + pad, qy - 3 * mm, w - 2 * pad
        if arrow:
            room_w = x + w - pad - (qx + qr_size + 2 * mm)
            _draw_arrow(c, qx + qr_size + 2 * mm + room_w / 2, qy + qr_size / 2, min(room_w, qr_size), arrow)
    else:
        qx, qy = x + pad, y + (h - qr_size) / 2
        tx, ty, tw = qx + qr_size + 2 * mm, y + h - pad - 1 * mm, w - qr_size - 2 * pad - 2 * mm
        if arrow:
            a = min(h - 2 * pad - 3 * mm, tw * 0.3)
            tw -= a + 2 * mm
            _draw_arrow(c, x + w - pad - a / 2, y + h / 2 + 1.5 * mm, a, arrow)
    c.drawImage(qr_img, qx, qy, qr_size, qr_size)
    big = 22 if h > 45 * mm else 16 if h > 30 * mm else 12
    if large:
        big *= 1.5
    small = max(6, big * 0.38)
    code_size = max(5, small * 0.8)

    def draw(lines: list[tuple[str, float]], font: str, gap: float) -> None:
        nonlocal ty
        for text, s in lines:
            c.setFont(font, s)
            c.drawString(tx, ty - s * 0.8, text)
            ty -= s + gap

    # Bottom: the code, and where it is (reserved first, so it never disappears).
    bottom = y + pad * 0.6
    c.setFont("Helvetica", code_size)
    c.drawRightString(x + w - pad, bottom, d.code)
    floor = bottom + code_size + 1
    if d.location:
        # Where it lives is what gets things put back: bold, black and a
        # size up from the contents, above a thin rule.
        loc_size = small * 1.2
        where = d.location
        if large:  # one line: keep the end of the path, the specific bit
            parts = where.split(" › ")
            while len(parts) > 1 and c.stringWidth(where, "Helvetica-Bold", loc_size * 0.7) > tw:
                parts = parts[1:]
                where = "… › " + " › ".join(parts)
        loc_lines = _wrap(c, where, "Helvetica-Bold", loc_size, tw, 1 if large else 2, min_scale=0.7 if large else 0.75)
        ly = floor + (len(loc_lines) - 1) * (loc_lines[0][1] + 1)
        for text, s in loc_lines:
            c.setFont("Helvetica-Bold", s)
            c.drawString(tx, ly, text)
            ly -= s + 1
        floor += len(loc_lines) * (loc_lines[0][1] + 1) + 1
        c.setLineWidth(0.4)
        c.line(tx, floor, tx + tw, floor)
        floor += 2

    def room(size: float) -> int:
        return max(0, int((ty - floor) // (size + 1.5)))

    if d.number:
        draw(_wrap(c, f"#{d.number}", "Helvetica-Bold", big, tw, 1, min_scale=0.6), "Helvetica-Bold", 1.5)
    title_size = big * (0.75 if large else 0.6)
    title_lines = max(2 if large else 1, min(3, room(title_size)))
    draw(_wrap(c, d.title, "Helvetica-Bold", title_size, tw, title_lines, min_scale=0.55 if large else 0.85), "Helvetica-Bold", 1.5)
    ty -= 1
    if d.description and room(small) >= 1:
        draw(_wrap(c, d.description, "Helvetica-Oblique", small, tw, min(2, room(small))), "Helvetica-Oblique", 1)
    for i, line in enumerate(d.contents):
        left = room(small)
        if left < 1:
            break
        if left == 1 and i < len(d.contents) - 1:
            draw([(f"+ {len(d.contents) - i} more", small)], "Helvetica", 1)
            break
        bullet = "" if line.startswith("› ") else "• "  # "› Top shelf": a place within this one
        draw(_wrap(c, f"{bullet}{line}", "Helvetica", small, tw, 1, min_scale=0.9), "Helvetica", 1)


def _draw_drawer(c: canvas.Canvas, x: float, y: float, w: float, h: float, d: LabelData) -> None:
    """Small drawer label: a small QR on the left; the number and name as big
    as the space allows."""
    pad = 1.5 * mm
    qr_size = min(h - 2 * pad, w * 0.3)
    c.drawImage(ImageReader(io.BytesIO(qr_png(d.payload, scale=6))), x + pad, y + (h - qr_size) / 2, qr_size, qr_size)
    tx = x + pad + qr_size + 1.5 * mm
    tw = x + w - pad - tx
    text = f"#{d.number} {d.title}" if d.number else d.title
    # The biggest size at which it fits in the lines the height allows.
    size = min(h * 0.55, 40)
    while True:
        max_lines = max(1, int((h - 2 * pad) // (size * 1.1)))
        lines = _lines(c, text, "Helvetica-Bold", size, tw)
        if len(lines) <= max_lines and all(s >= size for _, s in lines):
            break
        if size <= 6:
            lines = _wrap(c, text, "Helvetica-Bold", size, tw, max_lines, min_scale=1)
            break
        size *= 0.9
    total = sum(s * 1.1 for _, s in lines)
    ty = y + (h + total) / 2
    for line, s in lines:
        c.setFont("Helvetica-Bold", s)
        ty -= s * 1.1
        c.drawString(tx, ty + s * 0.2, line)


def render_pdf(labels: list[LabelData], template_key: str, copies: int = 1, start_position: int = 1,
               theme: str = "standard") -> bytes:
    t = TEMPLATES.get(template_key) or TEMPLATES["avery_l7160"]
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=t.page)
    c.setTitle("Container labels")
    page_w, page_h = t.page
    per_page = t.cols * t.rows
    slot = max(0, start_position - 1) % per_page
    expanded = [d for d in labels for _ in range(copies)]
    for d in expanded:
        if slot >= per_page:
            c.showPage()
            slot = 0
        col, row = slot % t.cols, slot // t.cols
        x = t.margin_left + col * (t.width + t.gap_x)
        y = page_h - t.margin_top - (row + 1) * t.height - row * t.gap_y
        _draw_label(c, x, y, t.width, t.height, d, theme)
        slot += 1
    c.showPage()
    c.save()
    return buf.getvalue()
