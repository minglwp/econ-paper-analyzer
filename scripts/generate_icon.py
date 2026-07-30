from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw


CANVAS_SIZE = 1024
ICON_SIZES = (
    (16, "icon_16x16.png"),
    (32, "icon_16x16@2x.png"),
    (32, "icon_32x32.png"),
    (64, "icon_32x32@2x.png"),
    (128, "icon_128x128.png"),
    (256, "icon_128x128@2x.png"),
    (256, "icon_256x256.png"),
    (512, "icon_256x256@2x.png"),
    (512, "icon_512x512.png"),
    (1024, "icon_512x512@2x.png"),
)


def draw_icon() -> Image.Image:
    image = Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (64, 64, 960, 960),
        radius=190,
        fill=(247, 249, 246, 255),
        outline=(31, 42, 43, 255),
        width=22,
    )

    grid_color = (205, 216, 210, 255)
    for position in (340, 484, 628):
        draw.line((210, position, 814, position), fill=grid_color, width=12)
    draw.line((220, 722, 818, 722), fill=(31, 42, 43, 255), width=24)
    draw.line((220, 722, 220, 270), fill=(31, 42, 43, 255), width=24)

    bar_color = (43, 138, 105, 255)
    draw.rounded_rectangle((292, 532, 382, 710), radius=22, fill=bar_color)
    draw.rounded_rectangle((448, 430, 538, 710), radius=22, fill=bar_color)
    draw.rounded_rectangle((604, 310, 694, 710), radius=22, fill=bar_color)

    line_color = (222, 92, 74, 255)
    points = [(270, 500), (430, 448), (590, 350), (752, 250)]
    draw.line(points, fill=line_color, width=28, joint="curve")
    for x, y in points:
        draw.ellipse((x - 27, y - 27, x + 27, y + 27), fill=line_color)
    return image


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--png", type=Path, required=True)
    parser.add_argument("--iconset", type=Path, required=True)
    args = parser.parse_args()

    image = draw_icon()
    args.png.parent.mkdir(parents=True, exist_ok=True)
    args.iconset.mkdir(parents=True, exist_ok=True)
    image.save(args.png)
    for size, filename in ICON_SIZES:
        resized = image.resize((size, size), Image.Resampling.LANCZOS)
        resized.save(args.iconset / filename)


if __name__ == "__main__":
    main()
