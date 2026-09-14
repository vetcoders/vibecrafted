"""Product entry choke — execute the shipped wrapper and shell, never grep them.

Four owners are under test, each proved on its own terms:

1. ``scripts/vc-frame-product-entry.sh`` — the published ``bin/vc-frame``.
   Its engine is the *physical* ``libexec/vc-frame`` of the generation the
   invoked entry physically belongs to. No Cargo sibling, no inherited
   ``VIBECRAFTED_VC_FRAME_BIN``, no PATH lookup and no moving "current"
   selector may stand in for it. The installed
   ``$HOME/.config/vibecrafted/vc-frame`` view is mandatory for *every*
   session name, and its ``--config-dir``/``--config`` reach the engine argv.
2. ``runtime/shell`` ``vc-start`` — ``_vetcoders_product_entry_prepare``.
   The installed path additionally demands the generation's own
   Python/core and a read-only runtime admission.
3. ``_vetcoders_control_plane_eye_prepare`` — one macOS service owner.
4. ``scripts/vetcoders_install.py`` — retirement of a launcher a previous
   generation published is receipt-gated; ``install-foundations.sh`` no
   longer owns an independent cleanup.

Evidence discipline
-------------------
* Engine selection, refusals and argv are proved by *running* the shipped
  wrapper and reading the real exit status and the real argv/env the engine
  received — never by counting words in the source.
* ``_NATIVE_DONOR`` is a real Mach-O/ELF file, so the wrapper's own
  ``file(1)`` classification is exercised for real in the accept case and in
  the "a shell script is not a native engine" refusal.
* Where a test needs the engine to *record* what it got, it stages a shell
  recorder plus a ``file`` shim that lies about exactly one path (the staged
  engine) and delegates every other query to ``/usr/bin/file``. Such a test
  proves argv/env/selection, not native-format admission; the two
  ``_NATIVE_DONOR`` tests carry that half with no shim at all.
* ``vc-start`` helper-level tests run in EXPLICIT developer mode
  (``VIBECRAFTED_PREFER_REPO_VC_FRAME=1`` against this directly sourced Git
  checkout). That is a documented developer entry, NOT an acceptance of an
  installed product. Installed-path admission is proved separately, by its
  own refusals.
* ``vc-start``'s status is captured immediately and re-raised as the process
  status; no later ``printf`` or attach may stand in for it.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import NamedTuple

import pytest

from scripts import vetcoders_install as installer

REPO = Path(__file__).resolve().parents[2]
WRAPPER = REPO / "scripts" / "vc-frame-product-entry.sh"
HELPER = REPO / "vibecrafted-core/vibecrafted_core/runtime/shell/vetcoders.sh"
DASHBOARD = REPO / "vibecrafted-core/vibecrafted_core/runtime/shell/lib/dashboard.sh"
FOUNDATIONS = REPO / "scripts" / "install-foundations.sh"

# Developer mode reads the frame view straight out of this checkout.
REPO_FRAME_CONFIG = REPO / "vibecrafted-core/vibecrafted_core/config/vc-frame"

# A real native executable: the wrapper's own `file -Lb` must accept it, and
# the installer's magic-byte probe agrees. Never a shell script.
_NATIVE_DONOR = Path("/usr/bin/true")


def _write_fake_bin(bin_dir: Path, name: str, body: str) -> Path:
    path = bin_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _write_probe_tool(directory: Path, name: str, marker: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    tool = directory / name
    tool.write_text(f"#!/bin/sh\nprintf '{marker}\\n'\n", encoding="utf-8")
    tool.chmod(0o755)
    return tool


class Generation(NamedTuple):
    """One staged, physically distinct installed generation."""

    home: Path
    root: Path
    wrapper: Path
    engine: Path
    config: Path
    record: Path
    env: dict[str, str]


def _recorder_body(record: Path) -> str:
    """An engine that reports the argv and environment it actually received."""
    return textwrap.dedent(
        f"""\
        #!/bin/sh
        {{
          for argument in "$@"; do
            printf 'ARGV=%s\\n' "$argument"
          done
          printf 'ENGINE=%s\\n' "$0"
          printf 'VC_FRAME_CONFIG_DIR=%s\\n' "${{VC_FRAME_CONFIG_DIR-}}"
          printf 'VC_FRAME_CONFIG_FILE=%s\\n' "${{VC_FRAME_CONFIG_FILE-}}"
          printf 'XDG_CONFIG_HOME=%s\\n' "${{XDG_CONFIG_HOME-}}"
          printf 'ZDOTDIR=%s\\n' "${{ZDOTDIR-}}"
          printf 'VC_FRAME_SOCKET_DIR=%s\\n' "${{VC_FRAME_SOCKET_DIR-}}"
          printf 'ZELLIJ_SOCKET_DIR=%s\\n' "${{ZELLIJ_SOCKET_DIR-}}"
          printf 'ZELLIJ_CONFIG_DIR=%s\\n' \\
            "${{ZELLIJ_CONFIG_DIR+set:}}${{ZELLIJ_CONFIG_DIR-}}"
          printf 'ZELLIJ_CONFIG_FILE=%s\\n' \\
            "${{ZELLIJ_CONFIG_FILE+set:}}${{ZELLIJ_CONFIG_FILE-}}"
          printf 'VIBECRAFTED_VC_FRAME_BIN=%s\\n' "${{VIBECRAFTED_VC_FRAME_BIN-}}"
          printf 'VIBECRAFTED_RUNTIME_ROOT=%s\\n' "${{VIBECRAFTED_RUNTIME_ROOT-}}"
          printf 'PREFER_REPO=%s\\n' "${{VIBECRAFTED_PREFER_REPO_VC_FRAME-}}"
          printf 'PREFER_SPAWN=%s\\n' "${{VIBECRAFTED_PREFER_REPO_SPAWN-}}"
        }} > {installer.shlex_quote(str(record))}
        exit 0
        """
    )


def _write_file_shim(tool_bin: Path, engine: Path) -> Path:
    """Classify exactly one staged path as native; delegate everything else.

    The lie is bounded to the path this test staged, so the shim cannot hide
    an unrelated regression in how the wrapper classifies other candidates.
    """
    return _write_fake_bin(
        tool_bin,
        "file",
        textwrap.dedent(
            f"""\
            #!/bin/sh
            for argument in "$@"; do
              if [ "$argument" = {installer.shlex_quote(str(engine))} ]; then
                printf 'Mach-O 64-bit executable arm64\\n'
                exit 0
              fi
            done
            exec /usr/bin/file "$@"
            """
        ),
    )


def _stage_generation(
    tmp_path: Path,
    *,
    engine: str = "recording",
    config: str = "installed",
    generation: str = "release",
    with_start: bool = False,
) -> Generation:
    """Stage one physical generation plus its mandatory installed config view.

    ``engine``:
      ``recording``  shell recorder + bounded ``file`` shim (argv/env truth)
      ``native``     a real Mach-O/ELF donor, classified by the real ``file``
      ``shell``      a shell script, NO shim — must be refused as non-native
      ``symlink``    a symlink to the native donor — must be refused
      ``absent``     no engine at all

    ``config``:
      ``installed`` | ``absent`` | ``symlinked-view``
      | ``symlinked-config`` | ``symlinked-layouts``
    """
    home = (tmp_path / "home").resolve()
    root = (tmp_path / generation).resolve()
    wrapper = root / "bin" / "vc-frame"
    engine_path = root / "libexec" / "vc-frame"
    record = (tmp_path / "engine-record").resolve()
    view = home / ".config/vibecrafted/vc-frame"

    home.mkdir(parents=True, exist_ok=True)
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    engine_path.parent.mkdir(parents=True, exist_ok=True)
    wrapper.write_text(WRAPPER.read_text(encoding="utf-8"), encoding="utf-8")
    wrapper.chmod(0o755)
    if with_start:
        _write_fake_bin(
            root / "bin",
            "vc-start",
            "#!/bin/sh\n"
            f"printf 'VC_START_RAN\\n' > {installer.shlex_quote(str(record))}\n"
            "exit 0\n",
        )

    if engine == "recording":
        engine_path.write_text(_recorder_body(record), encoding="utf-8")
        engine_path.chmod(0o755)
    elif engine == "native":
        shutil.copyfile(_NATIVE_DONOR, engine_path)
        engine_path.chmod(0o755)
    elif engine == "shell":
        engine_path.write_text(_recorder_body(record), encoding="utf-8")
        engine_path.chmod(0o755)
    elif engine == "symlink":
        native = root / "libexec" / "vc-frame-native"
        shutil.copyfile(_NATIVE_DONOR, native)
        native.chmod(0o755)
        engine_path.symlink_to(native)
    elif engine != "absent":  # pragma: no cover - test construction error
        raise ValueError(f"unknown engine mode: {engine}")

    if config != "absent":
        real_view = view
        if config == "symlinked-view":
            real_view = home / ".config/vibecrafted/vc-frame-real"
        real_view.mkdir(parents=True, exist_ok=True)
        (real_view / "config.kdl").write_text('default_shell "zsh"\n', encoding="utf-8")
        (real_view / "layouts").mkdir(exist_ok=True)
        (real_view / "layouts" / "operator.kdl").write_text(
            "layout {\n}\n", encoding="utf-8"
        )
        if config == "symlinked-view":
            view.symlink_to(real_view)
        elif config == "symlinked-config":
            outside = home / "outside-config.kdl"
            outside.write_text("// foreign\n", encoding="utf-8")
            (view / "config.kdl").unlink()
            (view / "config.kdl").symlink_to(outside)
        elif config == "symlinked-layouts":
            outside = home / "outside-layouts"
            outside.mkdir()
            shutil.rmtree(view / "layouts")
            (view / "layouts").symlink_to(outside)
        elif config != "installed":  # pragma: no cover - construction error
            raise ValueError(f"unknown config mode: {config}")

    path_entries = ["/usr/bin", "/bin"]
    if engine == "recording":
        tool_bin = (tmp_path / "tool-bin").resolve()
        tool_bin.mkdir(parents=True, exist_ok=True)
        _write_file_shim(tool_bin, engine_path)
        path_entries.insert(0, str(tool_bin))

    env = {
        "PATH": ":".join(path_entries),
        "HOME": str(home),
        "USER": "test",
    }
    return Generation(home, root, wrapper, engine_path, view, record, env)


def _read_record(record: Path) -> dict[str, object]:
    """Decode what the engine actually received."""
    fields: dict[str, object] = {}
    argv: list[str] = []
    for line in record.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        if key == "ARGV":
            argv.append(value)
        else:
            fields[key] = value
    fields["argv"] = argv
    return fields


def _run_wrapper(
    generation: Generation, *argv: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(generation.wrapper), *argv],
        capture_output=True,
        text=True,
        env={**generation.env, **(env or {})},
        check=False,
        timeout=30,
    )


# ---------------------------------------------------------------------------
# The engine: the physical libexec of the generation that was invoked
# ---------------------------------------------------------------------------


def test_wrapper_exists_executable_and_carries_the_product_pins() -> None:
    """Cheap shape guard for the shipped file the executing tests copy."""
    assert WRAPPER.is_file()
    assert WRAPPER.stat().st_mode & stat.S_IXUSR
    text = WRAPPER.read_text(encoding="utf-8")
    # The retired sibling is gone for good, and no session-name exemption
    # may return: every name needs the installed product config.
    assert "vc-frame.real" not in text
    assert "is_product_session_name" not in text


def test_wrapper_accepts_a_real_native_engine_without_any_file_shim(
    tmp_path: Path,
) -> None:
    """The real classifier on a real Mach-O/ELF donor: accepted and exec'd."""
    generation = _stage_generation(tmp_path, engine="native")
    proc = _run_wrapper(generation, "list-sessions")
    # /usr/bin/true ignores argv and exits 0 — reaching it IS the proof.
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert proc.stderr == ""


def test_wrapper_refuses_a_shell_engine_that_is_not_native(tmp_path: Path) -> None:
    """No shim: a shell script in libexec is not an engine, and never runs."""
    generation = _stage_generation(tmp_path, engine="shell")
    proc = _run_wrapper(generation, "list-sessions")
    assert proc.returncode == 127, (proc.stdout, proc.stderr)
    assert "native engine missing from selected generation" in proc.stderr
    assert str(generation.engine) in proc.stderr
    assert not generation.record.exists()


def test_wrapper_refuses_a_symlinked_engine(tmp_path: Path) -> None:
    """A selected generation carries its engine physically, not by reference."""
    generation = _stage_generation(tmp_path, engine="symlink")
    proc = _run_wrapper(generation, "list-sessions")
    assert proc.returncode == 127, (proc.stdout, proc.stderr)
    assert "native engine missing from selected generation" in proc.stderr


def test_wrapper_refuses_a_generation_without_an_engine(tmp_path: Path) -> None:
    generation = _stage_generation(tmp_path, engine="absent")
    proc = _run_wrapper(generation, "list-sessions")
    assert proc.returncode == 127, (proc.stdout, proc.stderr)
    assert "native engine missing from selected generation" in proc.stderr
    assert "runtime-install" in proc.stderr


def test_wrapper_ignores_inherited_cargo_and_path_engines(tmp_path: Path) -> None:
    """Ambient overrides are decoys: only the adjacent libexec may run."""
    generation = _stage_generation(tmp_path, engine="recording")
    decoy_dir = (tmp_path / "decoys").resolve()
    cargo_bin = generation.home / ".cargo" / "bin"
    ambient_marker = tmp_path / "decoy-ran"
    decoy_body = (
        "#!/bin/sh\n"
        "printf 'DECOY_RAN=%s\\n' \"$0\" >> "
        f"{installer.shlex_quote(str(ambient_marker))}\n"
        "exit 91\n"
    )
    inherited = _write_fake_bin(decoy_dir, "vc-frame-inherited", decoy_body)
    _write_fake_bin(cargo_bin, "vc-frame", decoy_body)
    _write_fake_bin(decoy_dir, "vc-frame", decoy_body)

    proc = _run_wrapper(
        generation,
        "list-sessions",
        env={
            "PATH": f"{decoy_dir}:{generation.env['PATH']}",
            "VIBECRAFTED_VC_FRAME_BIN": str(inherited),
            "VIBECRAFTED_PREFER_REPO_VC_FRAME": "1",
            "VIBECRAFTED_PREFER_REPO_SPAWN": "1",
        },
    )

    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert not ambient_marker.exists()
    record = _read_record(generation.record)
    assert record["ENGINE"] == str(generation.engine)
    # The inherited value is replaced by the physical engine, and the
    # development preferences are dropped for the child.
    assert record["VIBECRAFTED_VC_FRAME_BIN"] == str(generation.engine)
    assert record["PREFER_REPO"] == ""
    assert record["PREFER_SPAWN"] == ""
    assert record["VIBECRAFTED_RUNTIME_ROOT"] == str(generation.root)


def test_wrapper_follows_a_symlinked_entry_to_its_physical_generation(
    tmp_path: Path,
) -> None:
    """A public name may be a symlink; the payload it lands on decides."""
    generation = _stage_generation(tmp_path, engine="recording")
    public = (tmp_path / "public-bin").resolve()
    public.mkdir(parents=True, exist_ok=True)
    ambient = public / "vc-frame"
    ambient.symlink_to(generation.wrapper)

    proc = subprocess.run(
        [str(ambient), "list-sessions"],
        capture_output=True,
        text=True,
        env=generation.env,
        check=False,
        timeout=30,
    )

    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    record = _read_record(generation.record)
    assert record["ENGINE"] == str(generation.engine)
    assert record["argv"][-1] == "list-sessions"


def test_wrapper_does_not_recurse_into_a_sibling_wrapper(tmp_path: Path) -> None:
    """A generation whose libexec is another copy of the wrapper is unusable.

    Without the native-format demand this shape recursed until the process
    table gave out; the refusal must be immediate.
    """
    generation = _stage_generation(tmp_path, engine="absent")
    generation.engine.write_text(WRAPPER.read_text(encoding="utf-8"), encoding="utf-8")
    generation.engine.chmod(0o755)

    proc = _run_wrapper(generation, "list-sessions")

    assert proc.returncode == 127, (proc.stdout, proc.stderr)
    assert "native engine missing from selected generation" in proc.stderr


# ---------------------------------------------------------------------------
# The installed config view: mandatory for every session name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(["attach", "vibecrafted"], id="attach-product"),
        pytest.param(["attach", "scratch"], id="attach-scratch"),
        pytest.param(["-s", "operator", "list-sessions"], id="dash-s-operator"),
        pytest.param(["-s", "scratch", "list-sessions"], id="dash-s-scratch"),
        pytest.param(["list-sessions"], id="bare-subcommand"),
        pytest.param(["action", "new-tab"], id="action"),
    ],
)
def test_wrapper_requires_installed_config_for_every_session_name(
    tmp_path: Path, argv: list[str]
) -> None:
    """The name never buys an exemption — `scratch` needs the product too.

    Standalone engine/developer tooling remains a separate, explicit entry.
    """
    generation = _stage_generation(tmp_path, engine="recording", config="absent")
    proc = _run_wrapper(generation, *argv)
    assert proc.returncode == 2, (proc.stdout, proc.stderr)
    assert "installed product config/layouts missing or symlinked" in proc.stderr
    assert str(generation.config) in proc.stderr
    assert not generation.record.exists()


@pytest.mark.parametrize(
    "flag",
    ["--version", "-V", "--help", "-h"],
)
def test_wrapper_identity_probes_do_not_require_installed_config(
    tmp_path: Path, flag: str
) -> None:
    """Pack inventory asks `--version` before runtime-install writes the view."""
    generation = _stage_generation(tmp_path, engine="recording", config="absent")
    proc = _run_wrapper(generation, flag)
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    record = _read_record(generation.record)
    assert record["ENGINE"] == str(generation.engine)
    assert record["argv"] == [flag]


@pytest.mark.parametrize(
    "shape",
    ["symlinked-view", "symlinked-config", "symlinked-layouts"],
)
def test_wrapper_refuses_a_symlinked_product_config(tmp_path: Path, shape: str) -> None:
    """Config must be installer-owned files, never a redirect."""
    generation = _stage_generation(tmp_path, engine="recording", config=shape)
    proc = _run_wrapper(generation, "list-sessions")
    assert proc.returncode == 2, (proc.stdout, proc.stderr)
    assert "installed product config/layouts missing or symlinked" in proc.stderr
    assert not generation.record.exists()


def test_wrapper_does_not_accept_a_config_view_from_xdg_config_home(
    tmp_path: Path,
) -> None:
    """`$HOME/.config/vibecrafted/vc-frame` is the owner; XDG cannot move it."""
    generation = _stage_generation(tmp_path, engine="recording", config="absent")
    foreign = (tmp_path / "xdg").resolve()
    foreign_view = foreign / "vibecrafted/vc-frame"
    (foreign_view / "layouts").mkdir(parents=True)
    (foreign_view / "config.kdl").write_text("// foreign\n", encoding="utf-8")

    proc = _run_wrapper(
        generation, "list-sessions", env={"XDG_CONFIG_HOME": str(foreign)}
    )

    assert proc.returncode == 2, (proc.stdout, proc.stderr)
    assert str(generation.config) in proc.stderr
    assert not generation.record.exists()


def test_wrapper_pins_the_installed_config_into_the_engine_argv(
    tmp_path: Path,
) -> None:
    """The engine is addressed with the product's own config, plus caller argv."""
    generation = _stage_generation(tmp_path, engine="recording")
    proc = _run_wrapper(
        generation,
        "attach",
        "vibecrafted",
        env={
            "ZELLIJ_CONFIG_DIR": "/ambient/config",
            "ZELLIJ_CONFIG_FILE": "/ambient/config/config.kdl",
            "XDG_CONFIG_HOME": str(tmp_path / "foreign-xdg"),
        },
    )

    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    record = _read_record(generation.record)
    assert record["argv"] == [
        "--config-dir",
        str(generation.config),
        "--config",
        str(generation.config / "config.kdl"),
        "attach",
        "vibecrafted",
    ]
    assert record["VC_FRAME_CONFIG_DIR"] == str(generation.config)
    assert record["VC_FRAME_CONFIG_FILE"] == str(generation.config / "config.kdl")
    # Shipped key bindings address scripts through XDG_CONFIG_HOME, which the
    # product pins to its own home; the inherited frame config is dropped.
    assert record["XDG_CONFIG_HOME"] == str(generation.home / ".config")
    assert record["ZELLIJ_CONFIG_DIR"] == ""
    assert record["ZELLIJ_CONFIG_FILE"] == ""


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(["--config-dir", "/tmp/foreign"], id="config-dir"),
        pytest.param(["--config-dir=/tmp/foreign"], id="config-dir-inline"),
        pytest.param(["--config", "/tmp/foreign.kdl"], id="config"),
        pytest.param(["-c", "/tmp/foreign"], id="short-config"),
        pytest.param(["--data-dir", "/tmp/foreign"], id="data-dir"),
        pytest.param(["--layout-string", "layout {}"], id="layout-string"),
        pytest.param(["options", "--theme-dir", "/tmp/themes"], id="options-theme"),
    ],
)
def test_wrapper_refuses_caller_supplied_configuration(
    tmp_path: Path, argv: list[str]
) -> None:
    """Configuration and assets are product-owned, even with a valid install."""
    generation = _stage_generation(tmp_path, engine="recording")
    proc = _run_wrapper(generation, *argv)
    assert proc.returncode == 2, (proc.stdout, proc.stderr)
    assert "configuration and assets are product-owned" in proc.stderr
    assert not generation.record.exists()


def test_wrapper_refuses_a_root_short_option_cluster(tmp_path: Path) -> None:
    """A cluster could hide -c/-l/-n; the bounded refusal stays at the entry."""
    generation = _stage_generation(tmp_path, engine="recording")
    proc = _run_wrapper(generation, "-dc")
    assert proc.returncode == 2, (proc.stdout, proc.stderr)
    assert "use separate root short options" in proc.stderr
    assert not generation.record.exists()


def test_wrapper_resolves_a_layout_name_inside_the_installed_layouts(
    tmp_path: Path,
) -> None:
    generation = _stage_generation(tmp_path, engine="recording")
    proc = _run_wrapper(generation, "-l", "operator")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    record = _read_record(generation.record)
    assert record["argv"][-2:] == [
        "-l",
        str(generation.config / "layouts" / "operator.kdl"),
    ]


@pytest.mark.parametrize(
    "layout",
    [
        pytest.param("/etc/passwd", id="absolute-outside"),
        pytest.param("../escape", id="relative-escape"),
        pytest.param("missing", id="missing-name"),
    ],
)
def test_wrapper_refuses_a_layout_outside_the_installed_layouts(
    tmp_path: Path, layout: str
) -> None:
    generation = _stage_generation(tmp_path, engine="recording")
    proc = _run_wrapper(generation, "--layout", layout)
    assert proc.returncode == 2, (proc.stdout, proc.stderr)
    assert "layout must be an installed file" in proc.stderr
    assert not generation.record.exists()


def test_wrapper_refuses_a_symlinked_layout_inside_the_installed_layouts(
    tmp_path: Path,
) -> None:
    """A layout may not be a redirect out of the product's own assets."""
    generation = _stage_generation(tmp_path, engine="recording")
    outside = tmp_path / "foreign.kdl"
    outside.write_text("layout {\n}\n", encoding="utf-8")
    (generation.config / "layouts" / "smuggled.kdl").symlink_to(outside)

    proc = _run_wrapper(generation, "-l", "smuggled")

    assert proc.returncode == 2, (proc.stdout, proc.stderr)
    assert "layout must be an installed file" in proc.stderr
    assert not generation.record.exists()


def test_wrapper_keeps_execution_payloads_behind_the_delimiter_native(
    tmp_path: Path,
) -> None:
    """Never scan command/text data behind `--` for product flags."""
    generation = _stage_generation(tmp_path, engine="recording")
    proc = _run_wrapper(generation, "action", "new-tab", "--", "sh", "-c", "-l foo")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    record = _read_record(generation.record)
    assert record["argv"][-4:] == ["--", "sh", "-c", "-l foo"]


# ---------------------------------------------------------------------------
# Environment pinned for the process the wrapper execs
# ---------------------------------------------------------------------------


def test_product_frame_entry_pins_zdotdir_for_new_server(tmp_path: Path) -> None:
    """Panes this server starts or restores get the product profile."""
    generation = _stage_generation(tmp_path, engine="recording")
    proc = _run_wrapper(generation, "list-sessions")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    record = _read_record(generation.record)
    assert record["ZDOTDIR"] == str(generation.home / ".config/vibecrafted/vc-terminal")
    assert 'default_shell "zsh"' in (generation.config / "config.kdl").read_text(
        encoding="utf-8"
    )


def test_wrapper_pin_replaces_incoming_client_zdotdir_for_this_process(
    tmp_path: Path,
) -> None:
    """Wrapper pin is this-process behavior, not live-server reception."""
    generation = _stage_generation(tmp_path, engine="recording")
    proc = _run_wrapper(
        generation, "list-sessions", env={"ZDOTDIR": "/new/client/zdot"}
    )
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    record = _read_record(generation.record)
    assert record["ZDOTDIR"] == str(generation.home / ".config/vibecrafted/vc-terminal")
    assert record["ZDOTDIR"] != "/new/client/zdot"


@pytest.mark.skipif(sys.platform != "darwin", reason="darwin socket namespace")
def test_wrapper_pins_darwin_socket_dir_when_unset(tmp_path: Path) -> None:
    generation = _stage_generation(tmp_path, engine="recording")
    proc = _run_wrapper(generation, "list-sessions")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    record = _read_record(generation.record)
    expected = f"/tmp/vc-frame-{os.getuid()}"
    assert record["VC_FRAME_SOCKET_DIR"] == expected
    assert record["ZELLIJ_SOCKET_DIR"] == expected


@pytest.mark.skipif(sys.platform != "darwin", reason="darwin socket namespace")
def test_wrapper_keeps_an_explicit_socket_override(tmp_path: Path) -> None:
    """Explicit session overrides survive; the pin only fills a vacuum."""
    generation = _stage_generation(tmp_path, engine="recording")
    chosen = str(tmp_path / "chosen-sockets")
    proc = _run_wrapper(
        generation, "list-sessions", env={"VC_FRAME_SOCKET_DIR": chosen}
    )
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    record = _read_record(generation.record)
    assert record["VC_FRAME_SOCKET_DIR"] == chosen


def test_wrapper_without_arguments_enters_the_product_start(tmp_path: Path) -> None:
    """The framework Start here surface stays reachable through the generation."""
    generation = _stage_generation(tmp_path, engine="native", with_start=True)
    proc = _run_wrapper(generation)
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert generation.record.read_text(encoding="utf-8").strip() == "VC_START_RAN"


def test_wrapper_without_arguments_refuses_a_generation_without_start(
    tmp_path: Path,
) -> None:
    generation = _stage_generation(tmp_path, engine="native", with_start=False)
    proc = _run_wrapper(generation)
    assert proc.returncode == 127, (proc.stdout, proc.stderr)
    assert "product start missing" in proc.stderr


# ---------------------------------------------------------------------------
# vc-start — the shipped shell choke
# ---------------------------------------------------------------------------


def _core_cli_body(
    capture: Path, *, resolve_root: Path, resolve_status: int = 0
) -> str:
    """A core CLI stand-in that records argv and PATH for every call."""
    return textwrap.dedent(
        f"""\
        #!/usr/bin/env bash
        printf '%s\\n' "$*" >> {installer.shlex_quote(str(capture))}
        printf 'PATH<%s>\\n' "$PATH" >> {installer.shlex_quote(str(capture) + ".path")}
        if [[ "$1 $2" == "workspace resolve" ]]; then
          if (( {resolve_status} != 0 )); then
            echo "workspace resolve refused" >&2
            exit {resolve_status}
          fi
          echo VIBECRAFTED_WORKSPACE_ID=probe-workspace
          echo VIBECRAFTED_SESSION_ID=probe-session
          echo VIBECRAFTED_WORKSPACE_INSTANCE_ID=probe-instance
          echo VIBECRAFTED_BUILD_ID=probe-build
          echo VIBECRAFTED_OPERATOR_SESSION=probe-place
          echo VIBECRAFTED_WORKSPACE_ROOT={installer.shlex_quote(str(resolve_root))}
          exit 0
        fi
        [[ "$*" == "server status" ]] && exit 0
        [[ "$1 $2" == "workspace session-attach" ]] && exit 0
        exit 64
        """
    )


def _git_project(path: Path) -> Path:
    """Stage a real Git work tree, carrying one commit, at ``path``.

    vc-start selects its root through the shipped launch resolver
    (``_vetcoders_select_repo`` -> ``vibecrafted_core.repo_selection``), which
    demands BOTH a work tree (``require_git=True``) and a commit, because the
    launch spec pins ``--base HEAD``. A bare ``git init`` is therefore still
    refused, only with "--base does not resolve to one available commit on this
    host" in place of "is not inside a Git repository".

    The directory is its own top level on purpose: the resolver answers with
    the top level, and THAT is what reaches ``workspace resolve --root``. A
    repository staged in a parent would silently name the parent instead.

    The name is kept short for the same reason: the default workspace name is
    the root's basename, and it is validated (24 characters) BEFORE the entry
    prepares, so a pytest-generated directory name would refuse there instead
    of at the contract under test.
    """
    path.mkdir(parents=True, exist_ok=True)
    assert len(path.name) <= 24, f"workspace name would be refused: {path.name}"
    env = {
        "PATH": "/usr/bin:/bin",
        # Isolated from the host's own Git config: no templates, no hooks.
        "HOME": str(path.parent),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "vibecrafted-test",
        "GIT_AUTHOR_EMAIL": "test@vetcoders.io",
        "GIT_COMMITTER_NAME": "vibecrafted-test",
        "GIT_COMMITTER_EMAIL": "test@vetcoders.io",
    }
    for argv in (
        ["git", "init", "-q", "-b", "main", "."],
        ["git", "commit", "-q", "--allow-empty", "-m", "product entry fixture"],
    ):
        subprocess.run(
            argv, cwd=path, env=env, check=True, capture_output=True, text=True
        )
    return path.resolve()


class DeveloperEntry(NamedTuple):
    """An explicitly developer-mode vc-start harness (never install acceptance)."""

    home: Path
    bin_dir: Path
    capture: Path
    frame_bin: Path
    env: dict[str, str]


def _developer_entry(
    tmp_path: Path, *, resolve_root: Path, resolve_status: int = 0
) -> DeveloperEntry:
    home = (tmp_path / "home").resolve()
    bin_dir = (tmp_path / "bin").resolve()
    capture = (tmp_path / "core-calls").resolve()
    home.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)

    core_cli = _write_fake_bin(
        bin_dir,
        "vibecrafted",
        _core_cli_body(
            capture, resolve_root=resolve_root, resolve_status=resolve_status
        ),
    )
    # Developer mode resolves the frame binary from the explicit override; it
    # is never launched by the probe, but it must exist to pass the guard.
    frame_bin = _write_fake_bin(
        bin_dir, "vc-frame", "#!/bin/sh\nprintf 'FRAME_RAN\\n'\nexit 99\n"
    )
    sockets = home / "frame-sockets"
    sockets.mkdir(parents=True, exist_ok=True)

    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(home),
        "USER": "test",
        # EXPLICIT developer mode against this directly sourced checkout.
        "VIBECRAFTED_PREFER_REPO_VC_FRAME": "1",
        "VIBECRAFTED_VC_FRAME_BIN": str(frame_bin),
        "VIBECRAFTED_PRODUCT_CORE_CLI": str(core_cli),
        "VIBECRAFTED_PRODUCT_ENTRY_PROBE": "1",
        # Keep every session lookup inside this test's own namespace.
        "VC_FRAME_SOCKET_DIR": str(sockets),
        "ZELLIJ_SOCKET_DIR": str(sockets),
    }
    return DeveloperEntry(home, bin_dir, capture, frame_bin, env)


def _run_shell(
    script: str,
    *,
    env: dict[str, str],
    cwd: Path,
    helper: Path = HELPER,
) -> subprocess.CompletedProcess[str]:
    """Run a shell fragment after sourcing the shipped facade.

    The fragment always captures vc-start's status IMMEDIATELY and re-raises
    it as the process status, so no later command can stand in for it.
    """
    return subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            f'source "{helper}" || exit 90\n{script}',
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd),
        check=False,
        timeout=120,
    )


_VC_START_CAPTURE = (
    'vc-start\nstatus=$?\nprintf \'VC_START_STATUS=%s\\n\' "$status"\nexit "$status"\n'
)


def test_vc_start_probe_pins_the_product_config_in_developer_mode(
    tmp_path: Path,
) -> None:
    """Helper-level developer entry: preparation effects, no attach, no create.

    Developer mode is stated in the environment, not implied: this proves the
    shell choke, NOT that an installed product would be admitted.
    """
    workspace = _git_project(tmp_path / "workspace")
    entry = _developer_entry(tmp_path, resolve_root=workspace)

    proc = _run_shell(_VC_START_CAPTURE, env=entry.env, cwd=workspace)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "VC_START_STATUS=0" in proc.stdout
    assert "VIBECRAFTED_PRODUCT_ENTRY=1" in proc.stdout
    assert f"VC_FRAME_CONFIG_DIR={REPO_FRAME_CONFIG}" in proc.stdout
    assert "VC_FRAME_CONFIG_KDL=present" in proc.stdout
    assert "OPERATOR_LAYOUT_PRESENT=1" in proc.stdout
    assert (
        f"OPERATOR_LAYOUT={REPO_FRAME_CONFIG / 'layouts' / 'operator.kdl'}"
        in proc.stdout
    )
    assert "VIBECRAFTED_WORKSPACE_ID=probe-workspace" in proc.stdout
    assert "VIBECRAFTED_WORKSPACE_INSTANCE_ID=probe-instance" in proc.stdout
    assert "VIBECRAFTED_OPERATOR_SESSION=probe-place" in proc.stdout
    # The probe never launches the frame.
    assert "FRAME_RAN" not in proc.stdout


def test_vc_start_failure_is_not_masked_by_a_later_command(tmp_path: Path) -> None:
    """A refused workspace resolve must surface as vc-start's own status.

    The regression this guards: a trailing `printf`/attach in the harness (or
    in a caller) replaced the entry's status with its own success.
    """
    workspace = _git_project(tmp_path / "workspace")
    entry = _developer_entry(tmp_path, resolve_root=workspace, resolve_status=64)

    proc = _run_shell(
        _VC_START_CAPTURE + "printf 'TRAILING_OK\\n'\n",
        env=entry.env,
        cwd=workspace,
    )

    assert proc.returncode == 64, proc.stdout + proc.stderr
    assert "VC_START_STATUS=64" in proc.stdout
    assert "TRAILING_OK" not in proc.stdout
    assert "could not resolve requested workspace root" in proc.stderr


def test_vc_start_foreign_workspace_env_cannot_override_requested_root(
    tmp_path: Path,
) -> None:
    """An inherited foreign identity never redirects the requested root."""
    requested = _git_project(tmp_path / "requested")
    foreign = (tmp_path / "codescribe").resolve()
    foreign.mkdir()
    entry = _developer_entry(tmp_path, resolve_root=requested)
    env = {
        **entry.env,
        "VIBECRAFTED_WORKSPACE_ID": "foreign-workspace",
        "VIBECRAFTED_SESSION_ID": "foreign-session",
        "VIBECRAFTED_WORKSPACE_INSTANCE_ID": "foreign-instance",
        "VIBECRAFTED_OPERATOR_SESSION": "codescribe",
        "VIBECRAFTED_WORKSPACE_ROOT": str(foreign),
    }

    proc = _run_shell(
        "vc-start\n"
        "status=$?\n"
        "printf 'VC_START_STATUS=%s\\n' \"$status\"\n"
        "printf 'FINAL_PWD=%s\\n' \"$PWD\"\n"
        'exit "$status"\n',
        env=env,
        cwd=requested,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "VC_START_STATUS=0" in proc.stdout
    calls = entry.capture.read_text(encoding="utf-8").splitlines()
    assert calls[0] == f"workspace resolve --root {requested} --env"
    assert "VIBECRAFTED_WORKSPACE_ID=probe-workspace" in proc.stdout
    assert "VIBECRAFTED_WORKSPACE_INSTANCE_ID=probe-instance" in proc.stdout
    assert f"FINAL_PWD={requested}" in proc.stdout
    assert str(foreign) not in proc.stdout


def test_vc_start_reuses_the_resolved_tuple_for_frame_attachment(
    tmp_path: Path,
) -> None:
    """The attachment binds the ids the ONE resolve produced — no second walk."""
    workspace = _git_project(tmp_path / "workspace")
    entry = _developer_entry(tmp_path, resolve_root=workspace)

    proc = _run_shell(
        "vc-start\n"
        "status=$?\n"
        "printf 'VC_START_STATUS=%s\\n' \"$status\"\n"
        'if (( status != 0 )); then exit "$status"; fi\n'
        "_vetcoders_record_vc_frame_attachment live probe-place\n"
        "exit $?\n",
        env=entry.env,
        cwd=workspace,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "VC_START_STATUS=0" in proc.stdout
    calls = entry.capture.read_text(encoding="utf-8").splitlines()
    resolves = [call for call in calls if call.startswith("workspace resolve")]
    assert resolves == [f"workspace resolve --root {workspace} --env"]
    attach = next(call for call in calls if call.startswith("workspace session-attach"))
    assert "--workspace-id probe-workspace" in attach
    assert "--session-id probe-session" in attach
    assert "--instance-id probe-instance" in attach


def test_product_entry_prepares_path_then_workspace_then_control_plane_eye(
    tmp_path: Path,
) -> None:
    """Ordering proved by the calls themselves, not by source offsets.

    The sanitized PATH must already be in place when the workspace owner is
    called, and the control-plane eye must come after it.
    """
    workspace = _git_project(tmp_path / "workspace")
    entry = _developer_entry(tmp_path, resolve_root=workspace)
    founder_bin = (tmp_path / "founder-tools").resolve()
    _write_probe_tool(founder_bin, "founder-tool", "founder-tool")
    env = {**entry.env, "PATH": f"{entry.bin_dir}:{founder_bin}:/usr/bin:/bin"}

    proc = _run_shell(_VC_START_CAPTURE, env=env, cwd=workspace)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    calls = entry.capture.read_text(encoding="utf-8").splitlines()
    assert calls.index(f"workspace resolve --root {workspace} --env") < calls.index(
        "server status"
    )
    paths = Path(str(entry.capture) + ".path").read_text(encoding="utf-8").splitlines()
    # The very first core call already sees the founder's own entry.
    assert str(founder_bin) in paths[0]


def test_product_entry_probe_is_stable_across_repeated_runs(tmp_path: Path) -> None:
    """Verification plan: the same preparation twice, byte for byte."""
    workspace = _git_project(tmp_path / "workspace")
    entry = _developer_entry(tmp_path, resolve_root=workspace)

    outputs = []
    for _ in range(2):
        proc = _run_shell(_VC_START_CAPTURE, env=entry.env, cwd=workspace)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        outputs.append(proc.stdout)
    assert outputs[0] == outputs[1]
    assert f"VC_FRAME_CONFIG_DIR={REPO_FRAME_CONFIG}" in outputs[0]


# ---------------------------------------------------------------------------
# The installed path: admission, not a checkout
# ---------------------------------------------------------------------------


def _stage_shell_generation(tmp_path: Path, *, under_releases: bool) -> Path:
    """Copy the shipped shell tree into a physically distinct root.

    The copied facade decides the owner root, so sourcing it drives the
    installed (non-developer) branch of the entry choke.

    That branch performs a real read-only admission: the copied
    ``scripts/vetcoders_install.py`` executes
    ``vibecrafted_core/runtime_paths.py`` from its own file, because the
    installer runs on the host interpreter and must never import the
    package.  The generation therefore carries that module as well.  Without
    it the resolver dies before it can answer at all, and the refusal under
    test degrades from "this generation has no runtime identity" into "the
    resolver is unavailable" -- a different verdict, produced by a gap in
    this staging rather than by the product.
    """
    runtime_home = (tmp_path / "runtime-home").resolve()
    root = (
        runtime_home / "releases" / "9.9.9+gtest"
        if under_releases
        else runtime_home / "checkout"
    )
    core = root / "vibecrafted-core" / "vibecrafted_core"
    core.mkdir(parents=True)
    shutil.copytree(
        REPO / "vibecrafted-core/vibecrafted_core/runtime", core / "runtime"
    )
    for name in ("cli.py", "runtime_paths.py"):
        shutil.copy2(REPO / "vibecrafted-core/vibecrafted_core" / name, core / name)
    (root / "scripts").mkdir(parents=True)
    for name in (
        "vetcoders_install.py",
        "distribution_manifest.py",
        "installer_brand.py",
    ):
        shutil.copy2(REPO / "scripts" / name, root / "scripts" / name)
    _write_fake_bin(
        root / "bin",
        "python3",
        f'#!/bin/sh\nexec {installer.shlex_quote(sys.executable)} "$@"\n',
    )
    return root


def _installed_entry_env(tmp_path: Path, root: Path) -> dict[str, str]:
    home = (tmp_path / "home").resolve()
    sockets = home / "frame-sockets"
    sockets.mkdir(parents=True, exist_ok=True)
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "USER": "test",
        "VIBECRAFTED_PRODUCT_ENTRY_PROBE": "1",
        "VC_FRAME_SOCKET_DIR": str(sockets),
        "ZELLIJ_SOCKET_DIR": str(sockets),
    }


def test_vc_start_refuses_a_checkout_that_is_not_an_installed_generation(
    tmp_path: Path,
) -> None:
    """Without the developer opt-in, a shell outside `releases/` is refused."""
    root = _stage_shell_generation(tmp_path, under_releases=False)
    env = _installed_entry_env(tmp_path, root)
    project = _git_project(tmp_path / "project")

    proc = _run_shell(
        _VC_START_CAPTURE,
        env=env,
        cwd=project,
        helper=root / "vibecrafted-core/vibecrafted_core/runtime/shell/vetcoders.sh",
    )

    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "VC_START_STATUS=2" in proc.stdout
    assert "not an installed generation" in proc.stderr


def test_vc_start_refuses_an_installed_generation_without_runtime_identity(
    tmp_path: Path,
) -> None:
    """Inside `releases/`, the read-only resolver still has to admit it."""
    root = _stage_shell_generation(tmp_path, under_releases=True)
    env = _installed_entry_env(tmp_path, root)
    project = _git_project(tmp_path / "project")

    proc = _run_shell(
        _VC_START_CAPTURE,
        env=env,
        cwd=project,
        helper=root / "vibecrafted-core/vibecrafted_core/runtime/shell/vetcoders.sh",
    )

    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "VC_START_STATUS=2" in proc.stdout
    assert "vc-start: installed runtime" in proc.stderr
    # The refusal must be the identity verdict itself -- the resolver ran and
    # reported "absent" -- not a resolver that never got to answer.
    assert "runtime identity files are absent" in proc.stderr
    assert "explicit install/repair required" in proc.stderr


def test_vc_start_refuses_a_generation_without_its_own_python_or_core(
    tmp_path: Path,
) -> None:
    """The product runs its own interpreter and core, never the host's."""
    root = _stage_shell_generation(tmp_path, under_releases=True)
    (root / "bin" / "python3").unlink()
    env = _installed_entry_env(tmp_path, root)
    project = _git_project(tmp_path / "project")

    proc = _run_shell(
        _VC_START_CAPTURE,
        env=env,
        cwd=project,
        helper=root / "vibecrafted-core/vibecrafted_core/runtime/shell/vetcoders.sh",
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "installed runtime is missing its own interpreter" in proc.stderr
    assert "refusing to substitute a host python3" in proc.stderr
    assert str(root) in proc.stderr


def test_product_entry_path_preparation_never_reintroduces_private_generation(
    tmp_path: Path,
) -> None:
    """Drive the shipped sanitizer that vc-start uses before workspace resolve.

    ``_vetcoders_product_entry_prepare`` exports the PATH produced here, so
    this is the public product-preparation surface.  It used to PREPEND the
    selected generation's own bin, which made a stale private ``aicx``/
    ``loct``/``prview``/``screenscribe`` answer for a public foundation, and
    pushed the Founder's own directories down the list.

    Resolution is proved with ``command -v`` against the produced PATH — not
    by matching substrings.
    """

    home = tmp_path / "home"
    runtime_home = home / ".local" / "share" / "vibecrafted"
    stale_generation = runtime_home / "releases" / "4.3.0+gSTALE" / "bin"
    selected_generation = runtime_home / "releases" / "4.3.0+gSELECTED" / "bin"
    public_bin = home / ".local" / "bin"
    custom_bin = home / "opt" / "founder-tools"

    for name in ("aicx", "loct", "prview", "screenscribe"):
        _write_probe_tool(public_bin, name, f"public-{name}")
        _write_probe_tool(stale_generation, name, f"stale-{name}")
        _write_probe_tool(selected_generation, name, f"selected-{name}")
    # Only the private carrier ships this one: it must stay unresolved so the
    # caller prints canonical install guidance instead of running a copy.
    _write_probe_tool(selected_generation, "vc-private-only", "private-only")
    _write_probe_tool(custom_bin, "founder-tool", "founder-tool")

    inherited = ":".join(
        (
            str(stale_generation),
            str(custom_bin),
            str(public_bin),
            "/usr/bin",
            "/bin",
        )
    )

    script = textwrap.dedent(
        f"""
        set -euo pipefail
        export HOME="{home}"
        export XDG_DATA_HOME="{home / ".local" / "share"}"
        export VIBECRAFTED_RUNTIME_ROOT="{selected_generation.parent}"
        PATH="$(_vetcoders_path_with_bundled_bin_priority "{inherited}")"
        export PATH
        printf 'PATH=%s\\n' "$PATH"
        for tool in aicx loct prview screenscribe founder-tool; do
          printf '%s=%s\\n' "$tool" "$(command -v "$tool" || printf 'MISSING')"
        done
        printf 'private-only=%s\\n' "$(command -v vc-private-only || printf 'MISSING')"
        """
    )
    proc = subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            f'source "{HELPER}" >/dev/null 2>&1\n{script}',
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO),
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    fields = dict(
        line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line
    )
    entries = fields["PATH"].split(":")

    # Neither the inherited stale generation nor the selected one may appear.
    assert str(stale_generation) not in entries
    assert str(selected_generation) not in entries

    # The Founder's own entries keep their identity and relative order.
    assert entries[:4] == [str(custom_bin), str(public_bin), "/usr/bin", "/bin"]

    # Public foundations resolve to the operator's copies.
    for name in ("aicx", "loct", "prview", "screenscribe"):
        assert fields[name] == str(public_bin / name)
    assert fields["founder-tool"] == str(custom_bin / "founder-tool")

    # A foundation that exists only inside the carrier stays missing.
    assert fields["private-only"] == "MISSING"


def test_shipped_deck_routes_workspace_resolution_to_core(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = tmp_path / "workspace"
    home.mkdir()
    root.mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "VIBECRAFTED_HOME": str(home / ".vibecrafted"),
        "VIBECRAFTED_PYTHON": sys.executable,
    }
    proc = subprocess.run(
        [
            str(REPO / "scripts/vibecrafted"),
            "workspace",
            "resolve",
            "--root",
            str(root),
            "--env",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert "VIBECRAFTED_WORKSPACE_ID=" in proc.stdout
    # Operator session is the resolved PLACE (root basename / catalog label),
    # not the catalog-fallback workspace-{8hex} token.
    assert "VIBECRAFTED_OPERATOR_SESSION=workspace\n" in proc.stdout


def test_product_entry_reconciles_the_one_macos_server_service_owner(
    tmp_path: Path,
) -> None:
    """Darwin owns a persistent LaunchAgent: reconcile it, never start a rival."""
    bin_dir = (tmp_path / "bin").resolve()
    capture = (tmp_path / "server-calls").resolve()
    bin_dir.mkdir(parents=True)
    _write_fake_bin(bin_dir, "uname", "#!/bin/sh\necho Darwin\n")
    _write_fake_bin(
        bin_dir,
        "vibecrafted",
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            printf '%s\\n' "$*" >> {installer.shlex_quote(str(capture))}
            [[ "$*" == "server status" ]] && exit 1
            [[ "$*" == "server service reconcile" ]] && exit 0
            exit 1
            """
        ),
    )

    proc = subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            f'source "{DASHBOARD}"; _vetcoders_control_plane_eye_prepare',
        ],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "VIBECRAFTED_PRODUCT_CORE_CLI": str(bin_dir / "vibecrafted"),
        },
        check=False,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert capture.read_text(encoding="utf-8").splitlines() == [
        "server status",
        "server service reconcile",
    ]


def test_product_entry_does_not_invent_a_non_macos_service_owner(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    capture = tmp_path / "server-calls"
    bin_dir.mkdir()
    _write_fake_bin(bin_dir, "uname", "#!/usr/bin/env bash\necho Linux\n")
    _write_fake_bin(
        bin_dir,
        "vibecrafted",
        f'#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >> "{capture}"\nexit 1\n',
    )

    proc = subprocess.run(
        [
            "bash",
            "-c",
            f'source "{DASHBOARD}"; _vetcoders_control_plane_eye_prepare',
        ],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "VIBECRAFTED_PRODUCT_CORE_CLI": str(bin_dir / "vibecrafted"),
        },
        check=False,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert capture.read_text(encoding="utf-8").splitlines() == ["server status"]


def test_product_entry_keeps_a_healthy_macos_server_untouched(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    capture = tmp_path / "server-calls"
    bin_dir.mkdir()
    _write_fake_bin(bin_dir, "uname", "#!/usr/bin/env bash\necho Darwin\n")
    _write_fake_bin(
        bin_dir,
        "vibecrafted",
        f'#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >> "{capture}"\nexit 0\n',
    )

    proc = subprocess.run(
        [
            "bash",
            "-c",
            f'source "{DASHBOARD}"; _vetcoders_control_plane_eye_prepare',
        ],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "VIBECRAFTED_PRODUCT_CORE_CLI": str(bin_dir / "vibecrafted"),
        },
        check=False,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert capture.read_text(encoding="utf-8").splitlines() == ["server status"]


# ---------------------------------------------------------------------------
# Retiring a launcher a previous generation published — receipt-gated
# ---------------------------------------------------------------------------


class Retirement(NamedTuple):
    runtime_home: Path
    launcher_home: Path
    generation_bin: Path
    backup_root: Path


def _stage_retirement(tmp_path: Path) -> Retirement:
    runtime_home = (tmp_path / "runtime-home").resolve()
    launcher_home = (tmp_path / "launcher-bin").resolve()
    generation_bin = (tmp_path / "generation-bin").resolve()
    backup_root = runtime_home / ".installer-backups"
    for directory in (runtime_home, launcher_home, generation_bin, backup_root):
        directory.mkdir(parents=True, exist_ok=True)
    # The current generation publishes exactly one launcher name.
    _write_fake_bin(generation_bin, "vibecrafted", "#!/bin/sh\nexit 0\n")
    return Retirement(runtime_home, launcher_home, generation_bin, backup_root)


def test_retiring_a_receipted_launcher_restores_its_collision_backup(
    tmp_path: Path,
) -> None:
    """The pre-Vibecrafted owner of a public name resurfaces on retirement."""
    stage = _stage_retirement(tmp_path)
    stale = _write_fake_bin(
        stage.launcher_home, "vc-retired-probe", "#!/bin/sh\nexit 7\n"
    )
    digest = installer._sha256_path(stale)
    backup = stage.backup_root / "vc-retired-probe"
    backup.write_text(
        "#!/bin/sh\nprintf 'pre-vibecrafted owner\\n'\n", encoding="utf-8"
    )
    backup.chmod(0o755)
    receipt: dict[str, object] = {
        "owned_files": {str(stale): digest},
        "backups": {str(stale): str(backup)},
    }

    retired = installer._reclaim_foreign_launcher_names(
        stage.generation_bin,
        stage.launcher_home,
        runtime_home=stage.runtime_home,
        receipt=receipt,
        previous={"owned_files": {str(stale): digest}},
    )

    assert retired == [stale]
    assert stale.exists()
    assert "pre-vibecrafted owner" in stale.read_text(encoding="utf-8")
    assert str(stale) not in receipt["owned_files"]
    assert str(stale) not in receipt["backups"]
    persisted = json.loads(
        installer._runtime_receipt_path(stage.runtime_home).read_text(encoding="utf-8")
    )
    assert str(stale) not in persisted["owned_files"]


def test_retirement_leaves_a_drifted_foreign_bare_name_untouched(
    tmp_path: Path,
) -> None:
    """The user's own install of a bare public name always wins."""
    stage = _stage_retirement(tmp_path)
    stale = _write_fake_bin(stage.launcher_home, "founder-tool", "#!/bin/sh\nexit 0\n")
    previous_digest = installer._sha256_path(stale)
    stale.write_text("#!/bin/sh\nprintf 'user copy\\n'\n", encoding="utf-8")
    receipt: dict[str, object] = {"owned_files": {str(stale): previous_digest}}

    retired = installer._reclaim_foreign_launcher_names(
        stage.generation_bin,
        stage.launcher_home,
        runtime_home=stage.runtime_home,
        receipt=receipt,
        previous={"owned_files": {str(stale): previous_digest}},
    )

    assert retired == []
    assert "user copy" in stale.read_text(encoding="utf-8")
    assert str(stale) in receipt["owned_files"]


def test_retirement_preserves_drifted_namespace_bytes_before_removing(
    tmp_path: Path,
) -> None:
    """Inside our own namespace a drifted wrapper is ours — but never lost."""
    stage = _stage_retirement(tmp_path)
    stale = _write_fake_bin(
        stage.launcher_home, "vibecrafted-legacy-probe", "#!/bin/sh\nexit 0\n"
    )
    previous_digest = installer._sha256_path(stale)
    stale.write_text("#!/bin/sh\nprintf 'drifted wrapper\\n'\n", encoding="utf-8")
    receipt: dict[str, object] = {"owned_files": {str(stale): previous_digest}}

    retired = installer._reclaim_foreign_launcher_names(
        stage.generation_bin,
        stage.launcher_home,
        runtime_home=stage.runtime_home,
        receipt=receipt,
        previous={"owned_files": {str(stale): previous_digest}},
    )

    assert retired == [stale]
    assert not stale.exists()
    preserved = [
        path
        for path in stage.backup_root.rglob("*")
        if path.is_file() and "drifted wrapper" in path.read_text(encoding="utf-8")
    ]
    assert preserved, "drifted namespace bytes must be preserved before removal"


def test_a_launcher_the_generation_still_publishes_is_not_retired(
    tmp_path: Path,
) -> None:
    """Retirement is bounded to names this generation no longer publishes."""
    stage = _stage_retirement(tmp_path)
    stale = _write_fake_bin(stage.launcher_home, "vibecrafted", "#!/bin/sh\nexit 0\n")
    digest = installer._sha256_path(stale)
    receipt: dict[str, object] = {"owned_files": {str(stale): digest}}

    retired = installer._reclaim_foreign_launcher_names(
        stage.generation_bin,
        stage.launcher_home,
        runtime_home=stage.runtime_home,
        receipt=receipt,
        previous={"owned_files": {str(stale): digest}},
    )

    assert retired == []
    assert stale.exists()


def test_foundations_does_not_own_launcher_retirement() -> None:
    """Negative ownership claim: it can only be stated, not executed.

    Foundations installs and validates; retiring a name a previous generation
    published belongs to the receipt-gated installer path exercised above.
    """
    foundations = FOUNDATIONS.read_text(encoding="utf-8")
    assert "vc-frame.real" not in foundations
    assert "_reclaim_foreign_launcher_names" not in foundations
    assert "_reclaim_foreign_launcher_names" in (
        REPO / "scripts/vetcoders_install.py"
    ).read_text(encoding="utf-8")
