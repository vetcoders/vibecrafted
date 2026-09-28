from __future__ import annotations

import json
from pathlib import Path

import pytest
from vibecrafted_core.runtime_transcript import (
    InteractiveTranscriptCapture,
    is_private_launch_secret,
    runtime_transcript_manifest_path,
    validate_runtime_transcript,
    write_runtime_transcript_manifest,
)


def _authorized_transcript(tmp_path: Path) -> tuple[Path, Path]:
    transcript = tmp_path / "run.transcript.log"
    transcript.write_text("durable runtime evidence\n", encoding="utf-8")
    manifest = write_runtime_transcript_manifest(transcript, run_id="run-1")
    assert manifest is not None
    return transcript, manifest


def test_manifest_round_trip_authorizes_one_canonical_transcript(
    tmp_path: Path,
) -> None:
    transcript, manifest = _authorized_transcript(tmp_path)

    assert runtime_transcript_manifest_path(transcript) == manifest
    assert validate_runtime_transcript(transcript, run_id="run-1") == transcript
    assert not list(tmp_path.glob(f".{manifest.name}.*.tmp"))


@pytest.mark.parametrize(
    "corruption",
    [
        "missing_transcript",
        "symlinked_transcript",
        "empty_transcript",
        "missing_manifest",
        "symlinked_manifest",
        "wrong_run",
        "wrong_root",
        "wrong_path",
        "wrong_bytes",
        "wrong_hash",
    ],
)
def test_validation_fails_closed_for_untrusted_evidence(
    tmp_path: Path,
    corruption: str,
) -> None:
    transcript, manifest = _authorized_transcript(tmp_path)
    requested = transcript

    if corruption == "missing_transcript":
        transcript.unlink()
    elif corruption == "symlinked_transcript":
        alias = tmp_path / "alias.log"
        alias.symlink_to(transcript)
        requested = alias
    elif corruption == "empty_transcript":
        transcript.write_bytes(b"")
    elif corruption == "missing_manifest":
        manifest.unlink()
    elif corruption == "symlinked_manifest":
        real_manifest = tmp_path / "real.manifest.json"
        manifest.rename(real_manifest)
        manifest.symlink_to(real_manifest)
    else:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        if corruption == "wrong_run":
            payload["run_id"] = "some-other-run"
        elif corruption == "wrong_root":
            outside = tmp_path / "outside"
            outside.mkdir()
            payload["root"] = str(outside)
        elif corruption == "wrong_path":
            other = tmp_path / "other.log"
            other.write_bytes(transcript.read_bytes())
            payload["transcript"] = str(other)
        elif corruption == "wrong_bytes":
            payload["bytes"] += 1
        elif corruption == "wrong_hash":
            payload["sha256"] = "0" * 64
        manifest.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    assert validate_runtime_transcript(requested, run_id="run-1") is None


@pytest.mark.parametrize(
    "prompt",
    [
        "",
        "   \n",
        "/vc-init",
        "/vc-workflow",
        "  /vc-init\n",
        "napraw to",
        "short token",
    ],
)
def test_public_or_degenerate_launch_prompts_are_not_secrets(prompt: str) -> None:
    assert is_private_launch_secret(prompt) is False


@pytest.mark.parametrize(
    "prompt",
    [
        "Zbadaj regresję telemetrii i napraw pipeline usage",
        "/vc-init a potem opisz wynik orientacji w raporcie",
        "x" * 64,
    ],
)
def test_substantive_launch_prompts_stay_private(prompt: str) -> None:
    assert is_private_launch_secret(prompt) is True


def _capture_transcript(tmp_path: Path, secret: str, payload: bytes) -> bytes:
    import os
    import pty

    outer, display = pty.openpty()
    transcript = tmp_path / "transcript.log"
    capture = InteractiveTranscriptCapture(transcript, secret, display_fd=display)
    provider_output = os.dup(capture.slave)
    capture.start()
    try:
        os.write(provider_output, payload)
    finally:
        os.close(provider_output)
        capture.close()
        os.close(display)
        os.close(outer)
    return transcript.read_bytes()


def test_public_slash_command_launch_prompt_survives_capture(tmp_path: Path) -> None:
    payload = b"operator: /vc-init\r\nagent: running /vc-init orientation now\r\n"
    data = _capture_transcript(tmp_path, "/vc-init", payload)
    assert data.count(b"/vc-init") == 2
    assert b"[private launch input redacted]" not in data


def test_private_launch_prompt_is_still_redacted_in_capture(tmp_path: Path) -> None:
    secret = "Zbadaj wewnętrzny endpoint o tokenie k=abc123XYZ i napraw regresję"
    payload = ("echo: " + secret + " done\r\n").encode("utf-8")
    data = _capture_transcript(tmp_path, secret, payload)
    assert secret.encode("utf-8") not in data
    assert b"[private launch input redacted]" in data
