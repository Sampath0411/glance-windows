"""Convert the approved Glance artwork into a multi-size Windows icon.

The source PNG is embedded unchanged: it is never redrawn, recolored, or
cropped. Windows icon sizes are produced by high-quality Lanczos downscaling.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = REPO_ROOT / "assets" / "GlanceIcon.png"
ICON_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def create_icon(output: Path, source: Path | None = None) -> None:
    source = Path(source or DEFAULT_SOURCE)
    if not source.is_file():
        raise FileNotFoundError(f"Glance icon source is missing: {source}")

    with Image.open(source) as opened:
        artwork = opened.convert("RGBA")

    if artwork.width != artwork.height:
        # Preserve the whole artwork by padding instead of stretching or cropping.
        side = max(artwork.size)
        canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        canvas.paste(
            artwork,
            ((side - artwork.width) // 2, (side - artwork.height) // 2),
            artwork,
        )
        artwork = canvas

    if min(artwork.size) < 256:
        raise ValueError("Glance icon source must be at least 256x256 pixels.")

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    artwork.save(output, format="ICO", sizes=ICON_SIZES)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help="Approved PNG artwork (defaults to assets/GlanceIcon.png).",
    )
    args = parser.parse_args()
    create_icon(args.output, args.source)
