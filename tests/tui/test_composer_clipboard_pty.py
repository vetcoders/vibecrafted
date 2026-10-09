"""Exercise the shipped editor scripts, without touching a live pane/clipboard."""

from __future__ import annotations

import base64
import fcntl
import json
import os
import re
import select
import shutil
import signal
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path

import pytest

CONFIG = (
    Path(__file__).resolve().parents[2]
    / "vibecrafted-core/vibecrafted_core/config/vc-frame"
)
EDITORS = [name for name in ("vim", "nvim") if shutil.which(name)]
SCRIPTS = ["vc-composer.sh", "scrollback-select.sh"]
PARAGRAPHS = [
    "Zażółć gęślą jaźń — " + "pełny tekst Ω 漢字 bez obcięcia; " * 9,
    "Drugi akapit: " + "źródłem są bajty bufora, nie komórki ekranu. " * 7,
    "Koniec 👩‍💻 — ostatnia linia.",
]
DRAFT = ("\n".join(PARAGRAPHS) + "\n").encode()


class EditorPTY:
    def __init__(
        self,
        root: Path,
        editor: str,
        script: str,
        clipboard: str = "pbcopy",
        remote: str = "",
        draft: bytes = DRAFT,
        panes: list[dict] | None = None,
        current_pane: str = "9",
    ) -> None:
        self.root = root
        self.output = bytearray()
        self.barriers = 0
        self.script = script
        self.draft = draft
        root.mkdir(parents=True, exist_ok=True)
        config = root / "config with ' quote"
        config.mkdir()
        for source in CONFIG.iterdir():
            if source.is_file():
                shutil.copy2(source, config / source.name)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        (root / "seed").write_bytes(draft)
        (root / "panes.json").write_text(
            json.dumps(panes if panes is not None else [{"id": 1, "is_focused": True}])
        )
        (root / "tmp").mkdir()
        (root / "home").mkdir()
        self.clipboard = root / "clipboard"
        self.clipboard.write_bytes(b"untouched")
        self.executable(
            config / "paste-stack.sh",
            "import os, pathlib, sys\n"
            "r = pathlib.Path(os.environ['PTY_ROOT'])\n"
            "if sys.argv[1] == 'top': pathlib.Path(sys.argv[2]).write_bytes((r/'seed').read_bytes())\n"
            "if sys.argv[1] == 'push': (r/'roundtrip').write_bytes(pathlib.Path(sys.argv[2]).read_bytes())\n",
        )
        for name in ("pbcopy", "wl-copy", "xclip", "xsel"):
            self.executable(
                bin_dir / name,
                "import os, pathlib, sys\n"
                f"if {name!r} != os.environ['PTY_CLIPBOARD']: sys.exit(1)\n"
                "pathlib.Path(os.environ['PTY_ROOT'], 'clipboard').write_bytes(sys.stdin.buffer.read())\n",
            )
        self.executable(
            bin_dir / "vc-frame",
            "import json, os, pathlib, sys\n"
            "r = pathlib.Path(os.environ['PTY_ROOT'])\n"
            "a = sys.argv[1:]\n"
            "with (r/'frame-calls').open('a') as f: f.write(json.dumps(a)+'\\n')\n"
            "if 'list-panes' in a: print((r/'panes.json').read_text())\n"
            "if 'dump-screen' in a: pathlib.Path(a[a.index('--path')+1]).write_bytes((r/'seed').read_bytes())\n"
            "if 'write-chars' in a: (r/'sent').write_bytes(a[-1].encode())\n",
        )
        real_editor = shutil.which(editor)
        assert real_editor
        self.executable(
            bin_dir / editor,
            "import os, pathlib, sys\n"
            "pathlib.Path(os.environ['PTY_ROOT'], 'editor-pid').write_text(str(os.getpid()))\n"
            f"os.execv({real_editor!r}, [{real_editor!r}, '-i', 'NONE', '-n', *sys.argv[1:]])\n",
        )
        env = {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(root / "home"),
            "XDG_CONFIG_HOME": str(root / "home/.config"),
            "VIBECRAFTED_HOME": str(root / "runtime"),
            "TMPDIR": str(root / "tmp"),
            "TERM": "xterm-256color",
            "LANG": "en_US.UTF-8",
            "EDITOR": str(bin_dir / editor),
            "VC_COMPOSER_CARET": "0",
            "VC_FRAME_PANE_ID": current_pane,
            "PTY_ROOT": str(root),
            "PTY_CLIPBOARD": clipboard,
        }
        if remote:
            env[remote] = "synthetic-ssh"
        # The viewer prefers nvim; force the requested real editor in this
        # isolated config so both generated-profile launch paths get exercised.
        if script == "scrollback-select.sh" and editor == "vim":
            self.executable(bin_dir / "nvim", "import sys\nsys.exit(127)\n")
            viewer = config / script
            viewer.write_text(
                viewer.read_text().replace('editor_bin="nvim"', 'editor_bin="vim"')
            )
        self.master, slave = os.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 18, 80, 0, 0))
        self.process = subprocess.Popen(
            ["/bin/bash", str(config / script)],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=env,
            cwd=root,
            start_new_session=True,
        )
        os.close(slave)
        try:
            self.wait(lambda: (root / "editor-pid").exists())
            self.wait(lambda: b"= help" in self.output or b"q quit" in self.output)
            self.sync()
        except BaseException:
            # Dismiss a Vim startup-error prompt, then close the test editor.
            self.send(b"\r")
            self.close()
            raise

    @staticmethod
    def executable(path: Path, body: str) -> None:
        path.write_text(f"#!{sys.executable}\n{body}")
        path.chmod(0o755)

    def drain(self) -> None:
        if select.select([self.master], [], [], 0.02)[0]:
            try:
                self.output.extend(os.read(self.master, 65536))
            except OSError:
                pass

    def wait(self, condition) -> None:
        deadline = time.monotonic() + 8
        while not condition():
            self.drain()
            assert self.process.poll() is None, bytes(self.output[-2000:])
            assert time.monotonic() < deadline, bytes(self.output[-2000:])

    def send(self, keys: bytes) -> None:
        os.write(self.master, keys)

    def sync(self) -> list[int]:
        self.barriers += 1
        marker = self.root / f"barrier-{self.barriers}"
        escaped = str(marker).replace("'", "''")
        self.send(
            f"\x1b:call writefile([string(&columns), string(&lines)], '{escaped}')\r".encode()
        )
        self.wait(marker.exists)
        self.drain()
        return [int(x) for x in marker.read_text().splitlines()]

    def resize(self, columns: int) -> None:
        fcntl.ioctl(
            self.master, termios.TIOCSWINSZ, struct.pack("HHHH", 18, columns, 0, 0)
        )
        os.killpg(self.process.pid, signal.SIGWINCH)
        assert self.sync() == [columns, 18]

    def yank(self, keys: bytes) -> bytes:
        start = len(self.output)
        self.send(keys)
        self.sync()
        sequences = re.findall(
            rb"\x1b\]52;c;([A-Za-z0-9+/=]*)\x07", self.output[start:]
        )
        assert sequences, bytes(self.output[start:])
        os.kill(int((self.root / "editor-pid").read_text()), 0)
        assert self.process.poll() is None
        payload = base64.b64decode(sequences[-1])
        pattern = (
            "vc-composer-yank.*"
            if self.script == "vc-composer.sh"
            else "vc-scroll-yank.*"
        )
        assert next((self.root / "tmp").glob(pattern)).read_bytes() == payload
        return payload

    def finish(self) -> None:
        self.send(b"\x1b:wq\r" if self.script == "vc-composer.sh" else b"\x1b:q!\r")
        deadline = time.monotonic() + 8
        while self.process.poll() is None and time.monotonic() < deadline:
            self.drain()
        assert self.process.wait(timeout=1) == 0, bytes(self.output[-2000:])
        if self.script == "vc-composer.sh":
            assert (self.root / "roundtrip").read_bytes() == self.draft
            # Shell command substitution strips trailing newlines on handback.
            assert (self.root / "sent").read_bytes() == self.draft.rstrip(b"\n")
            calls = [
                json.loads(line)
                for line in (self.root / "frame-calls").read_text().splitlines()
            ]
            assert not any("dump-screen" in call for call in calls)

    def close(self) -> None:
        if self.process.poll() is None:
            self.send(b"\x1b:q!\r")
            deadline = time.monotonic() + 5
            while self.process.poll() is None and time.monotonic() < deadline:
                self.drain()
            self.process.wait(timeout=1)
        os.close(self.master)


@pytest.fixture
def editor_pty(tmp_path: Path):
    instances = []

    def create(editor, script, **kwargs):
        instance = EditorPTY(tmp_path / str(len(instances)), editor, script, **kwargs)
        instances.append(instance)
        return instance

    yield create
    for instance in instances:
        instance.close()


@pytest.mark.parametrize("editor", EDITORS)
@pytest.mark.parametrize("script", SCRIPTS)
def test_resize_preserves_buffer_yanks_and_draft(editor_pty, editor, script):
    pane = editor_pty(editor, script)
    for width in (24, 100, 40):
        pane.resize(width)
        assert pane.yank(b"gg0yy") == (PARAGRAPHS[0] + "\n").encode()
        assert pane.yank(b"gg0v$hy") == PARAGRAPHS[0].encode()
        assert pane.yank(b"gg0vj$hy") == "\n".join(PARAGRAPHS[:2]).encode()
        # In Vim, visual $ includes the line break; $h excludes it.
        assert pane.yank(b"gg0v$y") == (PARAGRAPHS[0] + "\n").encode()
        assert pane.yank(b"gg0vj$y") == ("\n".join(PARAGRAPHS[:2]) + "\n").encode()
        assert pane.yank(b"ggVGy") == DRAFT
    pane.finish()


@pytest.mark.parametrize("editor", EDITORS)
@pytest.mark.parametrize("script", SCRIPTS)
@pytest.mark.parametrize("clipboard", ["pbcopy", "wl-copy", "xclip", "xsel"])
def test_keyboard_yank_updates_clipboard_before_exit(
    editor_pty, editor, script, clipboard
):
    pane = editor_pty(editor, script, clipboard=clipboard)
    for width, keys, expected in (
        (24, b"gg0yy", (PARAGRAPHS[0] + "\n").encode()),
        (100, b"gg0v$hy", PARAGRAPHS[0].encode()),
        (40, b"ggVGy", DRAFT),
    ):
        pane.resize(width)
        assert pane.yank(keys) == expected
        # We capture but never interpret OSC52. No outer-terminal clipboard
        # support exists in this PTY: delivery must happen through the stub.
        assert pane.clipboard.read_bytes() == expected
    pane.finish()


@pytest.mark.parametrize("editor", EDITORS)
@pytest.mark.parametrize("script", SCRIPTS)
@pytest.mark.parametrize("remote", ["SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"])
def test_remote_yank_keeps_host_osc52_and_leaves_guest_clipboard(
    editor_pty, editor, script, remote
):
    pane = editor_pty(editor, script, remote=remote)
    pane.resize(24)
    assert pane.yank(b"ggVGy") == DRAFT
    assert pane.clipboard.read_bytes() == b"untouched"
    pane.finish()
    assert pane.clipboard.read_bytes() == b"untouched"


@pytest.mark.parametrize("editor", EDITORS)
@pytest.mark.parametrize("script", SCRIPTS)
def test_large_yank_reaches_local_clipboard_while_editor_open(
    editor_pty, editor, script
):
    draft = ("Zażółć Ω " * 12000 + "\n").encode()
    pane = editor_pty(editor, script, draft=draft)
    pane.resize(24)
    pane.send(b"ggyy")
    pane.sync()
    assert pane.process.poll() is None
    assert pane.clipboard.read_bytes() == draft
    assert b"\x1b]52;c;" not in pane.output
    pane.finish()


@pytest.mark.parametrize("editor", EDITORS)
@pytest.mark.parametrize("script", SCRIPTS)
def test_no_local_clipboard_provider_still_emits_host_payload(
    editor_pty, editor, script
):
    pane = editor_pty(editor, script, clipboard="none")
    assert pane.yank(b"ggVGy") == DRAFT
    assert pane.clipboard.read_bytes() == b"untouched"
    pane.finish()
    assert pane.clipboard.read_bytes() == b"untouched"


@pytest.mark.parametrize("editor", EDITORS)
@pytest.mark.parametrize(
    "panes, expected",
    [
        (
            [
                {"id": 1, "is_plugin": False, "is_focused": False},
                {"id": 9, "is_plugin": False, "is_focused": True, "is_floating": True},
                {"id": 2, "is_plugin": False, "is_focused": True},
            ],
            "2",
        ),
        (
            [
                {"id": 9, "is_plugin": False, "is_focused": True, "is_floating": True},
                {"id": 7, "is_plugin": True, "is_focused": False},
                {"id": 2, "is_plugin": False, "is_focused": False},
            ],
            "2",
        ),
        (
            [
                {"id": 9, "is_plugin": False, "is_focused": True, "is_floating": True},
                {"id": 7, "is_plugin": True, "is_focused": True},
                {"id": 2, "is_plugin": False, "is_focused": False},
            ],
            "plugin_7",
        ),
        ([{"id": 0, "is_plugin": False, "is_focused": True}], "0"),
        ([{"id": 9, "is_plugin": False, "is_focused": True}], None),
        ([], None),
    ],
    ids=[
        "focused-underlying",
        "first-terminal-fallback",
        "plugin-prefix",
        "zero-id",
        "viewer-only",
        "empty",
    ],
)
def test_scrollback_selector_targets_other_pane(editor_pty, editor, panes, expected):
    # list-panes uses integer ids plus is_plugin (as the existing physical
    # Frame fixtures show); CLI actions accept a bare terminal id/plugin_N.
    pane = editor_pty(editor, "scrollback-select.sh", panes=panes)
    assert b"SyntaxError" not in pane.output
    calls = [
        json.loads(line)
        for line in (pane.root / "frame-calls").read_text().splitlines()
    ]
    dumps = [call for call in calls if "dump-screen" in call]
    assert len(dumps) == 1
    if expected is None:
        assert "--pane-id" not in dumps[0]
    else:
        assert dumps[0][dumps[0].index("--pane-id") + 1] == expected
    pane.finish()
