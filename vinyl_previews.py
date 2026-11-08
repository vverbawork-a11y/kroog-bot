"""Generate visually consistent vinyl previews from unmodified supplier JPGs.

The circular vinyl is centered in a square with equal exterior margins.
A solid backdrop outside the disc removes the supplier's uneven white
background strips. No per-colour CSS offsets or browser-dependent crops.
"""
from __future__ import annotations

import logging
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT_SIZE = 960
BG = (248, 247, 245)
STAMP = "centered-v1"
ANGLES = tuple(
    (math.cos(i * math.pi / 32), math.sin(i * math.pi / 32))
    for i in range(64) if not 52 <= i <= 76
)


def _locate_disc(im: Image.Image) -> tuple[float, float, float]:
    """Find the outer vinyl edge using concentric lightness differences.

    Returns center X, center Y and radius in fractional source coordinates.
    Only the edge is measured; printed labels and transparent vinyl do not
    change the framing. Low-contrast photos use a conservative centered crop.
    """
    mini = im.resize((220, 220), Image.Resampling.BILINEAR).convert("RGB")
    px = mini.load()

    def edge(cx: int, cy: int, radius: int) -> float:
        total = 0.0
        count = 0
        for dx, dy in ANGLES:
            ix = int(cx + (radius - 4) * dx)
            iy = int(cy + (radius - 4) * dy)
            ox = int(cx + (radius + 4) * dx)
            oy = int(cy + (radius + 4) * dy)
            if not (0 <= ix < 220 and 0 <= iy < 220 and
                    0 <= ox < 220 and 0 <= oy < 220):
                continue
            inside = px[ix, iy]
            outside = px[ox, oy]
            inner = .299 * inside[0] + .587 * inside[1] + .114 * inside[2]
            outer = .299 * outside[0] + .587 * outside[1] + .114 * outside[2]
            # Outer light background should be brighter than vinyl.
            total += max(0., outer - inner)
            count += 1
        if not count:
            return 0.
        return total / count - (abs(cx - 110) + abs(cy - 110)) * .03

    best = (-1., 110, 108, 87)
    for cx in range(98, 123, 4):
        for cy in range(96, 125, 4):
            for radius in range(75, 102, 2):
                score = edge(cx, cy, radius)
                if score > best[0]:
                    best = (score, cx, cy, radius)
    if best[0] < 5:
        # White/translucent vinyl can be almost indistinguishable from white.
        return .5, .49, .395

    _, xc, yc, rc = best
    refined = best
    for cx in range(xc - 3, xc + 4):
        for cy in range(yc - 3, yc + 4):
            for radius in range(rc - 2, rc + 3):
                score = edge(cx, cy, radius)
                if score > refined[0]:
                    refined = (score, cx, cy, radius)
    _, cx, cy, radius = refined
    return cx / 220., cy / 220., radius / 220.


def render(source: Path, target: Path):
    with Image.open(source) as original:
        image = original.convert("RGB")
        cx, cy, radius = _locate_disc(image)
        # Make every vinyl occupy exactly 80% of its square, leaving the
        # same ~10% air on the four sides. Disc never touches the frame.
        pixels_per_output = (radius * image.width) / (OUT_SIZE * .40)
        x0 = cx * image.width - pixels_per_output * OUT_SIZE / 2
        y0 = cy * image.height - pixels_per_output * OUT_SIZE / 2
        crop = image.transform(
            (OUT_SIZE, OUT_SIZE), Image.Transform.AFFINE,
            (pixels_per_output, 0, x0, 0, pixels_per_output, y0),
            resample=Image.Resampling.BICUBIC, fillcolor=BG
        )
        # Uniform matte removes the original white bands, stands and rulers.
        # Soft 1px outer edge retains natural disc contours without aliasing.
        mask = Image.new("L", (OUT_SIZE, OUT_SIZE), 0)
        draw = ImageDraw.Draw(mask)
        margin = OUT_SIZE * .095
        draw.ellipse((margin, margin, OUT_SIZE - margin,
                      OUT_SIZE - margin), fill=255)
        mask = mask.filter(ImageFilter.GaussianBlur(.65))
        preview = Image.new("RGB", (OUT_SIZE, OUT_SIZE), BG)
        preview.paste(crop, (0, 0), mask)
        target.parent.mkdir(parents=True, exist_ok=True)
        preview.save(target, "JPEG", quality=95, subsampling=0,
                     optimize=True, progressive=True)


def generate(source_dir: Path, output_dir: Path) -> int:
    source_dir, output_dir = Path(source_dir), Path(output_dir)
    files = sorted(source_dir.glob("*.jpg"))
    if len(files) != 32:
        raise RuntimeError("Expected 32 original vinyl colour photographs")
    output_dir.mkdir(parents=True, exist_ok=True)
    if ((output_dir / ".version").exists() and
            (output_dir / ".version").read_text() == STAMP and
            all((output_dir / p.name).is_file() for p in files)):
        return 32
    for path in files:
        render(path, output_dir / path.name)
    (output_dir / ".version").write_text(STAMP)
    logging.info("Generated %s centered vinyl previews in %s",
                 len(files), output_dir)
    return len(files)
