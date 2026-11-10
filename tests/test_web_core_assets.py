"""The interface artwork and the markup that points at it.

The particle core is a binary asset that the layout depends on: if it 404s the
hero is an empty square, and nothing in the Python test suite would notice. These
tests fail on a missing, corrupt or mis-referenced asset, and they keep the
"state" vocabulary in ``motion.js`` and ``app.css`` from drifting apart.

They do not re-run the build script: the output depends on the local libwebp
version, so a byte comparison would fail for reasons that have nothing to do
with the product. The extraction maths is tested directly on a synthetic image
instead.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
ASSETS = WEB / "assets"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ------------------------------------------------------------------ the files
@pytest.mark.parametrize("name", ["tolesh-core-512.webp", "tolesh-core-1024.webp"])
def test_core_asset_decodes_with_alpha(name):
    path = ASSETS / name
    assert path.is_file(), f"missing core asset: {path}"
    assert path.stat().st_size > 20_000, "core asset is suspiciously small"
    with Image.open(path) as im:
        assert im.format == "WEBP"
        assert im.mode in ("RGBA", "LA"), "the core must keep a transparent background"
        side = int(name.split("-")[-1].split(".")[0])
        assert im.size == (side, side)
        alpha = im.getchannel("A")
        lo, hi = alpha.getextrema()
        assert lo == 0, "background must be fully transparent"
        assert hi >= 250, "the brightest particles must be fully opaque"


def test_core_metadata_matches_the_assets():
    meta = json.loads(_read(ASSETS / "tolesh-core.json"))
    assert meta["background"].startswith("#")
    for entry in meta["written"]:
        path = ROOT / entry["path"]
        assert path.is_file(), f"metadata lists a file that does not exist: {path}"
        assert path.stat().st_size == entry["bytes"], "asset changed without rebuilding metadata"
    # A flat backdrop is what makes the alpha extraction valid at all.
    assert meta["covered_pixels_pct"] > 5
    assert meta["max_alpha_pct"] == 100.0


def test_index_references_every_local_asset():
    """No dead ``src``/``href``: a typo here is a silent 404 in the browser."""
    html = _read(WEB / "index.html")
    refs = re.findall(r'(?:src|href)="(\./[^"#?]+)"', html)
    assert refs, "index.html references no local files"
    missing = [r for r in refs if not (WEB / r[2:]).is_file()]
    assert not missing, f"index.html points at missing files: {missing}"


def test_stylesheets_reference_existing_images():
    css = "".join(_read(p) for p in (WEB / "styles").glob("*.css"))
    urls = [u for u in re.findall(r'url\("(\.\./[^"]+)"\)', css) if not u.startswith("data:")]
    missing = [u for u in urls if not (WEB / "styles" / u).resolve().is_file()]
    assert not missing, f"CSS points at missing files: {missing}"


def test_core_image_declares_its_intrinsic_size():
    """Width/height on the <img> keep the hero from shifting as it decodes."""
    html = _read(WEB / "index.html")
    tag = re.search(r'<img class="core-art".*?/>', html, re.S)
    assert tag, "core image markup not found"
    assert 'width="512"' in tag.group(0) and 'height="512"' in tag.group(0)
    assert 'alt=""' in tag.group(0), "the artwork is decorative; the caption carries meaning"
    assert "srcset" in tag.group(0), "the 2x variant is never requested without srcset"


# ---------------------------------------------------------------- state names
def test_every_core_state_has_styling():
    js = _read(WEB / "js" / "motion.js")
    block = re.search(r"CORE_LABELS = \{(.*?)\};", js, re.S)
    assert block, "CORE_LABELS not found in motion.js"
    states = set(re.findall(r"^\s*(\w+):\s*'", block.group(1), re.M))
    assert {"idle", "listening", "thinking"} <= states

    css = _read(WEB / "styles" / "app.css")
    styled = set(re.findall(r'data-core-state="(\w+)"', css))
    # "idle" is the base style and needs no override rule.
    unstyled = states - styled - {"idle"}
    assert not unstyled, f"states with no CSS: {sorted(unstyled)}"


def test_core_labels_are_honest_words():
    js = _read(WEB / "js" / "motion.js")
    block = re.search(r"CORE_LABELS = \{(.*?)\};", js, re.S).group(1)
    labels = dict(re.findall(r"(\w+):\s*'([^']*)'", block))
    assert labels["idle"] == "Ready"
    assert labels["offline"] == "Engine offline"
    # No state may claim a capability the app does not have.
    assert not any("AI" in v and "offline" not in v for v in labels.values())


# ------------------------------------------------------- extraction algorithm
def test_extraction_turns_a_flat_backdrop_into_alpha(tmp_path):
    """The build script's core promise, tested without the 5000 px source."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "build_core_assets", ROOT / "scripts" / "build_core_assets.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    bg = (5, 29, 41)
    im = Image.new("RGB", (200, 200), bg)
    # A bright square well inside the frame, plus one dim pixel near the edge.
    for x in range(60, 140):
        for y in range(60, 140):
            im.putpixel((x, y), (0, 220, 255))
    im.putpixel((10, 10), (12, 60, 78))
    src = tmp_path / "synthetic.jpg"
    im.save(src, "JPEG", quality=95)

    core, stats = mod.extract(src)
    assert core.mode == "RGBA"
    assert stats["background"] == "#051D29"
    alpha = core.getchannel("A")
    assert alpha.getextrema()[0] == 0, "backdrop must become transparent"
    assert alpha.getextrema()[1] == 255, "the solid block must be opaque"
    # Cropped to the particles (plus padding), not to the 200 px frame.
    assert core.width < 200 and core.width > 80


def test_extraction_refuses_a_non_uniform_background(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "build_core_assets", ROOT / "scripts" / "build_core_assets.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    im = Image.new("RGB", (64, 64), (5, 29, 41))
    im.putpixel((0, 0), (200, 10, 10))  # a corner that is not the backdrop
    src = tmp_path / "gradient.png"
    im.save(src)
    with pytest.raises(SystemExit):
        mod.extract(src)
