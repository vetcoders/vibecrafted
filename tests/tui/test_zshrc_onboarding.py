"""W1-B: explicit PATH-only host zshrc onboarding."""

from __future__ import annotations

from pathlib import Path

from vibecrafted_core.vc_frame_delivery import ensure_zshrc, zshrc_template_text


def test_fresh_home_creates_zshrc(tmp_path: Path) -> None:
    home = tmp_path / "h"
    home.mkdir()
    result = ensure_zshrc(home)
    assert result["action"] == "create"
    zshrc = home / ".zshrc"
    assert zshrc.is_file()
    text = zshrc.read_text(encoding="utf-8")
    assert ".local/bin" in text
    assert "vc-skills" not in text
    assert "VETCODERS_CONFIG_DIR" not in text
    assert "starship init" not in text


def test_existing_zshrc_gets_fenced_append_idempotent(tmp_path: Path) -> None:
    home = tmp_path / "h"
    home.mkdir()
    zshrc = home / ".zshrc"
    original = "# operator content\nexport FOO=1\n"
    zshrc.write_text(original, encoding="utf-8")
    r1 = ensure_zshrc(home)
    assert r1["action"] == "append_fence"
    mid = zshrc.read_text(encoding="utf-8")
    assert mid.startswith("# operator content")
    assert ">>> vibecrafted >>>" in mid
    assert "vc-skills" not in mid
    assert 'export PATH="$HOME/.local/bin:$PATH"' in mid
    r2 = ensure_zshrc(home)
    assert r2["action"] == "already_present"
    assert zshrc.read_text(encoding="utf-8") == mid


def test_template_nonempty() -> None:
    assert "PATH" in zshrc_template_text()
    assert "vc-skills" not in zshrc_template_text()
    assert len(zshrc_template_text().strip("\n").splitlines()) <= 3


def test_fenced_onboarding_is_at_most_three_lines(tmp_path: Path) -> None:
    home = tmp_path / "h"
    home.mkdir()
    zshrc = home / ".zshrc"
    zshrc.write_text("# operator content\n", encoding="utf-8")
    ensure_zshrc(home)
    text = zshrc.read_text(encoding="utf-8")
    start = text.index("# >>> vibecrafted >>>")
    end = text.index("# <<< vibecrafted <<<") + len("# <<< vibecrafted <<<")
    assert len(text[start:end].splitlines()) <= 3
    assert "starship init" not in text
    assert "source " not in text[start:end]
