"""The particle orb, rendered and measured in a real browser.

The orb replaced a still image and a CSS spin. The properties that matter are
visual, so they are checked by reading pixels rather than by asserting that a
class is present:

  * something is actually drawn, and it is not a flat disc;
  * a louder microphone makes it brighter and larger — the difference has to
    be measurable, or "reactive" is a claim rather than a behaviour;
  * the four states look different from one another;
  * reduced motion stops it spinning but keeps it responsive;
  * destroying it stops the animation frame loop instead of leaking one.
"""

from __future__ import annotations

import functools
import http.server
import math
import struct
import threading
import wave
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright.sync_playwright

WEB = Path(__file__).resolve().parents[1] / "web"


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def site():
    handler = functools.partial(_Quiet, directory=str(WEB))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        try:
            instance = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        except Exception as exc:
            pytest.skip(f"Chromium is not available: {exc}")
        yield instance
        instance.close()


@pytest.fixture
def page(browser, site):
    context = browser.new_context(viewport={"width": 900, "height": 700})
    page = context.new_page()
    page.set_default_timeout(15_000)
    page.goto(f"{site}/index.html", wait_until="load")
    yield page
    context.close()


# A 160 x 160 canvas, so the pixels are readable and the sphere is not a
# thumbnail. The real orb is 68 px and uses the same code path.
MOUNT = """
async (opts) => {
  const { ParticleOrb } = await import('/js/orb.js');
  const canvas = document.createElement('canvas');
  canvas.id = 'probe';
  canvas.style.cssText = 'position:fixed;left:0;top:0;width:160px;height:160px;'
    + 'z-index:9999;background:#05090f';
  document.body.appendChild(canvas);
  window.__orb = new ParticleOrb(canvas, opts || {});
  window.__orb.resize();
  window.__orb.start();
  return true;
}
"""

STATS = """
() => {
  const canvas = document.getElementById('probe');
  const ctx = canvas.getContext('2d');
  const { width, height } = canvas;
  const data = ctx.getImageData(0, 0, width, height).data;
  // A dot is a pixel with real alpha; the soft halo around the sphere is not,
  // so counting every non-zero pixel would call the whole disc "lit".
  let lit = 0, total = 0, sumR = 0, sumG = 0, sumB = 0, maxLum = 0, lumSum = 0;
  let alphaSum = 0;
  for (let i = 0; i < data.length; i += 4) {
    const a = data[i + 3];
    total += 1;
    alphaSum += a;
    if (a > 60) {
      lit += 1;
      const lum = data[i] + data[i + 1] + data[i + 2];
      sumR += data[i]; sumG += data[i + 1]; sumB += data[i + 2];
      lumSum += lum;
      maxLum = Math.max(maxLum, lum);
    }
  }
  return {
    width, height,
    lit, total,
    coverage: lit / total,
    meanR: lit ? sumR / lit : 0,
    meanG: lit ? sumG / lit : 0,
    meanB: lit ? sumB / lit : 0,
    meanLum: lit ? lumSum / lit : 0,
    maxLum,
    // Total light emitted over the whole canvas. This is the number that moves
    // when the sphere brightens: individual dots are already near white, so
    // their mean stays flat while the field as a whole gains light.
    light: alphaSum / total,
  };
}
"""


def settle(page, ms=500):
    page.wait_for_timeout(ms)


def test_the_orb_draws_particles_rather_than_a_flat_disc(page):
    page.evaluate(MOUNT)
    settle(page)

    stats = page.evaluate(STATS)

    assert stats["width"] > 0
    # A filled circle would light most of its own bounding box. A particle
    # field lights a fraction of it and leaves gaps between the dots.
    assert 0.05 < stats["coverage"] < 0.92, stats
    # The tint is cyan: green and blue well above red.
    assert stats["meanG"] > stats["meanR"] * 1.4, stats
    assert stats["meanB"] > stats["meanR"] * 1.4, stats


def test_a_louder_microphone_makes_it_brighter_and_larger(page):
    page.evaluate(MOUNT)
    page.evaluate("window.__orb.setState('listening')")
    settle(page, 300)
    quiet = page.evaluate(STATS)

    page.evaluate("window.__orb.setLevel(0.95)")
    settle(page, 400)
    loud = page.evaluate(STATS)

    # The peak pixel is already near white at both levels, so brightness is
    # measured across the whole field rather than at its brightest point.
    assert loud["light"] > quiet["light"] * 1.05, (quiet, loud)
    assert loud["coverage"] > quiet["coverage"], (quiet, loud)


def test_silence_and_full_scale_are_far_apart(page):
    """A gate that moves by one pixel is not a visualisation."""
    page.evaluate(MOUNT)
    page.evaluate("window.__orb.setState('listening'); window.__orb.setLevel(0)")
    settle(page, 400)
    silent = page.evaluate(STATS)
    page.evaluate("window.__orb.setLevel(1)")
    settle(page, 400)
    full = page.evaluate(STATS)

    ratio = full["light"] / max(0.1, silent["light"])
    assert ratio > 1.15, f"silence {silent['light']} vs full {full['light']}"


def test_the_four_states_look_different(page):
    page.evaluate(MOUNT)
    seen = {}
    for state in ("idle", "listening", "speaking", "paused"):
        page.evaluate(f"window.__orb.setState('{state}'); window.__orb.setLevel(0.5)")
        settle(page, 350)
        stats = page.evaluate(STATS)
        seen[state] = (round(stats["light"], 2), round(stats["coverage"], 4))

    assert seen["paused"][0] < seen["idle"][0], seen
    assert seen["listening"][0] > seen["idle"][0], seen
    assert len(set(seen.values())) >= 3, seen


def test_reduced_motion_draws_a_still_sphere_that_still_reacts(browser, site):
    context = browser.new_context(viewport={"width": 900, "height": 700},
                                  reduced_motion="reduce")
    page = context.new_page()
    try:
        page.goto(f"{site}/index.html", wait_until="load")
        page.evaluate(MOUNT)
        page.evaluate("window.__orb.setState('listening'); window.__orb.setLevel(0)")
        settle(page, 400)
        silent = page.evaluate(STATS)

        page.evaluate("window.__orb.setLevel(1)")
        settle(page, 400)
        loud = page.evaluate(STATS)

        # Still drawn...
        assert silent["coverage"] > 0.05, silent
        # ...still reacting, because the level is information, not decoration...
        assert loud["maxLum"] > silent["maxLum"], (silent, loud)
        # ...and not spinning: with no animation frame loop, the rotation
        # cannot advance, so two samples a second apart are identical.
        first = page.evaluate(STATS)
        page.wait_for_timeout(900)
        second = page.evaluate(STATS)
        assert first["coverage"] == second["coverage"], "reduced motion still animated"
    finally:
        context.close()


def test_the_orb_is_decorative_for_a_screen_reader(page):
    canvas = page.locator("#orbField")
    assert canvas.get_attribute("aria-hidden") == "true"
    # The control itself keeps its label, which is what a screen reader reads.
    assert page.locator("#voiceOrb").get_attribute("aria-label")


def test_the_old_still_image_and_its_spin_are_gone(page):
    html = page.content()
    assert "orb-art" not in html
    css = page.evaluate("""() => {
      const el = document.getElementById('orbField');
      return getComputedStyle(el).animationName;
    }""")
    assert css == "none", "the orb is spinning on a CSS timer again"


def test_destroy_stops_the_frame_loop(page):
    page.evaluate(MOUNT)
    settle(page, 200)
    frames = page.evaluate("""async () => {
      let count = 0;
      const tick = () => { count += 1; requestAnimationFrame(tick); };
      requestAnimationFrame(tick);
      await new Promise((r) => setTimeout(r, 300));
      return count;
    }""")
    assert frames > 5, "the page is not animating at all"

    # First prove it really is animating: two samples while running differ,
    # because the sphere is turning.
    first = page.evaluate(STATS)
    page.wait_for_timeout(400)
    second = page.evaluate(STATS)
    assert (first["lit"], first["light"]) != (second["lit"], second["light"]), \
        "the orb was not animating to begin with"

    page.evaluate("window.__orb.destroy()")
    settle(page, 400)
    after = page.evaluate(STATS)
    page.wait_for_timeout(700)
    later = page.evaluate(STATS)

    # A stopped sphere is pixel-identical, which is the observable form of
    # "the animation frame loop ended".
    assert (later["lit"], later["light"]) == (after["lit"], after["light"]), \
        "the orb is still animating after destroy()"


def test_the_microphone_opens_even_when_speech_recognition_is_missing(page):
    """The orb must react in a browser with no speech engine.

    ``start()`` used to return before the meter started when
    ``SpeechRecognition`` was absent, so the sphere sat inert and the comment
    promising otherwise was false. The level display does not depend on the
    speech service, so the microphone opens first.
    """
    result = page.evaluate("""async () => {
      const { VoiceController } = await import('/js/voice.js');
      const savedA = window.SpeechRecognition;
      const savedB = window.webkitSpeechRecognition;
      delete window.SpeechRecognition;
      delete window.webkitSpeechRecognition;
      const seen = { started: 0, stopped: 0, errors: [] };
      const meter = {
        active: false,
        start() {
          seen.started += 1;
          this.active = true;
          return Promise.resolve(true);
        },
        stop() { seen.stopped += 1; this.active = false; },
        setState() {},
      };
      const voice = new VoiceController({
        meter,
        onError: (m) => seen.errors.push(m),
      });
      voice.start();
      const afterStart = { ...seen, active: meter.active };
      voice.toggle();
      const afterToggle = { ...seen, active: meter.active };
      window.SpeechRecognition = savedA;
      window.webkitSpeechRecognition = savedB;
      return { afterStart, afterToggle };
    }""")

    assert result["afterStart"]["started"] == 1, "the microphone never opened"
    assert result["afterStart"]["active"] is True
    assert result["afterStart"]["errors"], "the missing engine was not reported"
    # And the same click turns it off again, so the device is not left open.
    assert result["afterToggle"]["stopped"] == 1
    assert result["afterToggle"]["active"] is False
