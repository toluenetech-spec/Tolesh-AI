"""The microphone level display, measured in a real browser.

Chromium's fake capture device is fed a generated 440 Hz tone, so the analyser
is reading genuine audio: a level of zero here would mean the meter is not
connected to the microphone at all. The tests also check the two things a
visualisation usually gets wrong — it must stop when the session stops, and it
must not draw anything when there is no permission.
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


@pytest.fixture(scope="module")
def tone(tmp_path_factory) -> Path:
    """Ten seconds of a 440 Hz sine, so the fake device always has audio."""
    return _wav(tmp_path_factory.mktemp("mic") / "tone.wav", amplitude=11000)


@pytest.fixture(scope="module")
def quiet_tone(tmp_path_factory) -> Path:
    """The same tone, 25 dB quieter — a level meter must tell them apart."""
    return _wav(tmp_path_factory.mktemp("mic-quiet") / "quiet.wav", amplitude=500)


def _wav(path: Path, *, amplitude: int) -> Path:
    rate = 48000
    frames = bytearray()
    for i in range(rate * 10):
        frames += struct.pack("<h", int(amplitude * math.sin(2 * math.pi * 440 * i / rate)))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(frames))
    return path


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):     # the suite's output stays readable
        pass


@pytest.fixture(scope="module")
def site():
    handler = functools.partial(_Quiet, directory=str(WEB))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="module")
def playwright_instance():
    with sync_playwright() as pw:
        yield pw


@pytest.fixture(scope="module")
def browser(playwright_instance, tone):
    try:
        instance = playwright_instance.chromium.launch(headless=True,
                                                       args=_launch_args(tone))
    except Exception as exc:          # no browser binaries on this machine
        pytest.skip(f"Chromium is not available: {exc}")
    yield instance
    instance.close()


def _launch_args(wav: Path) -> list[str]:
    return [
        "--no-sandbox",
        "--use-fake-device-for-media-stream",
        "--use-fake-ui-for-media-stream",
        f"--use-file-for-fake-audio-capture={wav}",
        "--autoplay-policy=no-user-gesture-required",
    ]


@pytest.fixture
def page(browser, site):
    context = browser.new_context(permissions=["microphone"])
    page = context.new_page()
    page.set_default_timeout(15_000)
    page.goto(f"{site}/index.html", wait_until="load")
    yield page
    context.close()


METER = """
async () => {
  const { MicMeter } = await import('/js/meter.js');
  window.__levels = [];
  window.__errors = [];
  window.__m = new MicMeter({
    onError: (m) => window.__errors.push(m),
    onLevel: (l) => window.__levels.push(l),
  });
  return true;
}
"""


def measure(page, wait_ms=900):
    return page.evaluate(
        """async (wait) => {
             await new Promise((r) => setTimeout(r, wait));
             const orb = document.getElementById('voiceOrb');
             return {
               state: window.__m.state,
               level: window.__m.level,
               active: window.__m.active,
               css: Number(orb.style.getPropertyValue('--mic-level') || 0),
               attr: orb.dataset.voiceState || '',
               peak: Math.max(0, ...window.__levels),
               errors: window.__errors.slice(),
             };
           }""", wait_ms)


def test_the_level_comes_from_the_real_microphone(page):
    page.evaluate(METER)

    assert page.evaluate("async () => window.__m.start()") is True
    seen = measure(page)

    assert seen["state"] == "listening"
    assert seen["level"] > 0.2, f"the analyser saw no audio: {seen}"
    assert seen["css"] > 0.2, "the level never reached the orb"
    assert seen["attr"] == "listening"
    assert seen["errors"] == []


def test_a_quieter_microphone_shows_a_lower_level(page, site, quiet_tone,
                                                  playwright_instance):
    """The level tracks loudness, which a timer-driven animation cannot do."""
    page.evaluate(METER)
    page.evaluate("async () => window.__m.start()")
    loud = measure(page, 700)["level"]

    other = playwright_instance.chromium.launch(headless=True,
                                                args=_launch_args(quiet_tone))
    try:
        context = other.new_context(permissions=["microphone"])
        quiet_page = context.new_page()
        quiet_page.goto(f"{site}/index.html", wait_until="load")
        quiet_page.evaluate(METER)
        quiet_page.evaluate("async () => window.__m.start()")
        quiet = measure(quiet_page, 700)["level"]
        context.close()
    finally:
        other.close()

    assert loud > 0.6, f"a loud tone should read high, got {loud}"
    assert quiet < loud * 0.6, f"quiet={quiet} was not quieter than loud={loud}"


def test_the_curve_is_monotonic_and_bounded(page):
    curve = page.evaluate(
        """async () => {
             const { levelFromRms } = await import('/js/meter.js');
             return [0, 0.0005, 0.005, 0.02, 0.05, 0.1, 0.2, 0.5, 1]
               .map((rms) => levelFromRms(rms));
           }""")

    assert curve[0] == 0
    assert curve[-1] == 1
    assert curve == sorted(curve), "the level curve is not monotonic"
    assert all(0 <= v <= 1 for v in curve)


def test_stopping_releases_the_microphone(page):
    page.evaluate(METER)
    page.evaluate("async () => window.__m.start()")
    measure(page, 400)

    released = page.evaluate(
        """async () => {
             const stream = window.__m.stream;
             const track = stream.getAudioTracks()[0];
             const context = window.__m.context;
             window.__m.stop();
             await new Promise((r) => setTimeout(r, 120));
             return {
               track: track.readyState,
               context: context.state,
               active: window.__m.active,
               level: window.__m.level,
               state: window.__m.state,
               css: Number(document.getElementById('voiceOrb')
                            .style.getPropertyValue('--mic-level') || 0),
             };
           }""")

    assert released["track"] == "ended", "the microphone was left open"
    assert released["context"] == "closed", "the AudioContext was left running"
    assert released["active"] is False
    assert released["level"] == 0
    assert released["state"] == "idle"
    assert released["css"] == 0


def test_pausing_holds_the_level_but_keeps_the_session(page):
    page.evaluate(METER)
    page.evaluate("async () => window.__m.start()")
    measure(page, 500)

    paused = page.evaluate(
        """async () => {
             window.__m.setState('paused');
             await new Promise((r) => setTimeout(r, 250));
             return {
               state: window.__m.state,
               level: window.__m.level,
               track: window.__m.stream.getAudioTracks()[0].readyState,
               attr: document.getElementById('voiceOrb').dataset.voiceState,
             };
           }""")

    assert paused["state"] == "paused"
    assert paused["level"] == 0
    assert paused["attr"] == "paused"
    assert paused["track"] == "live", "pausing must not close the microphone"


def test_speaking_shows_a_state_not_a_level_then_resumes(page):
    page.evaluate(METER)
    page.evaluate("async () => window.__m.start()")
    measure(page, 500)

    speaking = page.evaluate(
        """async () => {
             window.__m.setState('speaking');
             await new Promise((r) => setTimeout(r, 250));
             return { state: window.__m.state, level: window.__m.level };
           }""")
    assert speaking["state"] == "speaking"
    assert speaking["level"] == 0

    resumed = page.evaluate(
        """async () => {
             window.__m.setState('listening');
             await new Promise((r) => setTimeout(r, 600));
             return { state: window.__m.state, level: window.__m.level };
           }""")
    assert resumed["state"] == "listening"
    assert resumed["level"] > 0.2, "the level did not come back after speaking"


def test_a_denied_microphone_is_explained_and_draws_nothing(page):
    denied = page.evaluate(
        """async () => {
             const { MicMeter } = await import('/js/meter.js');
             const errors = [];
             const original = navigator.mediaDevices.getUserMedia;
             navigator.mediaDevices.getUserMedia = async () => {
               const err = new Error('denied');
               err.name = 'NotAllowedError';
               throw err;
             };
             const meter = new MicMeter({ onError: (m) => errors.push(m) });
             const started = await meter.start();
             navigator.mediaDevices.getUserMedia = original;
             return { started, state: meter.state, level: meter.level,
                      active: meter.active, errors };
           }""")

    assert denied["started"] is False
    assert denied["state"] == "idle"
    assert denied["level"] == 0
    assert denied["active"] is False
    assert any("permission" in e.lower() for e in denied["errors"])


def test_a_browser_without_web_audio_says_so(page):
    unsupported = page.evaluate(
        """async () => {
             const { MicMeter } = await import('/js/meter.js');
             const errors = [];
             const meter = new MicMeter({ onError: (m) => errors.push(m) });
             const original = navigator.mediaDevices;
             Object.defineProperty(navigator, 'mediaDevices',
               { value: undefined, configurable: true });
             const supported = meter.supported;
             const started = await meter.start();
             Object.defineProperty(navigator, 'mediaDevices',
               { value: original, configurable: true });
             return { supported, started, errors };
           }""")

    assert unsupported["supported"] is False
    assert unsupported["started"] is False
    assert any("cannot open a microphone" in e for e in unsupported["errors"])


def test_the_ring_is_driven_by_the_level_not_by_a_keyframe(page):
    page.evaluate(METER)

    silent = page.evaluate(
        """() => {
             const ring = document.querySelector('.orb-level');
             const style = getComputedStyle(ring);
             return { animation: style.animationName, transform: style.transform };
           }""")

    page.evaluate("async () => window.__m.start()")
    measure(page, 700)
    loud = page.evaluate(
        """() => {
             const ring = document.querySelector('.orb-level');
             const style = getComputedStyle(ring);
             return { animation: style.animationName, transform: style.transform,
                      opacity: Number(style.opacity) };
           }""")

    assert silent["animation"] in ("none", ""), \
        "the ring still runs a keyframe animation of its own"
    assert loud["transform"] != silent["transform"], \
        "the ring did not react to the level"
    assert loud["opacity"] > 0.2


def test_the_controller_opens_and_closes_the_meter(page):
    """VoiceController.start() opens the device; stop() gives it back."""
    result = page.evaluate(
        """async () => {
             // A stand-in for the Web Speech API: this test is about the meter
             // wiring, not about a speech service that a headless browser
             // does not have.
             class FakeRecognition {
               constructor() { this.started = false; }
               start() { this.started = true; }
               stop() { this.started = false; }
             }
             window.SpeechRecognition = FakeRecognition;
             window.webkitSpeechRecognition = FakeRecognition;
             const { VoiceController } = await import('/js/voice.js');
             const voice = new VoiceController({});
             const before = voice.meter.active;
             voice.start();
             await new Promise((r) => setTimeout(r, 700));
             const during = { active: voice.meter.active,
                              state: voice.meter.state,
                              level: voice.meter.level };
             voice.stop();
             await new Promise((r) => setTimeout(r, 200));
             return { before, during,
                      after: { active: voice.meter.active,
                               state: voice.meter.state,
                               level: voice.meter.level } };
           }""")

    assert result["before"] is False
    assert result["during"]["active"] is True
    assert result["during"]["state"] == "listening"
    assert result["during"]["level"] > 0.2
    assert result["after"]["active"] is False
    assert result["after"]["state"] == "idle"
    assert result["after"]["level"] == 0
