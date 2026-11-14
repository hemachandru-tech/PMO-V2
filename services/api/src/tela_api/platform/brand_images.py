"""Checks for uploaded brand images (ENH-006, US-149): PNG, JPEG and SVG logos and favicons.

The type comes from the file's own bytes, never its name. PNG and JPEG sizes are read from their headers, so no
image library is needed. An SVG is refused (not cleaned) when it holds anything that could run or fetch: scripts,
event handlers, HTML, external links, ``javascript:`` URLs, CSS imports or external ``url(...)``, or a DOCTYPE
(which also rules out entity expansion). Logos are shown in ``<img>`` and served with a locked-down CSP as well.
"""

from __future__ import annotations

import re
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from tela_common.errors import ErrorCode, TelaError

MIN_PX = 16
MAX_PX = 4096
SVG_NS = "http://www.w3.org/2000/svg"
XHTML_NS = "http://www.w3.org/1999/xhtml"
SVG_ELEMENTS = frozenset({
    "svg", "g", "defs", "symbol", "use", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon",
    "text", "tspan", "textPath", "title", "desc", "metadata", "style", "linearGradient", "radialGradient", "stop",
    "clipPath", "mask", "pattern", "marker", "filter", "feBlend", "feColorMatrix", "feComponentTransfer",
    "feComposite", "feDropShadow", "feFlood", "feFuncA", "feFuncB", "feFuncG", "feFuncR", "feGaussianBlur",
    "feMerge", "feMergeNode", "feMorphology", "feOffset",
})
_CSS_BAD = re.compile(r"@import|expression\s*\(|url\s*\(\s*(?!['\"]?#)", re.IGNORECASE)


@dataclass(frozen=True)
class BrandImage:
    ext: str
    content_type: str
    width: int | None
    height: int | None


def _bad(message: str) -> TelaError:
    return TelaError(ErrorCode.VALIDATION_ERROR, message)


def _png_size(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[12:16] != b"IHDR":
        raise _bad("The PNG file is damaged.")
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def _jpeg_size(data: bytes) -> tuple[int, int]:
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7 or marker == 0xFF:
            i += 1 if marker == 0xFF else 2
            continue
        (length,) = struct.unpack(">H", data[i + 2:i + 4])
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height, width = struct.unpack(">HH", data[i + 5:i + 9])
            return width, height
        i += 2 + length
    raise _bad("The JPEG file is damaged.")


def _local(tag: str) -> tuple[str, str]:
    if tag.startswith("{"):
        ns, _, name = tag[1:].partition("}")
        return ns, name
    return "", tag


def _number(value: str | None) -> float | None:
    m = re.fullmatch(r"\s*([0-9]*\.?[0-9]+)\s*(px)?\s*", value or "")
    return float(m.group(1)) if m else None


def check_svg(data: bytes) -> tuple[int | None, int | None]:
    """Refuse an SVG that could run code or load anything; return its size from viewBox or width/height."""
    try:
        source = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise _bad("The SVG file must be UTF-8 text.") from exc
    if re.search(r"<!(DOCTYPE|ENTITY)", source, re.IGNORECASE):
        raise _bad("SVG files with a DOCTYPE or entities can't be used. Export the logo again as plain SVG.")
    try:
        root = ET.fromstring(source)  # noqa: S314 - DOCTYPE refused above, so no entity expansion
    except ET.ParseError as exc:
        raise _bad("The SVG file couldn't be read.") from exc
    if _local(root.tag) != (SVG_NS, "svg"):
        raise _bad("The file is not an SVG image.")
    for el in root.iter():
        ns, name = _local(el.tag)
        if ns == XHTML_NS or (ns == SVG_NS and name not in SVG_ELEMENTS):
            raise _bad(f"The SVG contains a <{name}> element, which isn't allowed in a logo.")
        if name == "style" and _CSS_BAD.search(el.text or ""):
            raise _bad("The SVG's styles load something from outside the file, which isn't allowed.")
        for key, value in el.attrib.items():
            attr = _local(key)[1]
            low = value.strip().lower()
            if attr.lower().startswith("on"):
                raise _bad(f"The SVG has an event handler ({attr}), which isn't allowed in a logo.")
            if attr == "href" and not low.startswith("#"):
                raise _bad("The SVG links to something outside the file, which isn't allowed in a logo.")
            if "javascript:" in low.replace(" ", "") or (attr == "style" and _CSS_BAD.search(value)):
                raise _bad("The SVG has an attribute that could run code or load something, which isn't allowed.")
    box = (root.get("viewBox") or "").replace(",", " ").split()
    if len(box) == 4:
        try:
            return round(float(box[2])), round(float(box[3]))
        except ValueError:
            pass
    w, h = _number(root.get("width")), _number(root.get("height"))
    return (round(w), round(h)) if w and h else (None, None)


def inspect_image(data: bytes) -> BrandImage:
    """Work out what an uploaded image is and check it is safe and a sensible size."""
    if not data:
        raise _bad("The file is empty.")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        image = BrandImage("png", "image/png", *_png_size(data))
    elif data.startswith(b"\xff\xd8\xff"):
        image = BrandImage("jpg", "image/jpeg", *_jpeg_size(data))
    elif b"<svg" in data[:4096]:
        image = BrandImage("svg", "image/svg+xml", *check_svg(data))
    else:
        raise _bad("Upload a PNG, JPEG or SVG image.")
    for side in (image.width, image.height):
        if side is not None and image.ext != "svg" and not MIN_PX <= side <= MAX_PX:
            raise _bad(f"The image must be between {MIN_PX} and {MAX_PX} pixels on each side "
                       f"(this one is {image.width} by {image.height}).")
    return image
