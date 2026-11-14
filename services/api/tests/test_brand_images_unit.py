"""Brand image checks (ENH-006, US-149): type from the bytes, pixel limits, and SVGs that could run or fetch."""

from __future__ import annotations

import struct
import zlib

import pytest

from tela_api.platform.brand_images import check_svg, inspect_image
from tela_common.errors import TelaError


def png(width: int = 64, height: int = 32) -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"\0" * 8)) + chunk(b"IEND", b"")


def jpeg(width: int = 120, height: int = 40) -> bytes:
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\0\x01\x01\0\0\x01\0\x01\0\0"
    sof0 = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 40"><title>Logo</title>' \
      b'<defs><linearGradient id="g"><stop offset="0" stop-color="#236f8d"/></linearGradient></defs>' \
      b'<rect width="120" height="40" fill="url(#g)"/><use href="#g"/></svg>'


def test_png_and_jpeg_sizes_come_from_their_headers() -> None:
    image = inspect_image(png(200, 80))
    assert (image.ext, image.content_type, image.width, image.height) == ("png", "image/png", 200, 80)
    image = inspect_image(jpeg(300, 90))
    assert (image.ext, image.content_type, image.width, image.height) == ("jpg", "image/jpeg", 300, 90)


def test_a_clean_svg_is_accepted_with_its_viewbox_size() -> None:
    image = inspect_image(SVG)
    assert (image.ext, image.width, image.height) == ("svg", 120, 40)
    assert check_svg(b'<svg xmlns="http://www.w3.org/2000/svg" width="48px" height="24"/>') == (48, 24)
    assert check_svg(b'<svg xmlns="http://www.w3.org/2000/svg"/>') == (None, None)


@pytest.mark.parametrize(("data", "words"), [
    (b"", "empty"),
    (b"GIF89a....", "PNG, JPEG or SVG"),
    (png(8, 8), "between 16 and 4096"),
    (png(5000, 100), "between 16 and 4096"),
    (b"\x89PNG\r\n\x1a\nxx", "damaged"),
    (b"\xff\xd8\xff\xe0\x00\x02", "damaged"),
])
def test_wrong_type_size_or_damaged_files_are_refused(data: bytes, words: str) -> None:
    with pytest.raises(TelaError) as err:
        inspect_image(data)
    assert words in err.value.message


NS = 'xmlns="http://www.w3.org/2000/svg"'


@pytest.mark.parametrize(("svg", "words"), [
    (f"<svg {NS}><script>alert(1)</script></svg>", "<script>"),
    (f"<svg {NS}><foreignObject/></svg>", "<foreignObject>"),
    (f"<svg {NS}><image href='https://x.test/a.png'/></svg>", "<image>"),
    (f'<svg {NS} onload="alert(1)"/>', "event handler"),
    (f'<svg {NS} xmlns:x="http://www.w3.org/1999/xlink"><use x:href="https://x.test/s.svg#a"/></svg>', "outside"),
    (f'<svg {NS}><style>@import url(https://x.test/a.css);</style></svg>', "styles"),
    (f'<svg {NS}><rect style="fill:url(https://x.test/p)"/></svg>', "could run code"),
    (f'<svg {NS}><rect fill="javascript:alert(1)"/></svg>', "could run code"),
    (f'<svg {NS}><h:p xmlns:h="http://www.w3.org/1999/xhtml">x</h:p></svg>', "<p>"),
    ('<!DOCTYPE svg [<!ENTITY a "aaaa">]><svg ' + NS + ">&a;</svg>", "DOCTYPE"),
    ("<svg><rect/></svg>", "not an SVG"),
    (f"<svg {NS}><rect>", "couldn't be read"),
])
def test_unsafe_or_broken_svgs_are_refused(svg: str, words: str) -> None:
    with pytest.raises(TelaError) as err:
        inspect_image(svg.encode())
    assert words in err.value.message


def test_editor_metadata_in_other_namespaces_is_allowed() -> None:
    svg = (f'<svg {NS} xmlns:i="http://www.inkscape.org/namespaces/inkscape" width="32" height="32">'
           '<i:namedview/><metadata/><g i:label="layer"><path d="M0 0h32v32z"/></g></svg>').encode()
    assert inspect_image(svg).ext == "svg"
