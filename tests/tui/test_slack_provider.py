from __future__ import annotations

import json
from pathlib import Path

from scripts import slack_provider


def _write_source(root: Path) -> None:
    files = {
        "package.json": '{"name":"vc-slack-agent","version":"0.1.0"}\n',
        "pnpm-lock.yaml": "lockfileVersion: '9.0'\n",
        "pnpm-workspace.yaml": "packages:\n  - portal\n",
        "README.md": "# Slack\n",
        "bin/vc-slack": "#!/usr/bin/env bash\nexit 0\n",
        "src/index.js": "export {};\n",
        "src/observer.js": "export {};\n",
        "src/runtime-env.js": "export {};\n",
        "portal/package.json": '{"name":"vc-slack-portal","scripts":{"build":"vite build"}}\n',
        "scripts/doctor-bridge.sh": "#!/usr/bin/env bash\nexit 0\n",
        "scripts/install-launchagent.sh": "#!/usr/bin/env bash\nexit 0\n",
        "scripts/resolve-server-url.mjs": "#!/usr/bin/env node\n",
        "deploy/com.vetcoders.vibecrafted-slack-bridge.plist.example": "<plist/>\n",
    }
    for relative, body in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    (root / "bin" / "vc-slack").chmod(0o755)


def _write_fake_pnpm(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "from pathlib import Path\n"
        "Path('node_modules/@slack/bolt').mkdir(parents=True, exist_ok=True)\n"
        "if 'build' in sys.argv:\n"
        "    Path('portal/dist').mkdir(parents=True, exist_ok=True)\n"
        "    Path('portal/dist/index.html').write_text('<main>portal</main>')\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def test_install_publishes_immutable_provider_and_secure_env(tmp_path: Path) -> None:
    source = tmp_path / "vc-slack-agent"
    _write_source(source)
    (source / ".env").write_text("SLACK_BOT_TOKEN=secret\n", encoding="utf-8")
    pnpm = tmp_path / "pnpm"
    _write_fake_pnpm(pnpm)
    provider_root = tmp_path / "runtime" / "providers" / "vc-slack-agent"
    bin_dir = tmp_path / "bin"
    config = tmp_path / "config"

    generation = slack_provider.install(
        source,
        provider_root=provider_root,
        bin_dir=bin_dir,
        user_config_home=config,
        pnpm=str(pnpm),
    )

    assert (provider_root / "current").resolve() == generation
    assert (bin_dir / "vc-slack").resolve() == generation / "bin" / "vc-slack"
    assert not (generation / ".env").exists()
    assert (generation / "portal" / "dist" / "index.html").is_file()
    assert (config / "slack.env").stat().st_mode & 0o777 == 0o600
    manifest = json.loads((generation / "provider-manifest.json").read_text())
    assert manifest["schema"] == "vibecrafted.slack-provider.v1"
    assert len(manifest["content_sha256"]) == 64
    healthy, detail = slack_provider.doctor(
        provider_root=provider_root, bin_dir=bin_dir
    )
    assert healthy, detail


def test_discover_source_prefers_explicit_valid_package(tmp_path: Path) -> None:
    framework = tmp_path / "vibecrafted"
    explicit = tmp_path / "custom-slack"
    framework.mkdir()
    _write_source(explicit)

    assert slack_provider.discover_source(framework, explicit) == explicit.resolve()


def test_doctor_rejects_launcher_outside_current_generation(tmp_path: Path) -> None:
    root = tmp_path / "providers" / "vc-slack-agent"
    generation = root / "generation-good"
    generation.mkdir(parents=True)
    (generation / "bin").mkdir()
    (generation / "bin" / "vc-slack").write_text("#!/bin/sh\n", encoding="utf-8")
    (generation / "provider-manifest.json").write_text("{}\n", encoding="utf-8")
    (generation / "node_modules").mkdir()
    (root / "current").symlink_to(generation)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    foreign = tmp_path / "checkout" / "vc-slack"
    foreign.parent.mkdir()
    foreign.write_text("#!/bin/sh\n", encoding="utf-8")
    (bin_dir / "vc-slack").symlink_to(foreign)

    healthy, detail = slack_provider.doctor(provider_root=root, bin_dir=bin_dir)

    assert not healthy
    assert "does not resolve" in detail
