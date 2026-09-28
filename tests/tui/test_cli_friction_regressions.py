from __future__ import annotations

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
