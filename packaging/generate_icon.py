"""Generate the deterministic ThemeScheduler multi-size Windows icon."""

from __future__ import annotations

import argparse
import struct
from collections.abc import Sequence
from pathlib import Path

SIZES = (16, 24, 32, 48, 64, 128, 256)


def _inside_rounded_square(x: float, y: float, size: int) -> bool:
    radius = size * 0.22
    left = radius
    right = size - radius
    if left <= x <= right or left <= y <= right:
        return True
    corner_x = left if x < left else right
    corner_y = left if y < left else right
    return (x - corner_x) ** 2 + (y - corner_y) ** 2 <= radius**2


def _pixel(x: int, y: int, size: int) -> tuple[int, int, int, int]:
    scale = 4
    samples = 0
    red = green = blue = alpha = 0
    for sample_y in range(scale):
        for sample_x in range(scale):
            px = x + (sample_x + 0.5) / scale
            py = y + (sample_y + 0.5) / scale
            if not _inside_rounded_square(px, py, size):
                continue
            t = (px + py) / (2 * size)
            background = (
                round(111 + (92 - 111) * t),
                round(99 + (132 - 99) * t),
                round(255 + (255 - 255) * t),
            )
            cx = cy = size / 2
            radius = size * 0.29
            distance = (px - cx) ** 2 + (py - cy) ** 2
            if distance <= radius**2:
                color = (241, 245, 255) if px < cx else background
                if abs(px - cx) <= max(0.55, size / 180):
                    color = (255, 255, 255)
            else:
                color = background
            red += color[0]
            green += color[1]
            blue += color[2]
            alpha += 255
            samples += 1
    if samples == 0:
        return 0, 0, 0, 0
    return (
        round(red / samples),
        round(green / samples),
        round(blue / samples),
        round(alpha / (scale * scale)),
    )


def _dib(size: int) -> bytes:
    pixels = bytearray()
    for y in range(size - 1, -1, -1):
        for x in range(size):
            red, green, blue, alpha = _pixel(x, y, size)
            pixels.extend((blue, green, red, alpha))
    mask_stride = ((size + 31) // 32) * 4
    mask = bytes(mask_stride * size)
    header = struct.pack(
        "<IIIHHIIIIII",
        40,
        size,
        size * 2,
        1,
        32,
        0,
        len(pixels),
        0,
        0,
        0,
        0,
    )
    return header + pixels + mask


def icon_bytes() -> bytes:
    images = [_dib(size) for size in SIZES]
    header_size = 6 + 16 * len(images)
    entries = bytearray()
    offset = header_size
    for size, image in zip(SIZES, images, strict=True):
        encoded_size = 0 if size == 256 else size
        entries.extend(
            struct.pack(
                "<BBBBHHII",
                encoded_size,
                encoded_size,
                0,
                0,
                1,
                32,
                len(image),
                offset,
            )
        )
        offset += len(image)
    return struct.pack("<HHH", 0, 1, len(images)) + entries + b"".join(images)


def write_icon(path: Path) -> Path:
    path = Path(path)
    data = icon_bytes()
    if path.exists():
        if path.read_bytes() != data:
            raise FileExistsError(f"Refusing to overwrite a different icon: {path}")
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="generate-themescheduler-icon")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("assets") / "ThemeScheduler.ico",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        print(write_icon(args.output))
        return 0
    except (FileExistsError, OSError, ValueError) as exc:
        print(f"error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
