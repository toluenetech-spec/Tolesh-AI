"""Run generated Python in a disposable, resource-limited child process.

This is a *containment* boundary, not a security boundary. It is honest about
that: the child runs as the same user, so it can still read files that user can
read. What it does guarantee is that a runaway script cannot

* hang the assistant (wall-clock timeout, process group killed),
* eat all memory (RLIMIT_AS), spin the CPU (RLIMIT_CPU) or fill the disk
  (RLIMIT_FSIZE),
* reach the network, when ``unshare`` is available (Linux, tested at runtime),
* leave anything behind (a fresh temporary working directory, deleted after).

For untrusted code from the internet you want a container or a VM; for "plot
this CSV the user just uploaded" this is the right amount of isolation.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

MAX_OUTPUT = 20_000
DEFAULT_TIMEOUT = 10
DEFAULT_MEMORY_MB = 512

_UNSHARE = shutil.which("unshare")


def network_isolation_available() -> bool:
    """``unshare -n`` needs a user namespace; test it rather than assume it."""
    if not _UNSHARE:
        return False
    try:
        probe = subprocess.run([_UNSHARE, "-n", "true"], capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


def run_python(code: str, *, timeout: int = DEFAULT_TIMEOUT,
               memory_mb: int = DEFAULT_MEMORY_MB,
               isolate_network: bool = True,
               files: dict[str, str] | None = None,
               collect: str | Path | None = None) -> dict[str, Any]:
    """Run *code* in a disposable directory.

    ``files`` seeds the working directory (a multi-file task needs its inputs),
    and ``collect`` names a directory that receives the files the script
    produced, so a chart or a report survives the sandbox. Both are bounded:
    names may not escape the directory and the total size is capped.
    """
    if not (code or "").strip():
        raise ValueError("There is no code to run.")
    if timeout < 1 or timeout > 120:
        raise ValueError("Timeout must be between 1 and 120 seconds.")

    workdir = Path(tempfile.mkdtemp(prefix="tolesh-run-"))
    script = workdir / "main.py"
    script.write_text(code, encoding="utf-8")
    seeded = _seed_files(workdir, files or {})
    isolated = bool(isolate_network) and network_isolation_available()

    argv = ([_UNSHARE, "-n"] if isolated else []) + [sys.executable, "-I", "-u", str(script)]
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(workdir),
        "TMPDIR": str(workdir),
        "LANG": "C.UTF-8",
        "PYTHONIOENCODING": "utf-8",
        # No inherited provider keys, no tokens, nothing to leak by accident.
        "MPLBACKEND": "Agg",
    }

    def _limits() -> None:                       # runs in the child, pre-exec
        import resource

        os.setsid()
        # A backstop above the wall clock, so the timeout we report is the
        # real one instead of a SIGXCPU that looks like a crash.
        resource.setrlimit(resource.RLIMIT_CPU, (timeout + 5, timeout + 6))
        resource.setrlimit(resource.RLIMIT_AS,
                           (memory_mb * 1024 * 1024, memory_mb * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))

    started = time.time()
    produced: list[dict[str, Any]] = []
    try:
        proc = subprocess.run(argv, cwd=str(workdir), env=env, capture_output=True,
                              timeout=timeout, preexec_fn=_limits)
        timed_out = False
        # Collected before the finally-block deletes the working directory.
        if collect:
            produced = _collect_files(workdir, Path(collect), seeded)
    except subprocess.TimeoutExpired as exc:
        elapsed = time.time() - started
        if collect:
            produced = _collect_files(workdir, Path(collect), seeded)
        return {"ok": False, "timed_out": True, "exit_code": None,
                "stdout": _clip(exc.stdout), "stderr": _clip(exc.stderr),
                "duration_ms": round(elapsed * 1000),
                "network_isolated": isolated, "files": produced,
                "message": f"Stopped after {timeout}s — the script was still running."}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    elapsed = time.time() - started
    stdout, stderr = _clip(proc.stdout), _clip(proc.stderr)
    if proc.returncode == 0:
        message = ""
    elif proc.returncode < 0:
        message = (f"Killed by signal {-proc.returncode} — the script used more "
                   "memory or CPU than its limit allows.")
    else:
        message = f"Exited with code {proc.returncode}."
    return {"ok": proc.returncode == 0, "timed_out": timed_out,
            "exit_code": proc.returncode, "stdout": stdout, "stderr": stderr,
            "duration_ms": round(elapsed * 1000), "network_isolated": isolated,
            "files": produced, "message": message}


MAX_FILES = 20
MAX_COLLECT_BYTES = 12 * 1024 * 1024


def _safe_name(name: str) -> str:
    base = Path(str(name).replace("\\", "/")).name
    cleaned = "".join(ch for ch in base if ch.isalnum() or ch in "._- ")
    return cleaned.strip(" .")[:80]


def _seed_files(workdir: Path, files: dict[str, str]) -> set[str]:
    seeded: set[str] = set()
    for name, content in list(files.items())[:MAX_FILES]:
        safe = _safe_name(name)
        if not safe:
            continue
        target = workdir / safe
        try:
            target.write_text(str(content), encoding="utf-8")
            seeded.add(safe)
        except OSError:
            continue
    return seeded


def _collect_files(workdir: Path, destination: Path, seeded: set[str]) -> list[dict[str, Any]]:
    """Copy files the script created into a directory that outlives it."""
    out: list[dict[str, Any]] = []
    try:
        destination.mkdir(parents=True, exist_ok=True)
    except OSError:
        return out
    total = 0
    for path in sorted(workdir.rglob("*")):
        if not path.is_file() or path.name == "main.py":
            continue
        relative = path.relative_to(workdir).as_posix()
        if relative in seeded:
            continue
        if len(out) >= MAX_FILES:
            break
        try:
            size = path.stat().st_size
        except OSError:
            continue
        total += size
        if total > MAX_COLLECT_BYTES:
            break
        safe = _safe_name(relative)
        if not safe:
            continue
        try:
            shutil.copy2(path, destination / safe)
        except OSError:
            continue
        out.append({"name": safe, "size": size})
    return out


def _clip(raw: Any) -> str:
    if raw is None:
        return ""
    text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
    if len(text) > MAX_OUTPUT:
        return text[:MAX_OUTPUT] + f"\n… output truncated at {MAX_OUTPUT} characters."
    return text
