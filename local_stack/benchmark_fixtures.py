"""Fixed synthetic quality fixtures; pure generation and scoring, no I/O."""
from __future__ import annotations

import base64
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import re
import struct
import zlib


_NUMERIC = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
_POSITION_PROMPT = (
    "Locate the single large colored square. Reply with exactly two uppercase words "
    "separated by a space: the half containing the square (LEFT or RIGHT), then its color. "
    "Do not add punctuation, explanation, or Markdown."
)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


@dataclass(frozen=True)
class QualityCase:
    name: str
    prompt: str
    expected: str | int | dict[str, str | int]
    png: bytes | None = None

    @property
    def modality(self):
        return "image" if self.png is not None else "text"

    def content(self):
        if self.png is None:
            return self.prompt
        encoded = base64.b64encode(self.png).decode("ascii")
        return [{"type": "image_url", "image_url": {"url": "data:image/png;base64," + encoded}},
                {"type": "text", "text": self.prompt}]

    def matches(self, content):
        if not isinstance(content, str) or len(content) > 4096:
            return False
        answer = content.strip()
        if isinstance(self.expected, str):
            return answer == self.expected
        if isinstance(self.expected, int):
            if len(answer) > 64 or not _NUMERIC.fullmatch(answer):
                return False
            try:
                return Decimal(answer) == self.expected
            except InvalidOperation:
                return False
        try:
            parsed = json.loads(answer, object_pairs_hook=_unique_object)
        except (ValueError, RecursionError):
            return False
        return (isinstance(parsed, dict) and parsed.keys() == self.expected.keys()
                and all(type(parsed[key]) is type(value) and parsed[key] == value
                        for key, value in self.expected.items()))


def _chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def _png(rectangles):
    # Fixed canvas and trusted rectangles only. No image decoder or caller files.
    width, height = 320, 160
    stride = width * 3
    pixels = bytearray(b"\xff" * (stride * height))
    for x, y, w, h, color in rectangles:
        row = bytes(color) * w
        for yy in range(y, y + h):
            start = yy * stride + x * 3
            pixels[start:start + len(row)] = row
    raster = b"".join(b"\0" + pixels[y * stride:(y + 1) * stride] for y in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header) + _chunk(b"IDAT", zlib.compress(raster)) + _chunk(b"IEND", b"")


def _ocr_png():
    glyphs = {
        "4": ("10010", "10010", "10010", "11111", "00010", "00010", "00010"),
        "0": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
        "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
        "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    }
    scale = 11
    return _png([(34 + (i * 6 + x) * scale, 42 + y * scale, scale, scale, (0, 0, 0))
                 for i, digit in enumerate("4073") for y, row in enumerate(glyphs[digit])
                 for x, pixel in enumerate(row) if pixel == "1"])


def vision_quick_cases():
    """Return the fixed image and text canaries in report order."""
    return (
        QualityCase("position_color_left", _POSITION_PROMPT, "LEFT RED",
                    _png([(26, 30, 100, 100, (220, 15, 15))])),
        QualityCase("position_color_right", _POSITION_PROMPT, "RIGHT BLUE",
                    _png([(194, 30, 100, 100, (20, 35, 220))])),
        QualityCase("ocr_digits", "Transcribe only the four large digits shown in the image. No spaces or other text.",
                    "4073", _ocr_png()),
        QualityCase("arithmetic", "Multiply 17 by 23. Reply with only the numeric result.", 391),
        QualityCase("json_extraction", "An inventory card reads: item meridian; units 7. Return only a valid JSON object "
                    "with keys item (string) and units (integer). No Markdown.", {"item": "meridian", "units": 7}),
        QualityCase("code_boundary", "In a binary search with inclusive bounds, mid is already checked. To search only to "
                    "its right, write the single Python assignment that advances left past mid. No Markdown or explanation.",
                    "left = mid + 1"),
        QualityCase("revision_tracking", "Revision 2 was approved. Revision 3 superseded it and was approved. Revision 4 "
                    "was proposed but rejected. Return only a JSON object for the latest approved revision with keys "
                    "revision (integer) and status (lowercase string).", {"revision": 3, "status": "approved"}),
    )
