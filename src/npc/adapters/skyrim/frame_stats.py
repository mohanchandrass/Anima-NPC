"""Standard-library PNG statistics, so a black capture is visible without an image library at runtime."""

import struct
import zlib

BLACK_LEVEL = 8  # a sampled pixel is black when every channel is below this
MOSTLY_BLACK = 0.9
_CHANNELS = {0: 1, 2: 3, 6: 4}


def frame_stats(raw: bytes, *, max_rows: int = 64, max_columns: int = 256) -> dict | None:
    """size, mean brightness (0-255) and black fraction from a sample of an 8-bit non-interlaced PNG.

    Reads rows stored with filter 0 (what this bridge's own GDI encoder writes); returns None for anything else
    rather than guessing. Only a sample of rows and columns is examined.
    """
    try:
        if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
            return None
        position, header, data = 8, None, bytearray()
        while position + 8 <= len(raw):
            length, kind = struct.unpack(">I4s", raw[position:position + 8])
            body = raw[position + 8:position + 8 + length]
            position += 12 + length
            if kind == b"IHDR":
                header = struct.unpack(">IIBBBBB", body)
            elif kind == b"IDAT":
                data.extend(body)
            elif kind == b"IEND":
                break
        if header is None:
            return None
        width, height, depth, colour, _, _, interlace = header
        channels = _CHANNELS.get(colour)
        if depth != 8 or channels is None or interlace or not width or not height:
            return None
        pixels = zlib.decompress(bytes(data))
        stride = 1 + width * channels
        if len(pixels) < stride * height:
            return None
        total, bright, black = 0, 0, 0
        step_x = max(1, width // max_columns)
        for row in range(0, height, max(1, height // max_rows)):
            line = pixels[row * stride:(row + 1) * stride]
            if line[0] != 0:
                return {"width": width, "height": height, "mean_brightness": None, "black_fraction": None}
            for x in range(0, width, step_x):
                pixel = line[1 + x * channels:1 + (x + 1) * channels][:3]
                bright += sum(pixel) / len(pixel)
                black += max(pixel) < BLACK_LEVEL
                total += 1
        return {"width": width, "height": height, "mean_brightness": round(bright / total, 1),
                "black_fraction": round(black / total, 3)}
    except (struct.error, zlib.error, ValueError, IndexError, ZeroDivisionError):
        return None
