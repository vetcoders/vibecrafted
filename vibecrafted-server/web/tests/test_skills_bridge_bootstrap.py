"""Isolated selected-generation bootstrap contract; never touches a real install."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

BOOTSTRAP_PATH = Path(__file__).parents[1] / "src/skills_bridge.py"


@pytest.fixture
def generation(tmp_path: Path) -> tuple[dict[str, str], Path]:
    runtime = tmp_path / "runtime"
    root = runtime / "releases" / "test-generation"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    (root / "bin").mkdir()
    (root / "bin/python3").symlink_to(sys.executable)
    installer = """
import hashlib, json, os
from pathlib import Path
class Failure(RuntimeError):
    code = "conflict"
def _runtime_install_paths():
    return {"runtime_home": Path(os.environ["VIBECRAFTED_RUNTIME_HOME"])}
def _runtime_receipt_path(home): return home / "install-receipt.json"
def _load_runtime_install_receipt(path): return json.loads(path.read_text())
def _runtime_rescue_pending_publication_reason(receipt): return False
def _runtime_generation_payload_errors(root): return []
def _load_local_skill_owner():
    class Owner:
        @staticmethod
        def read_skill(name):
            text = "owner content"
            return {"ok": True, "name": name, "text": text, "revision": hashlib.sha256(text.encode()).hexdigest(), "origin": "bundled", "activation": "unknown"}
    return Owner()
def publish_local_skill(name, text, revision):
    print("installer output must not enter JSON")
    if revision == "stale": raise Failure()
    return {"ok": True, "name": name, "revision": hashlib.sha256(text.encode()).hexdigest(), "origin": "local", "activation": "partial", "views": {"agents": "published", "claude": "conflict", "codex": "missing_root"}}
"""
    for name, content in (
        ("vetcoders_install.py", installer),
        ("distribution_manifest.py", ""),
        ("installer_brand.py", ""),
    ):
        (scripts / name).write_text(content)
    hashes = {
        f"scripts/{name}": hashlib.sha256((scripts / name).read_bytes()).hexdigest()
        for name in (
            "vetcoders_install.py",
            "distribution_manifest.py",
            "installer_brand.py",
        )
    }
    (root / "runtime-manifest.json").write_text(
        json.dumps(
            {
                "schema": "vibecrafted.runtime-generation.v2",
                "version": root.name,
                "hashes": hashes,
            }
        )
    )
    current = runtime / "tools/vibecrafted-current"
    current.parent.mkdir()
    current.symlink_to(root)
    (runtime / "active.json").write_text(
        json.dumps(
            {
                "schema": "vibecrafted.active-runtime.v1",
                "version": root.name,
                "runtime_root": str(root),
            }
        )
    )
    (runtime / "install-receipt.json").write_text(
        json.dumps(
            {
                "roots": {"runtime_home": str(runtime)},
                "version": root.name,
                "owned_symlinks": {str(current): str(root)},
            }
        )
    )
    env = {
        **os.environ,
        "VIBECRAFTED_RUNTIME_ROOT": str(root),
        "VIBECRAFTED_RUNTIME_HOME": str(runtime),
    }
    return env, root


def call(env: dict[str, str], request: dict[str, str]) -> dict:
    child = subprocess.Popen(
        [sys.executable, "-I", "-B", str(BOOTSTRAP_PATH)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        output, errors = child.communicate(json.dumps(request), timeout=10)
    except subprocess.TimeoutExpired:
        child.kill()
        child.communicate()
        raise
    assert child.returncode == 0
    assert errors == ""
    return json.loads(output)


def test_selected_generation_read_publish_conflict_and_partial(
    generation: tuple[dict[str, str], Path],
) -> None:
    env, _ = generation
    read = call(env, {"op": "read", "name": "vc-alpha"})
    assert read["text"] == "owner content" and read["writable"] is True
    saved = call(
        env,
        {"op": "save", "name": "vc-alpha", "text": "new", "revision": read["revision"]},
    )
    assert saved["activation"] == "partial" and saved["views"]["claude"] == "conflict"
    assert saved["revision"] == hashlib.sha256(b"new").hexdigest()
    assert call(
        env, {"op": "save", "name": "vc-alpha", "text": "new", "revision": "stale"}
    ) == {"ok": False, "code": "conflict"}


def test_drifted_installer_and_wrong_selector_fail_closed(
    generation: tuple[dict[str, str], Path],
) -> None:
    env, root = generation
    path = root / "scripts/installer_brand.py"
    path.write_text("changed after manifest")
    assert call(env, {"op": "read", "name": "vc-alpha"}) == {
        "ok": False,
        "code": "unavailable",
    }
    env = {**env, "VIBECRAFTED_RUNTIME_ROOT": str(root.parent / "wrong")}
    assert call(
        env, {"op": "save", "name": "vc-alpha", "text": "private", "revision": "0" * 64}
    ) == {"ok": False, "code": "unavailable"}
