#!/usr/bin/env python3
"""CI pre-flight smoke check: assert a PNG is predominantly #00C853 GREEN.

Samples the central 200x200 px region of the image and asserts that >= 90% of
pixels match #00C853 (R=0, G=200, B=83) within a per-channel tolerance of 20.

Usage:
    python3 scripts/check_green_feed.py <image.png>

Exits 0 on success, non-zero on failure (prints actual dominant color).
"""

import struct
import sys
import zlib


# Target color: #00C853
TARGET_R = 0
TARGET_G = 200
TARGET_B = 83
TOLERANCE = 20
REGION_SIZE = 200
MIN_COVERAGE = 0.90


def decode_png_to_rgb(path: str) -> tuple[list[list[tuple[int, int, int]]], int, int]:
    """Decode a PNG file to a 2D list of (r, g, b) tuples."""
    with open(path, "rb") as f:
        data = f.read()

    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path}: not a valid PNG file")

    # Parse chunks to find IHDR and IDAT
    ihdr = None
    idat_parts = []
    pos = 8
    while pos < len(data):
        if pos + 8 > len(data):
            break
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        chunk_type = data[pos + 4 : pos + 8]
        chunk_data = data[pos + 8 : pos + 8 + length]
        if chunk_type == b"IHDR":
            ihdr = chunk_data
        elif chunk_type == b"IDAT":
            idat_parts.append(chunk_data)
        elif chunk_type == b"IEND":
            break
        pos += 8 + length + 4

    if ihdr is None:
        raise ValueError(f"{path}: missing IHDR chunk")
    if not idat_parts:
        raise ValueError(f"{path}: missing IDAT chunk")

    width, height, bit_depth, color_type = struct.unpack(">IIBB", ihdr[:10])

    if bit_depth != 8:
        raise ValueError(f"{path}: only 8-bit depth supported, got {bit_depth}")
    if color_type not in (2, 6):
        raise ValueError(
            f"{path}: only RGB (2) or RGBA (6) color type supported, got {color_type}"
        )

    channels = 3 if color_type == 2 else 4
    raw = zlib.decompress(b"".join(idat_parts))

    # Reconstruct scanlines with PNG filter reversal
    pixels = []
    stride = width * channels
    prev_row = bytes(stride)
    idx = 0
    for _ in range(height):
        filter_type = raw[idx]
        idx += 1
        row = bytearray(raw[idx : idx + stride])
        idx += stride

        if filter_type == 0:  # None
            pass
        elif filter_type == 1:  # Sub
            for i in range(channels, stride):
                row[i] = (row[i] + row[i - channels]) & 0xFF
        elif filter_type == 2:  # Up
            for i in range(stride):
                row[i] = (row[i] + prev_row[i]) & 0xFF
        elif filter_type == 3:  # Average
            for i in range(stride):
                a = row[i - channels] if i >= channels else 0
                b = prev_row[i]
                row[i] = (row[i] + (a + b) // 2) & 0xFF
        elif filter_type == 4:  # Paeth
            for i in range(stride):
                a = row[i - channels] if i >= channels else 0
                b = prev_row[i]
                c = prev_row[i - channels] if i >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                row[i] = (row[i] + pr) & 0xFF
        else:
            raise ValueError(f"Unknown PNG filter type {filter_type}")

        row_pixels = []
        for x in range(width):
            r = row[x * channels]
            g = row[x * channels + 1]
            b = row[x * channels + 2]
            row_pixels.append((r, g, b))
        pixels.append(row_pixels)
        prev_row = bytes(row)

    return pixels, width, height


def matches_green(r: int, g: int, b: int) -> bool:
    return (
        abs(r - TARGET_R) <= TOLERANCE
        and abs(g - TARGET_G) <= TOLERANCE
        and abs(b - TARGET_B) <= TOLERANCE
    )


def dominant_color(pixels_region: list[tuple[int, int, int]]) -> tuple[int, int, int]:
    """Return the average color of sampled pixels."""
    total = len(pixels_region)
    if total == 0:
        return (0, 0, 0)
    avg_r = sum(p[0] for p in pixels_region) // total
    avg_g = sum(p[1] for p in pixels_region) // total
    avg_b = sum(p[2] for p in pixels_region) // total
    return (avg_r, avg_g, avg_b)


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <image.png>", file=sys.stderr)
        return 2

    path = sys.argv[1]
    try:
        pixels, width, height = decode_png_to_rgb(path)
    except (OSError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    # Sample the central REGION_SIZE x REGION_SIZE region
    cx = width // 2
    cy = height // 2
    half = REGION_SIZE // 2
    x0 = max(0, cx - half)
    y0 = max(0, cy - half)
    x1 = min(width, cx + half)
    y1 = min(height, cy + half)

    region_pixels = []
    for y in range(y0, y1):
        for x in range(x0, x1):
            region_pixels.append(pixels[y][x])

    total = len(region_pixels)
    if total == 0:
        print("ERROR: sampled region is empty (image too small?)", file=sys.stderr)
        return 2

    green_count = sum(1 for (r, g, b) in region_pixels if matches_green(r, g, b))
    coverage = green_count / total

    print(
        f"Green feed check: {green_count}/{total} pixels match #00C853 "
        f"(tolerance={TOLERANCE}) — coverage={coverage:.1%}"
    )

    if coverage >= MIN_COVERAGE:
        print("PASS: green feed verified.")
        return 0
    else:
        dom = dominant_color(region_pixels)
        print(
            f"FAIL: coverage {coverage:.1%} < {MIN_COVERAGE:.0%}. "
            f"Actual dominant color: #{dom[0]:02X}{dom[1]:02X}{dom[2]:02X} "
            f"(R={dom[0]}, G={dom[1]}, B={dom[2]})"
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
