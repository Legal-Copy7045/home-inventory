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


def _fit(c: canvas.Canvas, text: str, font: str, size: float, max_width: float) -> tuple[str, float]:
    while size > 5 and c.stringWidth(text, font, size) > max_width:
        size -= 0.5
    while c.stringWidth(text, font, size) > max_width and len(text) > 3:
        text = text[:-2] + "…"
    return text, size


def _draw_label(c: canvas.Canvas, x: float, y: float, w: float, h: float, d: LabelData) -> None:
    pad = 2.5 * mm
    portrait = h > w * 1.3
    qr_size = min(h - 2 * pad, (w - 2 * pad) * (0.9 if portrait else 0.45))
    qr_img = ImageReader(io.BytesIO(qr_png(d.payload, scale=8)))
    if portrait:
        qx, qy = x + (w - qr_size) / 2, y + h - pad - qr_size
        tx, ty, tw = x + pad, qy - 3 * mm, w - 2 * pad
    else:
        qx, qy = x + pad, y + (h - qr_size) / 2
        tx, ty, tw = qx + qr_size + 2 * mm, y + h - pad - 2 * mm, w - qr_size - 2 * pad - 2 * mm
    c.drawImage(qr_img, qx, qy, qr_size, qr_size)
    big = 22 if h > 45 * mm else 16 if h > 30 * mm else 12
    if d.number:
        num, s = _fit(c, f"#{d.number}", "Helvetica-Bold", big, tw)
        c.setFont("Helvetica-Bold", s)
        c.drawString(tx, ty - s * 0.7, num)
        ty -= s + 1.5
    title, s = _fit(c, d.title, "Helvetica-Bold", big * 0.6, tw)
    c.setFont("Helvetica-Bold", s)
    c.drawString(tx, ty - s * 0.8, title)
    ty -= s + 2
    small = max(6, big * 0.38)
    if d.location:
        loc, s2 = _fit(c, d.location, "Helvetica", small, tw)
        c.setFont("Helvetica", s2)
        c.drawString(tx, ty - s2, loc)
        ty -= s2 + 2
    c.setFont("Helvetica", small)
    floor = y + pad + small
    for line in d.contents:
        if ty - small < floor:
            c.drawString(tx, ty - small, "…")
            break
        text, s3 = _fit(c, f"• {line}", "Helvetica", small, tw)
        c.setFont("Helvetica", s3)
        c.drawString(tx, ty - s3, text)
        ty -= s3 + 1
    c.setFont("Helvetica", max(5, small * 0.8))
    c.drawRightString(x + w - pad, y + pad * 0.6, d.code)


def render_pdf(labels: list[LabelData], template_key: str, copies: int = 1, start_position: int = 1) -> bytes:
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
        _draw_label(c, x, y, t.width, t.height, d)
        slot += 1
    c.showPage()
    c.save()
    return buf.getvalue()
