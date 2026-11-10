#!/usr/bin/env python3
"""Turn the supplied neon particle artwork into the Tolesh "core" assets.

The artwork arrived as a 5000x5000 JPEG: a cyan particle cloud on a flat
``#051D29`` background. A rectangle of navy dropped onto the warm ink interface
would look like a pasted screenshot, so the background is converted into real
alpha here instead of being hidden with a blend mode.

How the alpha is derived (not guessed):

* the source background is a *single* colour — sampled from the four corners
  and asserted to be uniform, so the extraction cannot silently degrade if a
  different render is supplied;
* ``alpha = ((max(r,g,b) - bg_lum) / (255 - bg_lum)) ** gamma`` with a small
  noise floor, because a pixel that differs from the background is, by
  definition, how much of the particle covers it;
* the RGB is left untouched. Un-premultiplying against a dark background
  saturates faint dots to white and destroys the cyan hue; keeping the sampled
  colour is what makes the faint mesh read as faint rather than as dark specks.

Outputs (all under ``web/assets/``):

    tolesh-core-512.webp    for the rail orb and narrow viewports
    tolesh-core-1024.webp   for the empty-state core (2x on retina)

WebP only: the asset needs an alpha channel, and every engine Tolesh targets
(Chromium, QtWebEngine, current Safari/Firefox) decodes it. A PNG fallback was
dropped because it was 2x larger than the WebP for identical pixels.

Usage::

    python3 scripts/build_core_assets.py --source path/to/1410.jpg
    python3 scripts/build_core_assets.py --source ... --preview   # eyeball it

The script is deterministic: same input, same bytes out.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "web" / "assets"

# A pixel whose brightest channel is within this many levels of the background
# is JPEG noise, not a particle.
NOISE_FLOOR = 4.0
# < 1 lifts the faint mesh so the interior structure survives on the darker
# application background; 1.0 would keep the source's own contrast curve.
GAMMA = 0.86
# The artwork's particles bleed to the very edge of the frame, so a crop to the
# content plus this fraction of padding keeps the glow from being clipped.
PAD = 0.02


def _uniform_background(arr: np.ndarray) -> tuple[int, int, int]:
    h, w, _ = arr.shape
    corners = np.array([arr[0, 0], arr[0, w - 1], arr[h - 1, 0], arr[h - 1, w - 1]])
    # Per-channel spread, not the spread across channels: a flat backdrop has
    # four identical corners, while the channel values themselves differ.
    if float((corners.max(axis=0) - corners.min(axis=0)).max()) > 6:
        raise SystemExit(
            "source background is not uniform "
            f"({corners.tolist()}); this script assumes a flat backdrop")
    return tuple(int(round(v)) for v in corners.mean(axis=0))


def extract(source: Path) -> tuple[Image.Image, dict]:
    im = Image.open(source).convert("RGB")
    arr = np.asarray(im).astype(np.float32)
    bg = _uniform_background(arr)
    bg_lum = float(max(bg))

    # Distance from the backdrop, measured on the brightest channel: a cyan
    # particle at (0,220,255) must count as fully covered even though its red
    # channel is 0.
    lum = arr.max(axis=2)
    span = 255.0 - bg_lum - NOISE_FLOOR
    diff = np.clip(lum - bg_lum - NOISE_FLOOR, 0.0, None)
    # Divide by the span, not by (255 - bg_lum): the brightest particle then
    # reaches alpha 1.0 exactly, so highlights are not permanently translucent.
    alpha = np.clip(diff / span, 0.0, 1.0) ** GAMMA

    # Crop to the particles so the asset has no dead margin: the core is sized
    # by its visible diameter in the layout, not by an invisible frame.
    solid = alpha > 0.02
    ys, xs = np.where(solid)
    if not len(xs):
        raise SystemExit("no particles found — is this the right image?")
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    side = max(x1 - x0, y1 - y0)
    pad = int(side * PAD)
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    half = side // 2 + pad
    left, top = cx - half, cy - half
    box = (max(left, 0), max(top, 0),
           min(left + half * 2, im.width), min(top + half * 2, im.height))

    rgba = np.dstack([arr, alpha * 255.0]).astype(np.uint8)
    out = Image.fromarray(rgba, "RGBA").crop(box)

    stats = {
        "source": source.name,
        "source_size": list(im.size),
        "background": "#%02X%02X%02X" % bg,
        "crop": list(box),
        "cropped_size": list(out.size),
        "covered_pixels_pct": round(float((alpha > 0.05).mean()) * 100, 2),
        "max_alpha_pct": round(float(alpha.max()) * 100, 1),
        "gamma": GAMMA,
        "noise_floor": NOISE_FLOOR,
    }
    return out, stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="the supplied artwork (JPEG)")
    ap.add_argument("--preview", action="store_true",
                    help="also write a PNG preview composited on the app background")
    args = ap.parse_args()

    src = Path(args.source)
    if not src.is_file():
        print(json.dumps({"error": f"missing source: {src}"}))
        return 1

    core, stats = extract(src)
    ASSETS.mkdir(parents=True, exist_ok=True)
    written = []
    for size in (512, 1024):
        img = core.resize((size, size), Image.LANCZOS)
        webp = ASSETS / f"tolesh-core-{size}.webp"
        # alpha_quality below 100 is deliberate: the particle field's soft halo
        # tolerates it invisibly and it is where most of the bytes live.
        img.save(webp, "WEBP", quality=82, alpha_quality=80, method=6)
        written.append({"path": str(webp.relative_to(ROOT)), "bytes": webp.stat().st_size})

    stats["written"] = written
    meta = ASSETS / "tolesh-core.json"
    meta.write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")

    if args.preview:
        # The application background is oklch(0.135 0.006 62) ≈ #1C1712.
        bg = Image.new("RGBA", (900, 900), (28, 23, 18, 255))
        shot = core.resize((900, 900), Image.LANCZOS)
        bg.alpha_composite(shot)
        bg.convert("RGB").save(ROOT / "docs" / "img" / "core-preview.png")
        stats["preview"] = "docs/img/core-preview.png"

    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
