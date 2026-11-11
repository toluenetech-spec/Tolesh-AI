"""Three system controls, and nothing else.

Volume, brightness and locking the screen. Deliberately *not* here: shutdown,
restart, arbitrary commands, anything that deletes or sends. Those stay
refused — see ``tools_ext/computer.py``.

Two rules shape the implementation:

  * **No shell.** Every command is an argument list, built from a fixed
    executable and a validated integer. There is no string to interpolate and
    therefore nothing to inject into.
  * **No false success.** A control reports ``ok`` only when the command it
    ran exited zero. If the utility is missing, or the platform has no way to
    do it, the answer says which utility and why — an absent feature is
    reported, not simulated.

The runner, the executable lookup and the platform are all injectable so the
tests can exercise every branch on any machine, including the ones that only
exist on Windows.
"""

from __future__ import annotations

import importlib.util
import platform
import shutil
import subprocess
from typing import Any, Callable

__all__ = ["ControlError", "SystemControls", "LEVEL_MIN", "LEVEL_MAX"]

LEVEL_MIN, LEVEL_MAX = 0, 100
COMMAND_TIMEOUT = 10.0

# (returncode, stdout, stderr)
Runner = Callable[[list[str]], "tuple[int, str, str]"]


class ControlError(RuntimeError):
    """A control could not be performed. ``kind`` says which sort of no."""

    #: unsupported — this platform cannot do it at all
    #: missing     — a utility it needs is not installed
    #: failed      — the utility ran and refused
    def __init__(self, message: str, *, kind: str = "failed", utility: str = ""):
        super().__init__(message)
        self.kind = kind
        self.utility = utility


def _default_runner(command: list[str]) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(command, capture_output=True, text=True,
                                   timeout=COMMAND_TIMEOUT, check=False)
    except FileNotFoundError as exc:
        raise ControlError(f"{command[0]} is not installed.", kind="missing",
                           utility=command[0]) from exc
    except subprocess.TimeoutExpired as exc:
        raise ControlError(f"{command[0]} did not finish within "
                           f"{int(COMMAND_TIMEOUT)} s.", kind="failed",
                           utility=command[0]) from exc
    except OSError as exc:
        raise ControlError(f"{command[0]} could not run: {exc}", kind="failed",
                           utility=command[0]) from exc
    return completed.returncode, completed.stdout or "", completed.stderr or ""


def _check_level(level: Any) -> int:
    if isinstance(level, bool) or level is None:
        raise ControlError("A level is a whole number from 0 to 100.",
                           kind="failed")
    if isinstance(level, float) and not level.is_integer():
        # 40.5% is not a level these utilities accept; truncating it would
        # silently do something other than what was asked.
        raise ControlError("A level is a whole number from 0 to 100.",
                           kind="failed")
    try:
        value = int(level)
    except (TypeError, ValueError) as exc:
        raise ControlError("A level is a whole number from 0 to 100.",
                           kind="failed") from exc
    if not LEVEL_MIN <= value <= LEVEL_MAX:
        raise ControlError("A level must be between 0 and 100.", kind="failed")
    return value


def _first_percent(text: str) -> int | None:
    """First ``NN%`` in a tool's output — how these utilities report a level."""
    for token in text.replace("\n", " ").split():
        if token.endswith("%"):
            digits = token[:-1].strip().strip("[]")
            if digits.isdigit():
                return int(digits)
    return None


class SystemControls:
    """Volume, brightness and screen lock for the platform we are on."""

    def __init__(self, *, runner: Runner | None = None,
                 which: Callable[[str], str | None] | None = None,
                 system: str | None = None,
                 has_module: Callable[[str], bool] | None = None):
        self._run = runner or _default_runner
        self._which = which or shutil.which
        self._has_module = has_module or _module_available
        self.system = (system or platform.system()).strip().lower()

    # ------------------------------------------------------------ discovery
    def _find(self, *names: str) -> str | None:
        for name in names:
            if self._which(name):
                return name
        return None

    def capabilities(self) -> dict[str, Any]:
        """What works here, what does not, and what would make it work.

        The UI and the model both read this, so a control that cannot run is
        named as unavailable instead of failing when it is called.
        """
        return {"platform": self.system or "unknown",
                "controls": {name: self._capability(name)
                             for name in ("set_volume", "set_brightness",
                                          "lock_screen")}}

    def _capability(self, control: str) -> dict[str, Any]:
        try:
            utility = self._utility(control)
        except ControlError as exc:
            return {"supported": False, "utility": exc.utility,
                    "detail": str(exc), "kind": exc.kind}
        return {"supported": True, "utility": utility, "detail": "", "kind": ""}

    def _utility(self, control: str) -> str:
        """The executable this control would use, or a ControlError saying why not."""
        if self.system == "linux":
            table = {
                "set_volume": ("pactl", "amixer"),
                "set_brightness": ("brightnessctl",),
                "lock_screen": ("loginctl", "xdg-screensaver",
                                "gnome-screensaver-command"),
            }
            hints = {
                "set_volume": "Install PulseAudio's pactl (pulseaudio-utils) or "
                              "ALSA's amixer (alsa-utils).",
                "set_brightness": "Install brightnessctl (it needs permission to "
                                  "write /sys/class/backlight).",
                "lock_screen": "Install systemd's loginctl, or xdg-screensaver, or "
                               "GNOME's screensaver.",
            }
        elif self.system == "darwin":
            table = {
                "set_volume": ("osascript",),
                "set_brightness": ("brightness",),
                "lock_screen": ("pmset",),
            }
            hints = {
                "set_volume": "osascript ships with macOS; if it is missing, the "
                              "system is unusual.",
                "set_brightness": "Install the Homebrew utility: brew install brightness.",
                "lock_screen": "pmset ships with macOS.",
            }
        elif self.system == "windows":
            table = {
                "set_volume": ("powershell", "powershell.exe"),
                "set_brightness": ("powershell", "powershell.exe"),
                "lock_screen": ("rundll32", "rundll32.exe"),
            }
            hints = {
                "set_volume": "Windows has no built-in volume CLI. Install pycaw "
                              "(pip install pycaw) to control the endpoint.",
                "set_brightness": "PowerShell is needed for the WMI brightness "
                                  "interface.",
                "lock_screen": "rundll32 ships with Windows.",
            }
        else:
            raise ControlError(
                f"System controls are not implemented for {self.system or 'this platform'}.",
                kind="unsupported")

        if self.system == "windows" and control == "set_volume":
            # PowerShell alone cannot set the endpoint volume; pycaw can.
            if not self._has_module("pycaw"):
                raise ControlError(hints["set_volume"], kind="missing",
                                   utility="pycaw")
            return "pycaw"

        found = self._find(*table[control])
        if found is None:
            raise ControlError(hints[control], kind="missing",
                               utility=table[control][0])
        return found

    # -------------------------------------------------------------- controls
    def set_volume(self, level: Any) -> dict[str, Any]:
        value = _check_level(level)
        utility = self._utility("set_volume")
        if self.system == "linux":
            if utility == "pactl":
                self._expect([utility, "set-sink-volume", "@DEFAULT_SINK@",
                              f"{value}%"], utility)
                observed = self._observe_volume_pactl()
                return self._result("set_volume", value, "pactl", observed)
            self._expect([utility, "-q", "sset", "Master", f"{value}%"], utility)
            return self._result("set_volume", value, "amixer", None)
        if self.system == "darwin":
            self._expect([utility, "-e", f"set volume output volume {value}"], utility)
            observed = self._observe_darwin_volume()
            return self._result("set_volume", value, "osascript", observed)
        return self._windows_volume(value, utility)

    def set_brightness(self, level: Any) -> dict[str, Any]:
        value = _check_level(level)
        utility = self._utility("set_brightness")
        if self.system == "linux":
            self._expect([utility, "set", f"{value}%"], utility)
            # ``brightnessctl get`` reports a raw device value, not a percentage,
            # so there is nothing honest to compare against.
            return self._result("set_brightness", value, "brightnessctl", None)
        if self.system == "darwin":
            # The Homebrew utility takes 0.0–1.0.
            self._expect([utility, f"{value / 100:.2f}"], utility)
            return self._result("set_brightness", value, "brightness", None)
        return self._windows_brightness(value, utility)

    def lock_screen(self) -> dict[str, Any]:
        utility = self._utility("lock_screen")
        if self.system == "linux":
            if utility == "loginctl":
                self._expect([utility, "lock-session"], utility)
                return {"status": "ok", "tool": "lock_screen", "method": "loginctl"}
            if utility == "xdg-screensaver":
                self._expect([utility, "lock"], utility)
                return {"status": "ok", "tool": "lock_screen",
                        "method": "xdg-screensaver"}
            self._expect([utility, "-l"], utility)
            return {"status": "ok", "tool": "lock_screen",
                    "method": "gnome-screensaver-command"}
        if self.system == "darwin":
            # pmset sleeps the display; the screen locks if the system is set to
            # require a password immediately. Say so rather than overclaim.
            self._expect([utility, "displaysleepnow"], utility)
            return {"status": "ok", "tool": "lock_screen", "method": "pmset",
                    "detail": "The display is asleep. macOS locks it when "
                              "'require password immediately' is set."}
        self._expect([utility, "user32.dll,LockWorkStation"], utility)
        return {"status": "ok", "tool": "lock_screen", "method": "rundll32"}

    # --------------------------------------------------------------- windows
    def _windows_volume(self, value: int, utility: str) -> dict[str, Any]:
        if utility != "pycaw":
            raise ControlError("Volume control on Windows needs pycaw.",
                               kind="missing", utility="pycaw")
        try:
            from ctypes import POINTER, cast  # noqa: F401  (used by pycaw's API)
            from comtypes import CLSCTX_ALL  # type: ignore
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume  # type: ignore
        except Exception as exc:
            raise ControlError(f"pycaw could not be loaded: {exc}", kind="missing",
                               utility="pycaw") from exc
        try:
            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = cast(interface, POINTER(IAudioEndpointVolume))
            volume.SetMasterVolumeLevelScalar(value / 100.0, None)
            observed = int(round(volume.GetMasterVolumeLevelScalar() * 100))
        except Exception as exc:
            raise ControlError(f"Windows refused the volume change: {exc}",
                               kind="failed", utility="pycaw") from exc
        return self._result("set_volume", value, "pycaw", observed)

    def _windows_brightness(self, value: int, utility: str) -> dict[str, Any]:
        script = (
            "Get-CimInstance -Namespace root/WMI -ClassName "
            "WmiMonitorBrightnessMethods | Invoke-CimMethod -MethodName "
            f"WmiSetBrightness -Arguments @{{Brightness={value};Timeout=0}} "
            "| Out-Null")
        self._expect([utility, "-NoProfile", "-NonInteractive", "-Command", script],
                     utility)
        return self._result("set_brightness", value, "wmi", None)

    # --------------------------------------------------------------- helpers
    def _expect(self, command: list[str], utility: str) -> str:
        """Run a command and turn a non-zero exit into a reported failure."""
        code, out, err = self._run(command)
        if code != 0:
            detail = (err or out or "").strip().splitlines()
            message = detail[-1] if detail else "no error output"
            raise ControlError(f"{utility} failed (exit {code}): {message[:200]}",
                               kind="failed", utility=utility)
        return out

    def _observe_volume_pactl(self) -> int | None:
        try:
            code, out, _err = self._run(["pactl", "get-sink-volume",
                                         "@DEFAULT_SINK@"])
        except ControlError:
            return None
        return _first_percent(out) if code == 0 else None

    def _observe_darwin_volume(self) -> int | None:
        try:
            code, out, _err = self._run(
                ["osascript", "-e", "output volume of (get volume settings)"])
        except ControlError:
            return None
        text = out.strip()
        return int(text) if code == 0 and text.isdigit() else None

    @staticmethod
    def _result(tool: str, level: int, method: str,
                observed: int | None) -> dict[str, Any]:
        payload: dict[str, Any] = {"status": "ok", "tool": tool, "level": level,
                                   "method": method}
        if observed is not None:
            payload["observed"] = observed
            # A tolerance of two points absorbs per-channel rounding; a wider
            # gap means the device did not accept the value, and saying
            # "ok, verified" would be a lie.
            payload["verified"] = abs(observed - level) <= 2
        return payload


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False
