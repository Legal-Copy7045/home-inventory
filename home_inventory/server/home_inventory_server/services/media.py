"""Photo and document storage.

Security properties:
* File type is determined from content (Pillow / PDF magic), never from the
  client's filename or Content-Type.
* Images are decoded and re-encoded (JPEG/PNG/WebP), which strips EXIF/GPS
  metadata and anything appended to the image, and applies orientation.
* Stored paths are generated server-side (random hex), so user input never
  reaches the filesystem path.
* Size and pixel limits guard against decompression bombs.
"""

from __future__ import annotations

import hashlib
import io
import secrets
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

THUMB_PX = 480
IMAGE_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp", "GIF": "image/gif", "MPO": "image/jpeg"}


class MediaError(ValueError):
    pass


@dataclass
class StoredFile:
    stored_path: str
    thumb_path: str | None
    mime_type: str
    size_bytes: int
    sha256: str
    width: int | None
    height: int | None


def taken_on(data: bytes) -> str | None:
    """The day a photo was taken, from its EXIF data, as YYYY-MM-DD.

    Read before the photo is re-encoded (which strips EXIF).  ``None`` when
    there's no usable date - a screenshot, an edited copy, a PDF.
    """
    if data[:5] == b"%PDF-":
        return None
    try:
        with Image.open(io.BytesIO(data)) as img:
            exif = img.getexif()
            raw = exif.get_ifd(0x8769).get(36867) or exif.get(306)  # DateTimeOriginal, else DateTime
    except Exception:  # noqa: BLE001 - any unreadable EXIF just means "no date"
        return None
    if not isinstance(raw, str) or len(raw) < 10:
        return None
    from datetime import date

    try:
        return date(int(raw[0:4]), int(raw[5:7]), int(raw[8:10])).isoformat()
    except ValueError:
        return None


class MediaStore:
    def __init__(self, root: Path, max_bytes: int, max_image_px: int) -> None:
        self.root = root
        self.max_bytes = max_bytes
        self.max_image_px = max_image_px
        Image.MAX_IMAGE_PIXELS = 80_000_000

    def path(self, relative: str) -> Path:
        p = (self.root / relative).resolve()
        if self.root.resolve() not in p.parents:
            raise MediaError("invalid media path")
        return p

    def _new_name(self, ext: str) -> str:
        token = secrets.token_hex(16)
        return f"{token[:2]}/{token}{ext}"

    def save(self, data: bytes, allow_documents: bool) -> StoredFile:
        if not data:
            raise MediaError("Empty upload")
        if len(data) > self.max_bytes:
            raise MediaError(f"File is larger than {self.max_bytes // (1024 * 1024)} MB")
        if data[:5] == b"%PDF-":
            if not allow_documents:
                raise MediaError("A photo is required here")
            return self._write(data, ".pdf", "application/pdf", None, None, None)
        try:
            img = Image.open(io.BytesIO(data))
            fmt = img.format or ""
            img.load()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as err:
            raise MediaError("Unsupported file type; upload a JPEG, PNG, WebP or PDF") from err
        if fmt not in IMAGE_TYPES:
            raise MediaError("Unsupported image format")
        img = ImageOps.exif_transpose(img)
        if max(img.size) > self.max_image_px:
            img.thumbnail((self.max_image_px, self.max_image_px), Image.Resampling.LANCZOS)
        has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
        out = io.BytesIO()
        if has_alpha:
            img = img.convert("RGBA")
            img.save(out, "PNG", optimize=True)
            ext, mime = ".png", "image/png"
        else:
            img = img.convert("RGB")
            img.save(out, "JPEG", quality=88, optimize=True)
            ext, mime = ".jpg", "image/jpeg"
        thumb = img.copy()
        thumb.thumbnail((THUMB_PX, THUMB_PX), Image.Resampling.LANCZOS)
        tout = io.BytesIO()
        thumb.convert("RGB").save(tout, "JPEG", quality=80)
        return self._write(out.getvalue(), ext, mime, tout.getvalue(), img.width, img.height)

    def _write(
        self, data: bytes, ext: str, mime: str, thumb: bytes | None, width: int | None, height: int | None
    ) -> StoredFile:
        name = self._new_name(ext)
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        thumb_name = None
        if thumb is not None:
            thumb_name = name.rsplit(".", 1)[0] + ".thumb.jpg"
            (self.root / thumb_name).write_bytes(thumb)
        return StoredFile(
            stored_path=name,
            thumb_path=thumb_name,
            mime_type=mime,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            width=width,
            height=height,
        )

    def delete(self, relative: str | None) -> None:
        if not relative:
            return
        try:
            self.path(relative).unlink(missing_ok=True)
        except MediaError:
            pass

    def read(self, relative: str) -> bytes:
        return self.path(relative).read_bytes()


def crop_box(data: bytes, box: list[float], pad: float = 0.04) -> bytes | None:
    """JPEG of the part of a stored photo inside ``box`` = [x, y, w, h]
    (fractions of the width/height), with a little margin.  None if too small."""
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img).convert("RGB")
    w, h = img.size
    x, y, bw, bh = box
    left, top = max(0.0, x - pad), max(0.0, y - pad)
    right, bottom = min(1.0, x + bw + pad), min(1.0, y + bh + pad)
    px = (int(left * w), int(top * h), int(right * w), int(bottom * h))
    if px[2] - px[0] < 48 or px[3] - px[1] < 48:
        return None
    out = io.BytesIO()
    img.crop(px).save(out, "JPEG", quality=88)
    return out.getvalue()


def downscale_for_ai(data: bytes, max_px: int) -> tuple[bytes, str]:
    """Return a JPEG no larger than ``max_px`` on its long edge."""
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85)
    return out.getvalue(), "image/jpeg"
