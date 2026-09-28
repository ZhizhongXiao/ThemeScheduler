"""Build the small, self-contained font used by the operation GUI."""

from __future__ import annotations

import argparse
import os
import string
import tempfile
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

FAMILY = "ThemeScheduler Source Han Sans"
FULL_NAME = f"{FAMILY} Regular"
POSTSCRIPT_NAME = "ThemeSchedulerSourceHanSans-Regular"
TEXT_SUFFIXES = {".css", ".html", ".js"}


def collect_frontend_text(frontends: tuple[Path, ...]) -> str:
    characters = set(string.printable)
    for frontend in frontends:
        if not frontend.is_dir():
            continue
        for path in sorted(frontend.rglob("*")):
            if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
                characters.update(path.read_text(encoding="utf-8"))
    return "".join(sorted(characters))


def rename_font(font: TTFont) -> None:
    replacements = {
        1: FAMILY,
        2: "Regular",
        3: f"{FAMILY}; Regular",
        4: FULL_NAME,
        6: POSTSCRIPT_NAME,
        16: FAMILY,
        17: "Regular",
    }
    name_table = font["name"]
    for record in list(name_table.names):
        value = replacements.get(record.nameID)
        if value is None:
            continue
        name_table.setName(
            value,
            record.nameID,
            record.platformID,
            record.platEncID,
            record.langID,
        )


def build_subset(
    source: Path,
    frontends: tuple[Path, ...],
    output: Path,
) -> None:
    options = subset.Options()
    # fontTools accepts "*" here, though its type stub only declares integer IDs.
    options.__dict__["name_IDs"] = ["*"]
    options.name_legacy = True
    options.__dict__["name_languages"] = ["*"]
    options.notdef_glyph = True
    options.notdef_outline = True
    options.recommended_glyphs = True

    font = subset.load_font(
        str(source),
        options,
        dontLoadGlyphNames=False,
        lazy=False,
    )
    subsetter = subset.Subsetter(options=options)
    subsetter.populate(text=collect_frontend_text(frontends))
    subsetter.subset(font)
    rename_font(font)

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=output.parent,
            prefix=f".{output.stem}-",
            suffix=output.suffix,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        subset.save_font(font, str(temporary_path), options)
        os.replace(temporary_path, output)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def parse_args() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument(
        "--frontend",
        action="append",
        type=Path,
        help="Frontend root to scan; may be supplied more than once.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=project_root / "ui" / "fonts" / "ThemeSchedulerSourceHanSans.otf",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = Path(__file__).resolve().parents[1]
    frontends = tuple(
        path.resolve()
        for path in (
            args.frontend
            or [
                project_root / "ui",
            ]
        )
    )
    build_subset(
        args.source.resolve(),
        frontends,
        args.output.resolve(),
    )
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
