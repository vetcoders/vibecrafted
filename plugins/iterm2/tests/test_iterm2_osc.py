"""Tests for the OSC primitive emitters."""

from __future__ import annotations

import base64
import subprocess
import sys

import pytest
from vibecrafted_iterm2 import iterm2_osc as osc


# Convenience: sequences should always start with ESC ] and end with BEL.
def _wrapped(sequence: str) -> bool:
    return sequence.startswith("\x1b]") and sequence.endswith("\x07")


# --------------------------------------------------------------------- OSC 1337


def test_set_badge_base64_encoded() -> None:
    out = osc.set_badge("Hello world")
    assert _wrapped(out)
    assert "SetBadgeFormat=" in out
    payload = out.split("SetBadgeFormat=")[1].rstrip("\x07")
    assert base64.b64decode(payload).decode("utf-8") == "Hello world"


def test_set_badge_handles_unicode() -> None:
    out = osc.set_badge("host-a 🐉 vetcoders")
    payload = out.split("SetBadgeFormat=")[1].rstrip("\x07")
    assert base64.b64decode(payload).decode("utf-8") == "host-a 🐉 vetcoders"


def test_set_profile_passthrough() -> None:
    out = osc.set_profile("Vetcoders / host-a")
    assert out == "\x1b]1337;SetProfile=Vetcoders / host-a\x07"


def test_set_user_var_b64_value_only() -> None:
    out = osc.set_user_var("vetcoders.repo", "vibecrafted")
    assert out.startswith("\x1b]1337;SetUserVar=vetcoders.repo=")
    payload = out.split("=", 2)[2].rstrip("\x07")
    assert base64.b64decode(payload).decode("utf-8") == "vibecrafted"


def test_set_colors_with_preset() -> None:
    out = osc.set_colors("preset", "Solarized Dark")
    assert out == "\x1b]1337;SetColors=preset=Solarized Dark\x07"


def test_set_mark_no_value() -> None:
    assert osc.set_mark() == "\x1b]1337;SetMark\x07"


def test_request_attention_modes() -> None:
    assert "fireworks" in osc.request_attention("fireworks")
    assert osc.request_attention("once").endswith("once\x07")
    assert osc.request_attention("no").endswith("no\x07")


def test_cursor_shape_values() -> None:
    assert osc.cursor_shape(0).endswith("CursorShape=0\x07")
    assert osc.cursor_shape(1).endswith("CursorShape=1\x07")
    assert osc.cursor_shape(2).endswith("CursorShape=2\x07")


def test_highlight_cursor_line_bool_to_yes_no() -> None:
    assert osc.highlight_cursor_line(True).endswith("yes\x07")
    assert osc.highlight_cursor_line(False).endswith("no\x07")


# --------------------------------------------------------------------- blocks


def test_block_lifecycle() -> None:
    start = osc.block_start("build")
    end = osc.block_end("build")
    fold = osc.update_block("build", "fold")
    unfold = osc.update_block("build", "unfold")
    assert "Block=id=build;attr=start" in start
    assert "Block=id=build;attr=end" in end
    assert "UpdateBlock=id=build;action=fold" in fold
    assert "UpdateBlock=id=build;action=unfold" in unfold


# --------------------------------------------------------------------- buttons


def test_custom_button_signature() -> None:
    out = osc.custom_button(42, "star.fill")
    assert "Button=type=custom;code=42;icon=star.fill" in out


def test_invalidate_buttons() -> None:
    assert osc.invalidate_buttons().endswith("Button=type=custom\x07")


# --------------------------------------------------------------------- OSC 9 progress


def test_progress_clear_state() -> None:
    assert osc.progress(0) == "\x1b]9;4;0\x07"


def test_progress_indeterminate_no_percent() -> None:
    assert osc.progress(3) == "\x1b]9;4;3\x07"


def test_progress_success_requires_percent() -> None:
    with pytest.raises(ValueError):
        osc.progress(1)


def test_progress_warning_requires_percent() -> None:
    with pytest.raises(ValueError):
        osc.progress(4)


def test_progress_error_optional_percent() -> None:
    assert osc.progress(2) == "\x1b]9;4;2\x07"
    assert osc.progress(2, 75) == "\x1b]9;4;2;75\x07"


def test_progress_success_with_percent() -> None:
    assert osc.progress(1, 50) == "\x1b]9;4;1;50\x07"


def test_post_notification() -> None:
    assert osc.post_notification("build done") == "\x1b]9;build done\x07"


# --------------------------------------------------------------------- OSC 8 hyperlinks


def test_hyperlink_no_id() -> None:
    assert osc.hyperlink("https://example.com", "click me") == (
        "\x1b]8;;https://example.com\x07click me\x1b]8;;\x07"
    )


def test_hyperlink_with_id() -> None:
    out = osc.hyperlink("https://example.com", "click", link_id="link-1")
    assert "id=link-1;https://example.com" in out


# --------------------------------------------------------------------- OSC 133 FinalTerm


def test_ftcs_prompt() -> None:
    assert osc.ftcs_prompt() == "\x1b]133;A\x07"


def test_ftcs_command_lifecycle() -> None:
    assert osc.ftcs_command_start() == "\x1b]133;B\x07"
    assert osc.ftcs_command_executed() == "\x1b]133;C\x07"
    assert osc.ftcs_command_finished() == "\x1b]133;D\x07"
    assert osc.ftcs_command_finished(0) == "\x1b]133;D;0\x07"
    assert osc.ftcs_command_finished(127) == "\x1b]133;D;127\x07"


def test_remote_host_format() -> None:
    assert (
        osc.remote_host("tester", "host-a") == "\x1b]1337;RemoteHost=tester@host-a\x07"
    )


# --------------------------------------------------------------------- CLI


def test_cli_progress_with_percent(capsys: pytest.CaptureFixture[str]) -> None:
    rc = osc._cli(["progress", "1", "50"])
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out == "\x1b]9;4;1;50\x07"


def test_cli_badge_b64(capsys: pytest.CaptureFixture[str]) -> None:
    rc = osc._cli(["badge", "Test"])
    captured = capsys.readouterr()
    assert rc == 0
    payload = captured.out.split("SetBadgeFormat=")[1].rstrip("\x07")
    assert base64.b64decode(payload).decode("utf-8") == "Test"


def test_cli_unknown_op() -> None:
    assert osc._cli(["nope"]) == 2


def test_cli_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert osc._cli(["--help"]) == 0
    out = capsys.readouterr().out
    assert "Usage" in out
    assert "badge" in out
    assert "progress" in out


def test_module_cli_help_runs_without_import_warning() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-Werror",
            "-m",
            "vibecrafted_iterm2.iterm2_osc",
            "--help",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "Usage: python -m vibecrafted_iterm2.iterm2_osc" in result.stdout
    assert "RuntimeWarning" not in result.stderr
