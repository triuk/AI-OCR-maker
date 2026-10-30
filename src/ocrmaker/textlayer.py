"""Exporter of the text layer: writes the OCR PDF from the source PDF plus words placed in their word boxes.

The scan images and every existing object of the source PDF stay byte for byte as they are. Each page gets
two new content streams around its own ones:

    q <page transform> q <original content> Q  <text layer> Q

so an optional page transform (ADR 0003: rotation through the page matrix) moves the scan image and the
text layer together without touching the image, and cropping only rewrites the CropBox.

The text is invisible (render mode 3) and set in a glyphless font, as Tesseract's own PDF renderer does:
a Type0 font with Identity-H encoding whose every character id is drawn by one empty glyph of half an em.
Character ids are allocated per document, one per distinct character, and a ToUnicode CMap maps each back
to its text, so any text (Czech diacritics included) copies exactly as it was stored. Each word is set in
the page as displayed (upright), at the height of its word box with its baseline on the box's bottom edge,
and horizontally scaled (Tz) to the box's width. Words of one line are separated by a space; a line ends
without one. Line-end hyphenation stays as the words carry it.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pymupdf

from .pages import apply_rect, display_matrix, display_size, invert, multiply, transform_matrix

FONT_NAME = "OcrText"
GLYPH_ADVANCE = 500  # of 1000 units per em; every character has this width
UNITS_PER_EM = 1000


def _table_checksum(data: bytes) -> int:
    data += b"\0" * (-len(data) % 4)
    return sum(struct.unpack(f">{len(data) // 4}I", data)) & 0xFFFFFFFF


def glyphless_font() -> bytes:
    """A minimal TrueType font: .notdef and one empty glyph, both GLYPH_ADVANCE wide, ascent 1 em, descent 0."""
    name_text = FONT_NAME.encode("utf-16-be")
    records = [(1, len(name_text)), (4, len(name_text)), (6, len(name_text))]
    name = struct.pack(">HHH", 0, len(records), 6 + 12 * len(records))
    for name_id, length in records:
        name += struct.pack(">HHHHHH", 3, 1, 0x409, name_id, length, 0)
    name += name_text
    cmap_format4 = struct.pack(">HHHHHHHHHHHH", 4, 24, 0, 2, 2, 0, 0, 0xFFFF, 0, 0xFFFF, 1, 0)
    tables = {
        b"cmap": struct.pack(">HHHHI", 0, 1, 3, 1, 12) + cmap_format4,
        b"glyf": b"",
        b"head": struct.pack(">IIIIHHqqhhhhHHhhh", 0x00010000, 0x00010000, 0, 0x5F0F3CF5, 0x000B,
                             UNITS_PER_EM, 0, 0, 0, 0, GLYPH_ADVANCE, UNITS_PER_EM, 0, 3, 2, 0, 0),
        b"hhea": struct.pack(">IhhhHhhhhhhhhhhhH", 0x00010000, UNITS_PER_EM, 0, 0, GLYPH_ADVANCE, 0, 0, 0,
                             1, 0, 0, 0, 0, 0, 0, 0, 2),
        b"hmtx": struct.pack(">HhHh", GLYPH_ADVANCE, 0, GLYPH_ADVANCE, 0),
        b"loca": struct.pack(">HHH", 0, 0, 0),
        b"maxp": struct.pack(">IHHHHHHHHHHHHHH", 0x00010000, 2, 0, 0, 0, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0),
        b"name": name,
        b"post": struct.pack(">IIhhIIIII", 0x00030000, 0, 0, 0, 0, 0, 0, 0, 0),
    }
    count = len(tables)
    power = 1 << (count.bit_length() - 1)
    header = struct.pack(">IHHHH", 0x00010000, count, power * 16, power.bit_length() - 1, count * 16 - power * 16)
    offset = len(header) + 16 * count
    directory, body = b"", b""
    for tag in sorted(tables):
        data = tables[tag]
        directory += struct.pack(">4sIII", tag, _table_checksum(data), offset + len(body), len(data))
        body += data + b"\0" * (-len(data) % 4)
    font = bytearray(header + directory + body)
    head_offset = offset + sum(len(tables[tag]) + (-len(tables[tag]) % 4) for tag in sorted(tables) if tag < b"head")
    struct.pack_into(">I", font, head_offset + 8, (0xB1B0AFBA - _table_checksum(bytes(font))) & 0xFFFFFFFF)
    return bytes(font)


class _Font:
    """The document's glyphless text font; character ids are allocated as words are encoded."""

    def __init__(self, document: pymupdf.Document):
        self.document = document
        self.ids: dict[str, int] = {}
        self.xref = self._new_object("<< >>")

    def _new_object(self, source: str, stream: bytes | None = None) -> int:
        xref = self.document.get_new_xref()
        self.document.update_object(xref, source)
        if stream is not None:
            self.document.update_stream(xref, stream, compress=True)
        return xref

    def encode(self, text: str) -> str:
        """The hex string of a text's character ids (one per Unicode character)."""
        return "".join(f"{self.ids.setdefault(char, len(self.ids) + 1):04X}" for char in text)

    def finish(self) -> None:
        """Write the font objects once every word is encoded: the ToUnicode map needs all characters."""
        top = len(self.ids)
        if top >= 0xFFFF:
            raise ValueError("Too many distinct characters for one text font")
        program = glyphless_font()
        file = self._new_object(f"<< /Length1 {len(program)} >>", program)
        descriptor = self._new_object(
            f"<< /Type /FontDescriptor /FontName /{FONT_NAME} /Flags 5 /FontBBox [0 0 {GLYPH_ADVANCE} "
            f"{UNITS_PER_EM}] /ItalicAngle 0 /Ascent {UNITS_PER_EM} /Descent 0 /CapHeight {UNITS_PER_EM} "
            f"/StemV 80 /FontFile2 {file} 0 R >>")
        # Every character id is drawn by glyph 1, the empty one.
        to_glyph = self._new_object("<< >>", b"\0\0" + b"\0\1" * top)
        cid_font = self._new_object(
            f"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /{FONT_NAME} "
            "/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> "
            f"/FontDescriptor {descriptor} 0 R /DW {GLYPH_ADVANCE} /CIDToGIDMap {to_glyph} 0 R >>")
        to_unicode = self._new_object("<< >>", _to_unicode(self.ids))
        self.document.update_object(
            self.xref, f"<< /Type /Font /Subtype /Type0 /BaseFont /{FONT_NAME} /Encoding /Identity-H "
                       f"/DescendantFonts [{cid_font} 0 R] /ToUnicode {to_unicode} 0 R >>")


def _to_unicode(ids: dict[str, int]) -> bytes:
    entries = [f"<{cid:04X}> <{char.encode('utf-16-be').hex().upper()}>" for char, cid in ids.items()]
    chunks = "".join(f"{len(chunk)} beginbfchar\n" + "\n".join(chunk) + "\nendbfchar\n"
                     for chunk in (entries[i:i + 100] for i in range(0, len(entries), 100)))
    return ("/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
            "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
            "/CMapName /Adobe-Identity-UCS def\n/CMapType 2 def\n"
            "1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
            f"{chunks}endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n").encode("ascii")


def _number(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _matrix(matrix) -> str:
    return " ".join(_number(value) for value in matrix)


def _text_layer(font: _Font, page: dict, lines: list[list[dict]], resource: str) -> bytes:
    """Content operators setting the words of one page, in the page's user space."""
    display = display_matrix(page)
    _, height = display_size(page)
    # Words are set upright in the displayed page with y upwards; this maps that frame to PDF user space.
    upright_to_display = [1, 0, 0, -1, 0, height]
    to_user = multiply(multiply(upright_to_display, invert(display)), invert(page["pdf_to_source"]))
    out = [f"q {_matrix(to_user)} cm BT 3 Tr"]
    for line in lines:
        for position, word in enumerate(line):
            text = word["text"] + (" " if position < len(line) - 1 else "")
            x0, y0, x1, y1 = apply_rect(display, word["bbox"])
            size = y1 - y0
            if size <= 0 or x1 <= x0 or not word["text"]:
                continue
            scale = 100 * (x1 - x0) / (len(word["text"]) * size * GLYPH_ADVANCE / UNITS_PER_EM)
            out.append(f"/{resource} {_number(size)} Tf {_number(scale)} Tz "
                       f"1 0 0 1 {_number(x0)} {_number(height - y1)} Tm <{font.encode(text)}> Tj")
    out.append("ET Q")
    return "\n".join(out).encode("ascii")


def _add_font_resource(document: pymupdf.Document, page: pymupdf.Page, font_xref: int) -> str:
    """Register the text font in the page's resources under a name not used there yet."""
    kind, value = document.xref_get_key(page.xref, "Resources")
    if kind == "null":  # inherited from the page tree: give the page its own copy first
        parent = page.xref
        while kind == "null":
            kind, parent_ref = document.xref_get_key(parent, "Parent")
            if kind != "xref":
                break
            parent = int(parent_ref.split()[0])
            kind, value = document.xref_get_key(parent, "Resources")
        document.xref_set_key(page.xref, "Resources", value if kind != "null" else "<< >>")
        kind, value = document.xref_get_key(page.xref, "Resources")
    holder, prefix = (int(value.split()[0]), "") if kind == "xref" else (page.xref, "Resources/")
    fonts_kind, fonts = document.xref_get_key(holder, prefix + "Font")
    if fonts_kind == "xref":
        holder, prefix = int(fonts.split()[0]), ""
    else:
        prefix += "Font/"
    used = fonts if fonts_kind != "null" else ""
    number = 0
    while f"/{FONT_NAME}{number}" in used:
        number += 1
    name = f"{FONT_NAME}{number}"
    document.xref_set_key(holder, prefix + name, f"{font_xref} 0 R")
    return name


def _wrap_contents(document: pymupdf.Document, page: pymupdf.Page, before: bytes, after: bytes) -> None:
    """Put new content streams before and after the page's own ones, which stay untouched."""
    own = [f"{xref} 0 R" for xref in page.get_contents()]
    streams = []
    for data in (before, after):
        xref = document.get_new_xref()
        document.update_object(xref, "<< >>")
        document.update_stream(xref, data, compress=True)
        streams.append(f"{xref} 0 R")
    document.xref_set_key(page.xref, "Contents", f"[{streams[0]} {' '.join(own)} {streams[1]}]")


def write_ocr_pdf(source: Path, target: Path, pages: list[dict]) -> None:
    """Write the OCR PDF: the source PDF with each listed page's words added as an invisible text layer.

    Each item of pages is {"page": <prepared page geometry, see ocrmaker.pages>, "lines": [[word, ...], ...]},
    lines in the order the text layer is written, each word {"text", "bbox"} in source page coordinates.
    """
    with pymupdf.open(source) as document:
        font = _Font(document)
        for item in pages:
            page_geometry = item["page"]
            page = document[page_geometry["index"]]
            resource = _add_font_resource(document, page, font.xref)
            to_source = page_geometry["pdf_to_source"]
            transform = multiply(multiply(to_source, transform_matrix(page_geometry)), invert(to_source))
            text = _text_layer(font, page_geometry, item["lines"], resource)
            _wrap_contents(document, page, f"q {_matrix(transform)} cm q\n".encode("ascii"), b"\nQ\n" + text + b"\nQ\n")
            crop = (page_geometry.get("transform") or {}).get("crop")
            if crop:
                box = apply_rect(invert(to_source), crop)
                document.xref_set_key(page.xref, "CropBox", f"[{_matrix(box)}]")
        font.finish()
        document.save(target)
