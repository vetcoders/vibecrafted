"""Execution controls (``--permissions`` / ``--sandbox``): resolver + argv shapes.

Pure in-process contract of ``execution_controls`` and the spawn builders it
feeds. No provider binary is ever executed here; cursor materialization is fed
a fake ``--help`` surface.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from vibecrafted_core import spawn, workflow
from vibecrafted_core.continuity.capabilities import (
    CURSOR_TRACKED_FLAGS,
    PROBE_CONFIRMED,
    CursorCliSurface,
)
from vibecrafted_core.execution_controls import (
    PERMISSION_POLICIES,
    SANDBOX_BOUNDARY,
    ExecutionControlsError,
    claude_sandbox_settings,
    parse_permissions_word,
    parse_sandbox_word,
    resolve_execution_controls,
)

# Claude Code's documented hard gate: on, fail when the backend is missing, and
# never let the model retry a command outside the sandbox.
CLAUDE_SANDBOX_ON = (
    '{"sandbox":{"enabled":true,"failIfUnavailable":true,'
    '"allowUnsandboxedCommands":false}}'
)
CLAUDE_SANDBOX_OFF = '{"sandbox":{"enabled":false}}'


def test_public_words_are_parsed_deliberately() -> None:
    assert parse_permissions_word(None) == ""
    assert parse_permissions_word("") == ""
    assert parse_permissions_word(" Auto ") == "auto"
    for word in PERMISSION_POLICIES:
        assert parse_permissions_word(word) == word
    with pytest.raises(ExecutionControlsError, match="--permissions expects"):
        parse_permissions_word("yolo")
    with pytest.raises(ExecutionControlsError, match="bypassPermissions"):
        # The provider's own spelling is not a public word.
        parse_permissions_word("bypassPermissions")

    assert parse_sandbox_word(None) is None
    assert parse_sandbox_word("") is None
    assert parse_sandbox_word(True) is True
    for word in ("true", "1", "yes", "on", "TRUE"):
        assert parse_sandbox_word(word) is True
    for word in ("false", "0", "no", "off"):
        assert parse_sandbox_word(word) is False
    with pytest.raises(ExecutionControlsError, match="--sandbox expects true or false"):
        parse_sandbox_word("maybe")


def test_spawn_re_exports_the_same_permission_vocabulary() -> None:
    assert spawn.PERMISSION_POLICIES is PERMISSION_POLICIES


def test_claude_auto_and_sandbox_true_map_to_supported_interfaces() -> None:
    controls = resolve_execution_controls("claude", permissions="auto", sandbox=True)
    assert controls.permissions_requested == "auto"
    assert controls.permissions_effective == "auto"
    assert controls.sandbox_requested is True
    assert controls.sandbox_effective == "enabled"
    assert controls.provider_flags == (
        "--permission-mode",
        "auto",
        "--settings",
        CLAUDE_SANDBOX_ON,
    )
    assert "bypassPermissions" not in controls.provider_flags
    assert "sandbox.enabled" in controls.evidence
    receipt = controls.receipt()
    assert receipt["schema"] == "vibecrafted.execution_controls.v1"
    assert receipt["sandbox_requested"] == "true"
    assert receipt["provider_flags"] == list(controls.provider_flags)
    assert receipt["boundary"] == SANDBOX_BOUNDARY["claude"]


def test_claude_sandbox_true_emits_the_documented_no_fallback_hard_gate() -> None:
    """The exact --settings document: enabled, fail-closed, no unsandboxed retry.

    ``sandbox.enabled`` alone would let inherited settings negate the request:
    ``allowUnsandboxedCommands`` defaults to true (the model may retry a failed
    command with ``dangerouslyDisableSandbox``) and ``failIfUnavailable``
    defaults to false (a missing backend runs commands unsandboxed with a
    warning). Both are scalar keys, so the command-line ``--settings`` level
    overrides user/project/local values.
    """
    controls = resolve_execution_controls("claude", permissions="auto", sandbox=True)
    assert controls.provider_flags[-2] == "--settings"
    settings = json.loads(controls.provider_flags[-1])
    assert settings == {
        "sandbox": {
            "enabled": True,
            "failIfUnavailable": True,
            "allowUnsandboxedCommands": False,
        }
    }
    assert settings == claude_sandbox_settings(True)
    # Key order is part of the argv contract the subprocess tests pin.
    assert controls.provider_flags[-1] == CLAUDE_SANDBOX_ON
    # The receipt names the real boundary and the remaining inherited hole, and
    # does not claim the launcher observed OS enforcement.
    assert "Bash-tool sandbox" in controls.boundary
    assert "child processes" in controls.boundary
    assert "MCP servers run outside" in controls.boundary
    assert "VM" not in controls.boundary
    assert "excludedCommands" in controls.behavior
    assert "managed settings outrank --settings" in controls.behavior
    assert "not observed" in controls.behavior


def test_claude_sandbox_false_is_explicit_not_omitted() -> None:
    controls = resolve_execution_controls("claude", sandbox=False)
    assert controls.permissions_effective == "bypass"
    assert controls.sandbox_effective == "disabled"
    assert controls.provider_flags[-2:] == ("--settings", CLAUDE_SANDBOX_OFF)
    # Disabling touches one scalar key only; no hard-gate keys leak into "off".
    assert json.loads(controls.provider_flags[-1]) == {"sandbox": {"enabled": False}}
    assert claude_sandbox_settings(False) == {"sandbox": {"enabled": False}}
    assert controls.boundary == SANDBOX_BOUNDARY["claude"]


def test_omitted_controls_keep_provider_defaults() -> None:
    claude = resolve_execution_controls("claude")
    assert not claude.requested
    assert claude.permissions_requested == ""
    assert claude.permissions_effective == "bypass"
    assert claude.sandbox_effective == "provider-default"
    assert claude.provider_flags == ("--permission-mode", "bypassPermissions")
    assert claude.boundary == SANDBOX_BOUNDARY["claude"]

    # Omission stays provider-default for agy too: the launcher neither emits
    # nor infers anything about agy's persistent terminal-sandbox settings.
    agy = resolve_execution_controls("agy")
    assert agy.sandbox_effective == "provider-default"
    assert "--sandbox" not in agy.provider_flags
    assert "agy's own settings" in agy.behavior

    junie = resolve_execution_controls("junie")
    assert junie.permissions_effective == "auto"
    assert junie.provider_flags == ()

    codex = resolve_execution_controls("codex")
    # Truth, not a label: the default codex bypass runs without a sandbox.
    assert codex.sandbox_effective == "disabled"
    assert codex.provider_flags == ("--dangerously-bypass-approvals-and-sandbox",)


def test_codex_headless_auto_uses_exec_surface_not_interactive_flags() -> None:
    controls = resolve_execution_controls("codex", permissions="auto")
    assert controls.provider_flags == ("--approve-for-me",)
    assert "--ask-for-approval" not in controls.provider_flags
    assert controls.sandbox_effective == "enabled"
    # Interactive resolution keeps the interactive cell untouched.
    interactive = spawn.resolve_provider_policy(
        "codex", "local-native", "auto", "interactive"
    )
    assert interactive.flags[:2] == ("--ask-for-approval", "on-request")


def test_codex_bypass_with_sandbox_true_keeps_the_sandbox() -> None:
    controls = resolve_execution_controls("codex", permissions="bypass", sandbox=True)
    assert controls.provider_flags == ("--sandbox", "workspace-write")
    assert "--dangerously-bypass-approvals-and-sandbox" not in controls.provider_flags
    assert controls.sandbox_effective == "enabled"


@pytest.mark.parametrize(
    ("provider", "permissions", "sandbox", "alternative"),
    [
        ("codex", "auto", False, "--permissions bypass --sandbox false"),
        ("codex", "read-only", False, "--sandbox true"),
        ("junie", "", True, "Omit --sandbox"),
        ("junie", "auto", False, "Omit --sandbox"),
        ("junie", "bypass", None, "supported: auto"),
        ("agy", "", False, "Omit --sandbox to keep agy's own setting"),
        ("agy", "auto", False, "or pass --sandbox true"),
        ("codex", "accept-edits", None, "supported: bypass, auto, read-only"),
        ("cursor", "accept-edits", True, "supported: bypass, auto, read-only"),
    ],
)
def test_unenforceable_combinations_are_refused_with_an_alternative(
    provider: str, permissions: str, sandbox: bool | None, alternative: str
) -> None:
    with pytest.raises(ExecutionControlsError) as excinfo:
        resolve_execution_controls(provider, permissions=permissions, sandbox=sandbox)
    message = str(excinfo.value)
    assert message.startswith(f"{provider}:")
    assert alternative in message


def test_grok_and_cursor_and_agy_sandbox_words() -> None:
    grok_on = resolve_execution_controls("grok", permissions="auto", sandbox=True)
    assert grok_on.provider_flags == (
        "--permission-mode",
        "auto",
        "--sandbox",
        "workspace",
    )
    grok_ro = resolve_execution_controls("grok", permissions="read-only", sandbox=True)
    assert grok_ro.provider_flags[-2:] == ("--sandbox", "read-only")
    grok_off = resolve_execution_controls("grok", sandbox=False)
    assert grok_off.provider_flags[-2:] == ("--sandbox", "off")
    assert grok_off.sandbox_effective == "disabled"

    cursor_on = resolve_execution_controls("cursor", permissions="auto", sandbox=True)
    assert cursor_on.provider_flags == ("--trust", "--sandbox", "enabled")
    cursor_off = resolve_execution_controls("cursor", sandbox=False)
    assert cursor_off.provider_flags == ("--force", "--trust", "--sandbox", "disabled")

    agy_on = resolve_execution_controls("agy", permissions="auto", sandbox=True)
    assert agy_on.provider_flags == ("--sandbox",)
    assert agy_on.sandbox_effective == "enabled"
    assert agy_on.boundary == SANDBOX_BOUNDARY["agy"]


def test_agy_sandbox_false_fails_closed_instead_of_claiming_disabled() -> None:
    """agy has only an opt-in flag; "no flag" is not evidence of "disabled".

    agy 1.1.27 keeps persistent terminal-sandbox settings (sandboxMode /
    enableTerminalSandbox in the binary), so omitting ``--sandbox`` may still
    run sandboxed. Without a verified explicit disable interface the resolver
    refuses explicit false with the supported alternatives; nothing is receipted
    as ``disabled`` on the strength of an absent flag.
    """
    for permissions in ("", "bypass", "auto", "accept-edits", "read-only"):
        with pytest.raises(ExecutionControlsError) as excinfo:
            resolve_execution_controls("agy", permissions=permissions, sandbox=False)
        message = str(excinfo.value)
        assert message.startswith("agy: agy 1.1.27 exposes only the opt-in --sandbox")
        assert "persistent terminal-sandbox settings" in message
        assert "Omit --sandbox to keep agy's own setting" in message
        assert "pass --sandbox true" in message
    assert "sandboxMode" in resolve_execution_controls("agy").evidence


def test_stdin_command_default_shape_is_unchanged() -> None:
    assert spawn._stdin_command("claude") == [
        "claude",
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        "bypassPermissions",
    ]
    assert spawn._stdin_command("codex") == [
        "codex",
        "exec",
        "--json",
        "--dangerously-bypass-approvals-and-sandbox",
        "-",
    ]


def test_stdin_command_carries_resolved_controls_verbatim() -> None:
    controls = resolve_execution_controls("claude", permissions="auto", sandbox=True)
    command = spawn._stdin_command("claude", controls=controls)
    assert command == [
        "claude",
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        "auto",
        "--settings",
        CLAUDE_SANDBOX_ON,
    ]
    codex = spawn._stdin_command(
        "codex", controls=resolve_execution_controls("codex", permissions="auto")
    )
    assert codex == ["codex", "exec", "--json", "--approve-for-me", "-"]
    grok = spawn._stdin_command(
        "grok", controls=resolve_execution_controls("grok", sandbox=True)
    )
    assert grok[:6] == [
        "grok",
        "--cwd",
        ".",
        "--permission-mode",
        "bypassPermissions",
        "--sandbox",
    ]
    assert grok[6] == "workspace"
    agy = spawn._stdin_command(
        "agy",
        controls=resolve_execution_controls("agy", permissions="auto", sandbox=True),
    )
    assert agy[:4] == ["agy", "--sandbox", "--add-dir", "."]
    assert agy[-5:] == [
        "--print=",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
    ]

    with pytest.raises(ValueError, match="resolved for claude, not codex"):
        spawn._stdin_command("codex", controls=controls)


def test_cursor_sandbox_flag_is_verified_against_the_help_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def surface(with_sandbox: bool) -> CursorCliSurface:
        flags = {flag: flag != "--best-of-n" for flag in CURSOR_TRACKED_FLAGS}
        flags["--sandbox"] = with_sandbox
        return CursorCliSurface(
            executable="/fake/cursor-agent",
            version="2026.09.08-test",
            help_text="",
            flags=flags,
            state=PROBE_CONFIRMED,
            detail="fake surface",
            checked_at="",
        )

    controls = resolve_execution_controls("cursor", permissions="auto", sandbox=True)
    monkeypatch.setattr(
        "vibecrafted_core.continuity.capabilities.probe_cursor_cli_surface",
        lambda executable=None: surface(True),
    )
    command = spawn._stdin_command("cursor", controls=controls)
    assert command[-3:] == ["--trust", "--sandbox", "enabled"]

    monkeypatch.setattr(
        "vibecrafted_core.continuity.capabilities.probe_cursor_cli_surface",
        lambda executable=None: surface(False),
    )
    with pytest.raises(ValueError, match="lacks required flag\\(s\\) --sandbox"):
        spawn._stdin_command("cursor", controls=controls)


def _spec(**overrides: object) -> workflow.WorkflowLaunchSpec:
    base: dict[str, object] = {
        "agent": "claude",
        "mode": "workflow",
        "skill": "workflow",
        "prompt": "go",
        "file": "",
        "runtime": "headless",
        "root": "/tmp/repo",
    }
    base.update(overrides)
    return workflow.WorkflowLaunchSpec(**base)  # type: ignore[arg-type]


def test_normalize_launch_spec_parses_and_refuses_before_launch(
    tmp_path: Path,
) -> None:
    payload = {
        "skill": "workflow",
        "agent": "claude",
        "prompt": "go",
        "root": str(tmp_path),
        "permissions": "auto",
        "sandbox": "true",
    }
    spec = workflow.normalize_launch_spec(payload, tmp_path)
    assert spec.permissions == "auto"
    assert spec.sandbox is True
    assert spec.to_payload()["sandbox"] is True

    omitted = workflow.normalize_launch_spec(
        {**payload, "permissions": "", "sandbox": ""}, tmp_path
    )
    assert omitted.permissions == ""
    assert omitted.sandbox is None

    with pytest.raises(ValueError, match="--permissions expects"):
        workflow.normalize_launch_spec({**payload, "permissions": "yolo"}, tmp_path)
    with pytest.raises(ValueError, match="--sandbox expects true or false"):
        workflow.normalize_launch_spec({**payload, "sandbox": "maybe"}, tmp_path)
    with pytest.raises(ValueError, match="junie: junie 26.8.31 exposes no sandbox"):
        workflow.normalize_launch_spec(
            {**payload, "agent": "junie", "permissions": ""}, tmp_path
        )
    with pytest.raises(ValueError, match="codex: --permissions auto runs under"):
        workflow.normalize_launch_spec(
            {**payload, "agent": "codex", "sandbox": "false"}, tmp_path
        )
    with pytest.raises(ValueError, match="agy: agy 1.1.27 exposes only the opt-in"):
        workflow.normalize_launch_spec(
            {**payload, "agent": "agy", "permissions": "", "sandbox": "false"},
            tmp_path,
        )
    with pytest.raises(ValueError, match="not carried into the research"):
        workflow.normalize_launch_spec(
            {**payload, "skill": "research", "agent": ["claude"]}, tmp_path
        )
    with pytest.raises(ValueError, match="not carried into the marbles"):
        workflow.normalize_launch_spec({**payload, "skill": "marbles"}, tmp_path)


def test_build_launch_command_injects_controls_only_when_requested(
    tmp_path: Path,
) -> None:
    default = workflow.build_launch_command(_spec(), tmp_path)
    assert default[-2:] == ["--permission-mode", "bypassPermissions"]
    assert "--settings" not in default

    founder = workflow.build_launch_command(
        _spec(model="claude-fable-5-1", permissions="auto", sandbox=True), tmp_path
    )
    assert founder[:3] == ["claude", "--model", "claude-fable-5-1"]
    assert founder[-4:] == [
        "--permission-mode",
        "auto",
        "--settings",
        CLAUDE_SANDBOX_ON,
    ]
    assert "bypassPermissions" not in founder

    codex = workflow.build_launch_command(
        _spec(agent="codex", permissions="auto"), tmp_path
    )
    assert codex == ["codex", "exec", "--json", "--approve-for-me", "-"]

    with pytest.raises(ExecutionControlsError, match="junie: junie 26.8.31"):
        workflow.build_launch_command(_spec(agent="junie", sandbox=True), tmp_path)


def test_launch_execution_controls_receipt_states_defaults_and_requests(
    tmp_path: Path,
) -> None:
    default = workflow.launch_execution_controls(_spec())
    assert default is not None
    assert default.receipt()["permissions_requested"] == ""
    assert default.receipt()["permissions_effective"] == "bypass"
    assert default.receipt()["sandbox_requested"] == ""
    assert default.receipt()["sandbox_effective"] == "provider-default"

    requested = workflow.launch_execution_controls(
        _spec(permissions="read-only", sandbox=False)
    )
    assert requested is not None
    assert requested.receipt()["permissions_effective"] == "read-only"
    assert requested.receipt()["sandbox_requested"] == "false"
    assert requested.receipt()["sandbox_effective"] == "disabled"

    # Supervised runtimes have no controls receipt and refuse explicit ones.
    assert workflow.launch_execution_controls(_spec(skill="marbles")) is None
    with pytest.raises(ExecutionControlsError, match="not carried into the marbles"):
        workflow.launch_execution_controls(_spec(skill="marbles", permissions="auto"))


def test_machine_launch_receipt_projects_execution_controls() -> None:
    controls = resolve_execution_controls("claude", permissions="auto", sandbox=True)
    payload = {
        "accepted": True,
        "run_id": "work-1",
        "agent": "claude",
        "skill": "workflow",
        "root": "/tmp/repo",
        "execution_controls": controls.receipt(),
    }
    receipt = workflow.machine_launch_receipt(payload)
    assert receipt["execution_controls"]["permissions_effective"] == "auto"
    assert receipt["execution_controls"]["sandbox_effective"] == "enabled"
    assert receipt["execution_controls"]["boundary"] == SANDBOX_BOUNDARY["claude"]
    assert "execution_controls" not in workflow.machine_launch_receipt(
        {k: v for k, v in payload.items() if k != "execution_controls"}
    )
