"""Measure what the background features actually cost.

Run: ``PYTHONPATH=src python3.11 scripts/measure_runtime.py``

Numbers, not adjectives. Three things are measured:

  * the wake-word gate — CPU seconds per second of audio (a real-time factor),
    and the resident-memory cost of loading the backend;
  * the scheduler loop with nothing due, which should be close to free;
  * the Telegram poller against a fake transport, which shows the polling rate
    the backoff produces rather than the rate the code claims.

The audio is synthetic, so this measures the *detector*, not its accuracy.
Accuracy needs a microphone and a person saying the word; see the checklist in
docs/PRODUCTION-AUDIT.md.
"""

from __future__ import annotations

import gc
import os
import resource
import sys
import threading
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def cpu() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return usage.ru_utime + usage.ru_stime


def measure(label: str, fn) -> None:
    gc.collect()
    before_rss, before_cpu = rss_mb(), cpu()
    started = time.perf_counter()
    detail = fn()
    elapsed = time.perf_counter() - started
    after_rss, after_cpu = rss_mb(), cpu()
    print(f"{label}")
    print(f"  wall {elapsed * 1000:8.1f} ms   cpu {(after_cpu - before_cpu) * 1000:8.1f} ms"
          f"   rss {before_rss:7.1f} → {after_rss:7.1f} MB")
    if detail:
        print(f"  {detail}")
    print()


# --------------------------------------------------------------- wake word
def wake_word() -> None:
    from tolesh.listening.hotword import HotwordError, create_detector
    from tolesh.config import load_settings

    cfg = load_settings()
    try:
        detector = create_detector(cfg)
    except HotwordError as exc:
        print(f"  openWakeWord unavailable here: {exc}")
        print("  (install with `pip install openwakeword` and run "
              "`python -m openwakeword.utils.download_models`)")
        return

    frame = (np.random.default_rng(7).standard_normal(320) * 0.01).astype(np.float32)
    seconds = 60.0
    frames = int(seconds / 0.02)
    started_cpu = cpu()
    started = time.perf_counter()
    for _ in range(frames):
        detector.feed(frame)
    elapsed = time.perf_counter() - started
    used = cpu() - started_cpu
    print(f"  backend {detector.name}, chunk {detector.chunk} samples "
          f"({detector.chunk / detector.sample_rate * 1000:.0f} ms)")
    print(f"  {seconds:.0f}s of audio scored in {elapsed:.2f}s wall / {used:.2f}s cpu"
          f" → real-time factor {elapsed / seconds:.3f} "
          f"({(elapsed / seconds) * 100:.1f}% of one core)")
    detector.close()


# --------------------------------------------------------------- scheduler
def scheduler_idle() -> None:
    from tolesh.jobs import JobScheduler

    sched = JobScheduler(":memory:", executor=lambda job, progress: {}, tick=0.5)
    time.sleep(0.2)
    started_cpu = cpu()
    time.sleep(3.0)
    used = cpu() - started_cpu
    print(f"  idle for 3.0s with no jobs: {used * 1000:.1f} ms cpu "
          f"({used / 3.0 * 100:.2f}% of one core)")
    sched.close()


# ---------------------------------------------------------------- telegram
def telegram_idle() -> None:
    from tolesh.channels import FakeTransport, TelegramChannel, TelegramLinks

    transport = FakeTransport()
    channel = TelegramChannel(transport, links=TelegramLinks(":memory:"),
                              reply=lambda cid, text: "ok",
                              allowlist=lambda: {1},
                              new_conversation=lambda t, p="": "conv-1")
    channel.start()
    time.sleep(2.0)
    channel.stop()
    print(f"  {channel.polls} polls in 2.0s against a transport that answers "
          f"instantly — this is the busy case, not the network case")


# --------------------------------------------------------------------- app
def app_idle() -> None:
    from tolesh.server.app import AppState

    before = {t.name for t in threading.enumerate()}
    state = AppState(in_memory=True)
    time.sleep(0.3)
    started_cpu = cpu()
    time.sleep(2.0)
    used = cpu() - started_cpu
    threads = sorted({t.name for t in threading.enumerate()} - before)
    print(f"  threads: {threads or 'none'}")
    print(f"  idle for 2.0s: {used * 1000:.1f} ms cpu "
          f"({used / 2.0 * 100:.2f}% of one core)")
    state.close()


if __name__ == "__main__":
    print(f"python {sys.version.split()[0]}  rss at start {rss_mb():.1f} MB\n")
    measure("wake word — 60 s of synthetic audio through the detector", wake_word)
    measure("scheduler — idle loop", scheduler_idle)
    measure("telegram — polling a fake transport", telegram_idle)
    measure("app — full AppState, nothing happening", app_idle)
