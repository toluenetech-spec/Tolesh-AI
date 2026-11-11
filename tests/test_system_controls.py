"""Volume, brightness and screen lock — gated, platform-specific, honest.

Every platform branch is exercised on any machine: the runner, the executable
lookup and the platform name are injected. The tests that matter most are the
negative ones — a missing utility must say which utility, a failing command must
not report success, and the four dangerous tools must stay refused.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tolesh.agents import WorkspaceContext, build_registry
from tolesh.ai import ModelRouter, Registry
from tolesh.permissions import PermissionGate, RiskLevel, TOOL_POLICY, classify
from tolesh.server import create_app
from tolesh.server.app import AppState
from tolesh.system import ControlError, SystemControls
from tolesh.tools_ext.computer import ComputerTools


class FakeShell:
    """Records commands and answers them from a script."""

    def __init__(self, *, code: int = 0, stdout: str = "", stderr: str = ""):
        self.code = code
        self.stdout = stdout
        self.stderr = stderr
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str]) -> tuple[int, str, str]:
        self.commands.append(list(command))
        return self.code, self.stdout, self.stderr


def linux(*available: str, **kwargs) -> SystemControls:
    """A Linux machine on which only ``available`` utilities exist."""
    return SystemControls(runner=kwargs.pop("runner", FakeShell()),
                          which=lambda name: f"/usr/bin/{name}" if name in available else None,
                          system="linux", **kwargs)


# ------------------------------------------------------------- policy table
def test_the_three_controls_are_elevated_not_consequential():
    for tool in ("set_volume", "set_brightness", "lock_screen"):
        assert TOOL_POLICY[tool] is RiskLevel.ELEVATED
        assert classify(tool) is RiskLevel.ELEVATED


def test_the_dangerous_tools_are_still_consequential():
    for tool in ("shutdown", "restart", "run_command", "delete_file",
                 "send_message", "network_request"):
        assert TOOL_POLICY[tool] is RiskLevel.CONSEQUENTIAL


def test_an_unknown_tool_is_never_safe():
    assert classify("format_disk") is RiskLevel.CONSEQUENTIAL


# -------------------------------------------------------------------- levels
@pytest.mark.parametrize("level", [-1, 101, 1000, "loud", None, True, 40.5])
def test_a_bad_level_is_refused_before_anything_runs(level):
    shell = FakeShell()
    controls = SystemControls(runner=shell, which=lambda n: n, system="linux")

    with pytest.raises(ControlError):
        controls.set_volume(level)

    assert shell.commands == []


@pytest.mark.parametrize("level", [0, 1, 50, 99, 100])
def test_every_valid_level_is_accepted(level):
    shell = FakeShell(stdout="volume: front-left: 32768 /  50% / -18.06 dB")
    controls = linux("pactl", runner=shell)

    assert controls.set_volume(level)["level"] == level


# -------------------------------------------------------------------- linux
def test_linux_volume_uses_pactl_with_an_argument_list():
    shell = FakeShell(stdout="Volume: front-left: 26214 /  40%")
    controls = linux("pactl", "amixer", runner=shell)

    result = controls.set_volume(40)

    assert shell.commands[0] == ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "40%"]
    assert result["status"] == "ok"
    assert result["method"] == "pactl"
    assert result["observed"] == 40
    assert result["verified"] is True


def test_linux_volume_falls_back_to_amixer():
    shell = FakeShell()
    controls = linux("amixer", runner=shell)

    result = controls.set_volume(25)

    assert shell.commands[0] == ["amixer", "-q", "sset", "Master", "25%"]
    assert result["method"] == "amixer"


def test_linux_volume_reports_a_level_the_device_did_not_accept():
    def runner(command):
        if command[0] == "pactl" and command[1] == "set-sink-volume":
            return 0, "", ""
        return 0, "Volume: front-left: 13107 /  20%", ""

    controls = linux("pactl", runner=runner)

    result = controls.set_volume(80)

    assert result["status"] == "ok"
    assert result["observed"] == 20
    assert result["verified"] is False


def test_linux_brightness_uses_brightnessctl():
    shell = FakeShell()
    controls = linux("brightnessctl", runner=shell)

    result = controls.set_brightness(60)

    assert shell.commands[0] == ["brightnessctl", "set", "60%"]
    assert result["status"] == "ok"
    assert result["method"] == "brightnessctl"


def test_linux_brightness_without_the_utility_names_the_utility():
    controls = linux("pactl")

    with pytest.raises(ControlError) as caught:
        controls.set_brightness(60)

    assert caught.value.kind == "missing"
    assert "brightnessctl" in str(caught.value)


def test_linux_lock_uses_loginctl_first():
    shell = FakeShell()
    controls = linux("loginctl", "xdg-screensaver", runner=shell)

    result = controls.lock_screen()

    assert shell.commands[0] == ["loginctl", "lock-session"]
    assert result["method"] == "loginctl"


def test_linux_lock_falls_back_to_xdg_screensaver():
    shell = FakeShell()
    controls = linux("xdg-screensaver", runner=shell)

    controls.lock_screen()

    assert shell.commands[0] == ["xdg-screensaver", "lock"]


def test_linux_lock_falls_back_to_gnome_screensaver():
    shell = FakeShell()
    controls = linux("gnome-screensaver-command", runner=shell)

    controls.lock_screen()

    assert shell.commands[0] == ["gnome-screensaver-command", "-l"]


def test_a_failing_command_is_a_failure_not_a_success():
    shell = FakeShell(code=1, stderr="No such sink\nConnection failure: Connection refused")
    controls = linux("pactl", runner=shell)

    with pytest.raises(ControlError) as caught:
        controls.set_volume(30)

    assert caught.value.kind == "failed"
    assert "exit 1" in str(caught.value)
    assert "Connection refused" in str(caught.value)


# --------------------------------------------------------------------- macOS
def test_macos_volume_uses_osascript():
    shell = FakeShell(stdout="55\n")
    controls = SystemControls(runner=shell, which=lambda n: f"/usr/bin/{n}",
                              system="Darwin")

    result = controls.set_volume(55)

    assert shell.commands[0] == ["osascript", "-e", "set volume output volume 55"]
    assert result["method"] == "osascript"
    assert result["observed"] == 55
    assert result["verified"] is True


def test_macos_brightness_needs_the_homebrew_utility():
    controls = SystemControls(runner=FakeShell(), which=lambda n: None,
                              system="Darwin")

    with pytest.raises(ControlError) as caught:
        controls.set_brightness(40)

    assert caught.value.kind == "missing"
    assert "brew install brightness" in str(caught.value)


def test_macos_brightness_scales_to_a_fraction():
    shell = FakeShell()
    controls = SystemControls(runner=shell, which=lambda n: f"/usr/local/bin/{n}",
                              system="Darwin")

    controls.set_brightness(70)

    assert shell.commands[0] == ["brightness", "0.70"]


def test_macos_lock_uses_pmset_and_says_what_it_actually_does():
    shell = FakeShell()
    controls = SystemControls(runner=shell, which=lambda n: f"/usr/bin/{n}",
                              system="Darwin")

    result = controls.lock_screen()

    assert shell.commands[0] == ["pmset", "displaysleepnow"]
    assert "require password immediately" in result["detail"]


# ------------------------------------------------------------------- windows
def test_windows_volume_explains_that_pycaw_is_needed():
    controls = SystemControls(runner=FakeShell(), which=lambda n: f"C:/{n}",
                              system="Windows", has_module=lambda name: False)

    with pytest.raises(ControlError) as caught:
        controls.set_volume(30)

    assert caught.value.kind == "missing"
    assert "pycaw" in str(caught.value)


def test_windows_brightness_uses_the_wmi_cmdlet():
    shell = FakeShell()
    controls = SystemControls(runner=shell, which=lambda n: f"C:/{n}",
                              system="Windows")

    result = controls.set_brightness(45)

    command = shell.commands[0]
    assert command[0] == "powershell"
    assert command[1:4] == ["-NoProfile", "-NonInteractive", "-Command"]
    assert "WmiSetBrightness" in command[4]
    assert "Brightness=45" in command[4]
    assert result["status"] == "ok"


def test_windows_lock_uses_rundll32():
    shell = FakeShell()
    controls = SystemControls(runner=shell, which=lambda n: f"C:/{n}",
                              system="Windows")

    controls.lock_screen()

    assert shell.commands[0] == ["rundll32", "user32.dll,LockWorkStation"]


# -------------------------------------------------------------- capabilities
def test_capabilities_report_each_control_separately():
    controls = linux("pactl", "loginctl")

    report = controls.capabilities()

    assert report["platform"] == "linux"
    assert report["controls"]["set_volume"]["supported"] is True
    assert report["controls"]["set_brightness"]["supported"] is False
    assert "brightnessctl" in report["controls"]["set_brightness"]["detail"]
    assert report["controls"]["lock_screen"]["supported"] is True


def test_an_unsupported_platform_says_so_and_refuses():
    controls = SystemControls(runner=FakeShell(), which=lambda n: n, system="Plan9")

    report = controls.capabilities()

    assert all(not c["supported"] for c in report["controls"].values())
    assert report["controls"]["set_volume"]["kind"] == "unsupported"
    with pytest.raises(ControlError) as caught:
        controls.set_volume(10)
    assert caught.value.kind == "unsupported"


# ------------------------------------------------------- the tool executor
def tools_with(*available: str, runner=None) -> ComputerTools:
    return ComputerTools(PermissionGate(),
                         controls=linux(*available, runner=runner or FakeShell()))


def test_the_tool_refuses_to_run_without_an_approval():
    shell = FakeShell()
    tools = tools_with("pactl", runner=shell)

    result = tools.invoke("set_volume", {"level": 40})

    assert result["status"] == "pending-confirmation"
    assert result["token"]
    assert shell.commands == []          # nothing ran before approval


def test_an_approved_tool_runs_exactly_once():
    shell = FakeShell(stdout="40%")
    tools = tools_with("pactl", runner=shell)
    pending = tools.invoke("set_volume", {"level": 40})

    decision = tools.gate.approve(pending["id"], pending["token"])
    executed = tools.execute(decision["tool"], decision["args"], risk=decision["risk"])

    assert decision["status"] == "approved"
    assert executed["status"] == "ok"
    # The set command ran once; the second call is the read-back that proves it.
    assert shell.commands[0] == ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "40%"]
    assert [c[1] for c in shell.commands] == ["set-sink-volume", "get-sink-volume"]


def test_a_wrong_token_cannot_approve_a_control():
    shell = FakeShell()
    tools = tools_with("pactl", runner=shell)
    pending = tools.invoke("set_volume", {"level": 40})

    decision = tools.gate.approve(pending["id"], "not-the-token")

    assert decision["status"] == "error"
    assert shell.commands == []


def test_a_missing_utility_is_reported_as_unavailable():
    tools = tools_with("pactl")           # no brightnessctl

    result = tools.execute("set_brightness", {"level": 40}, risk="elevated")

    assert result["status"] == "unavailable"
    assert "brightnessctl" in result["message"]
    assert result["utility"] == "brightnessctl"


def test_a_failing_utility_is_reported_as_an_error():
    tools = tools_with("pactl", runner=FakeShell(code=2, stderr="boom"))

    result = tools.execute("set_volume", {"level": 10}, risk="elevated")

    assert result["status"] == "error"
    assert "boom" in result["message"]


def test_shutdown_and_restart_are_still_refused_after_approval():
    tools = ComputerTools(PermissionGate(), controls=linux("pactl"))

    for tool in ("shutdown", "restart", "run_command", "delete_file"):
        result = tools.execute(tool, {}, risk="consequential")
        assert result["status"] == "refused"
        assert "not implemented" in result["message"]


def test_the_tool_list_says_which_controls_work_here():
    tools = tools_with("pactl", "loginctl")

    listed = {t["name"]: t for t in tools.list_tools()}

    assert listed["set_volume"]["available"] is True
    assert listed["set_brightness"]["available"] is False
    assert "brightnessctl" in listed["set_brightness"]["detail"]
    assert listed["shutdown"]["implemented"] is False


# ------------------------------------------------------- the agent registry
def test_the_agent_sees_the_three_tools_and_nothing_more():
    registry = build_registry(WorkspaceContext(tools=tools_with("pactl", "loginctl")))

    names = set(registry.names())
    assert {"set_volume", "set_brightness", "lock_screen"} <= names
    assert "run_command" not in names
    assert "shutdown" not in names
    assert registry.get("set_volume").needs_approval is True


def test_an_agent_call_without_approval_runs_nothing():
    shell = FakeShell()
    registry = build_registry(WorkspaceContext(tools=tools_with("pactl", runner=shell)))

    result = registry.invoke("set_volume", {"level": 30})

    assert result.ok is False
    assert "approval" in result.text
    assert shell.commands == []


def test_an_agent_call_with_approval_runs():
    shell = FakeShell(stdout="30%")
    registry = build_registry(WorkspaceContext(tools=tools_with("pactl", runner=shell)))

    result = registry.invoke("set_volume", {"level": 30}, approved=True)

    assert result.ok is True
    assert "30%" in result.text
    assert shell.commands[0] == ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "30%"]
    assert [c[1] for c in shell.commands].count("set-sink-volume") == 1


def test_an_agent_lock_call_reports_the_lock():
    registry = build_registry(WorkspaceContext(tools=tools_with("loginctl")))

    result = registry.invoke("lock_screen", {}, approved=True)

    assert result.ok is True
    assert "locked" in result.text.lower()


def test_an_agent_control_that_cannot_run_is_not_ok():
    registry = build_registry(WorkspaceContext(tools=tools_with("pactl")))

    result = registry.invoke("set_brightness", {"level": 30}, approved=True)

    assert result.ok is False
    assert "brightnessctl" in result.text


# ----------------------------------------------------------------------- API
@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.delenv("TOLESH_API_TOKEN", raising=False)
    state = AppState(in_memory=True)
    offline = Registry.from_defaults().provider("offline")
    state.registry = Registry({"offline": offline})
    state.router = ModelRouter(state.registry, state.ledger, offline_delay=0)
    shell = FakeShell(stdout="volume: 40%")
    state.tools.controls = linux("pactl", "loginctl", runner=shell)
    app = create_app(state=state, web_dir=str(tmp_path), serve_static=False)
    with TestClient(app) as client:
        yield client, shell
    state.close()


def test_the_api_reports_what_this_machine_can_do(api):
    client, _shell = api

    payload = client.get("/api/tools/system").json()

    assert payload["controls"]["set_volume"]["supported"] is True
    assert payload["controls"]["set_brightness"]["supported"] is False
    assert "shutdown" in payload["refused"]
    assert "approval token" in payload["note"]


def test_the_api_needs_a_token_and_then_runs_the_control(api):
    client, shell = api

    pending = client.post("/api/tools/invoke",
                          json={"tool": "set_volume", "args": {"level": 40}}).json()
    assert pending["status"] == "pending-confirmation"
    assert shell.commands == []

    approved = client.post(f"/api/permissions/{pending['id']}/approve",
                           json={"token": pending["token"], "execute": True}).json()

    assert approved["decision"]["status"] == "approved"
    assert approved["execution"]["status"] == "ok"
    assert shell.commands[0] == ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "40%"]


def test_the_api_refuses_a_control_with_a_wrong_token(api):
    client, shell = api
    pending = client.post("/api/tools/invoke",
                          json={"tool": "lock_screen"}).json()

    refused = client.post(f"/api/permissions/{pending['id']}/approve",
                          json={"token": "wrong", "execute": True})

    assert refused.status_code == 400
    assert shell.commands == []


def test_the_api_cannot_be_talked_into_shutdown(api):
    client, shell = api

    result = client.post("/api/tools/invoke", json={"tool": "shutdown"}).json()
    assert result["status"] == "pending-confirmation"

    approved = client.post(f"/api/permissions/{result['id']}/approve",
                           json={"token": result["token"], "execute": True}).json()

    assert approved["execution"]["status"] == "refused"
    assert shell.commands == []


def test_the_api_denies_a_control_when_the_user_says_no(api):
    client, shell = api
    pending = client.post("/api/tools/invoke",
                          json={"tool": "set_volume", "args": {"level": 90}}).json()

    denied = client.post(f"/api/permissions/{pending['id']}/deny",
                         json={"token": pending["token"], "execute": False}).json()

    assert denied["decision"]["status"] == "denied"
    assert shell.commands == []
    audit = client.get("/api/permissions/audit").json()["entries"]
    assert any(e["tool"] == "set_volume" and e["status"] == "denied" for e in audit)
