from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DISPATCH_SH = (
    REPO_ROOT / "vibecrafted-core/vibecrafted_core/runtime/shell/lib/dispatch.sh"
)


def test_repo_full_largest_tracked_files_include_size_and_name(tmp_path: Path) -> None:
    repo = tmp_path / "fixture-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    tracked = repo / "known-size.bin"
    tracked.write_bytes(b"x" * 2048)
    subprocess.run(["git", "-C", str(repo), "add", tracked.name], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )

    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; cd "$2"; repo-full',
            "repo-full-test",
            str(DISPATCH_SH),
            str(repo),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    largest = result.stdout.split(
        "==================== TOP 10 LARGEST TRACKED FILES ===================="
    )[1].split("==================== GIT CONFIG ====================")[0]

    assert "2.0 KB" in largest
    assert tracked.name in largest


def test_scaffold_doctor_prefers_runtime_pack_binary_outside_monorepo(
    tmp_path: Path,
) -> None:
    pack = tmp_path / "runtime-pack"
    pack_bin = pack / "bin"
    pack_bin.mkdir(parents=True)
    (pack / "runtime-manifest.json").write_text("{}\n", encoding="utf-8")
    deck = pack_bin / "vibecrafted"
    shutil.copy2(REPO_ROOT / "scripts/vibecrafted", deck)
    helper_source = REPO_ROOT / "vibecrafted-core/vibecrafted_core/runtime"
    helper_target = pack / "vibecrafted-core/vibecrafted_core/runtime"
    shutil.copytree(helper_source, helper_target)
    scaffold_doctor = pack_bin / "scaffold-doctor"
    scaffold_doctor.write_text(
        "#!/bin/sh\nprintf 'runtime-pack-scaffold-doctor\\n'\n",
        encoding="utf-8",
    )
    scaffold_doctor.chmod(0o755)
    outside = tmp_path / "outside"
    outside.mkdir()
    plan = tmp_path / "plan"
    plan.mkdir()
    env = os.environ.copy()
    env.pop("VIBECRAFTED_PREFER_REPO_SPAWN", None)
    env["VIBECRAFTED_ROOT"] = str(pack)

    result = subprocess.run(
        [str(deck), "scaffold-doctor", "--plan", str(plan)],
        cwd=outside,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "runtime-pack-scaffold-doctor" in result.stdout
