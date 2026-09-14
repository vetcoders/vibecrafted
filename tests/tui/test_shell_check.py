from pathlib import Path

from scripts import check_shell


def test_tracked_shell_files_cover_repo_level_shell_surfaces() -> None:
    tracked = {
        path.relative_to(check_shell.REPO_ROOT).as_posix()
        for path in check_shell.tracked_shell_files()
    }

    assert "install.sh" in tracked
    assert "scripts/hooks/commit-msg" in tracked
    assert "scripts/hooks/pre-commit" in tracked
    assert "scripts/hooks/pre-push" in tracked
    assert "scripts/vibecrafted" in tracked
    assert "tests/portable/run.sh" in tracked
    assert "tools/hooks/load-project-context.sh" in tracked


def test_shell_for_path_uses_suffix_and_shebang(tmp_path: Path) -> None:
    zsh_file = tmp_path / "session"
    zsh_file.write_text("#!/usr/bin/env zsh\nprint ok\n", encoding="utf-8")
    bash_file = tmp_path / "bootstrap"
    bash_file.write_text("#!/usr/bin/env bash\nprintf 'hi\\n'\n", encoding="utf-8")
    suffix_only_bash = tmp_path / "bootstrap.sh"
    suffix_only_bash.write_text("printf 'hi\\n'\n", encoding="utf-8")

    assert check_shell.shell_for_path(zsh_file) == "zsh"
    assert check_shell.shell_for_path(bash_file) == "bash"
    assert check_shell.shell_for_path(suffix_only_bash) == "bash"


POLYGLOT = (
    '#!/bin/sh\n"""":\nexec "$(dirname -- "$0")/python3" "$0" "$@"\n":"""\n'
    "import sys\nraise SystemExit(main())\n"
)


def test_polyglot_launcher_is_checked_by_its_sh_prelude_only(tmp_path: Path) -> None:
    launcher = tmp_path / "vc-demo"
    launcher.write_text(POLYGLOT, encoding="utf-8")
    plain = tmp_path / "plain.sh"
    plain.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    assert check_shell.shell_prelude(launcher) == POLYGLOT.split('":"""\n', 1)[0]
    assert check_shell.shell_prelude(plain) is None

    workdir = tmp_path / "work"
    workdir.mkdir()
    checkable = check_shell.materialize_preludes([launcher, plain], workdir)
    assert checkable[1] == plain
    assert checkable[0].name == "vc-demo"
    assert "raise SystemExit" not in checkable[0].read_text(encoding="utf-8")
    # The Python body would be a shell syntax error; the prelude alone is clean.
    assert check_shell.run_syntax_fallback(checkable) == 0

    broken = tmp_path / "vc-broken"
    broken.write_text(POLYGLOT.replace("exec ", "if then exec "), encoding="utf-8")
    broken_work = tmp_path / "broken-work"
    broken_work.mkdir()
    assert (
        check_shell.run_syntax_fallback(
            check_shell.materialize_preludes([broken], broken_work)
        )
        == 1
    )


def test_build_shellcheck_command_keeps_repo_exclude_list(tmp_path: Path) -> None:
    sample = tmp_path / "sample.sh"
    sample.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")

    command = check_shell.build_shellcheck_command([sample])

    assert command[:3] == [
        "shellcheck",
        "-e",
        ",".join(check_shell.SHELLCHECK_EXCLUDES),
    ]
    assert command[-1] == str(sample)
