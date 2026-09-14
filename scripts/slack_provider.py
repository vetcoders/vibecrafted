#!/usr/bin/env python3
"""Install the Slack gateway as an immutable Vibecrafted provider.

The source repository is a development surface. This installer copies only the
runtime package, installs production Node dependencies in a content-addressed
generation, and publishes it through a stable ``current`` pointer. Secrets live
in the per-user config directory and are never copied into the generation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

PROVIDER_NAME = "vc-slack-agent"
REQUIRED_FILES = (
    "package.json",
    "pnpm-lock.yaml",
    "pnpm-workspace.yaml",
    "bin/vc-slack",
    "src/index.js",
    "src/observer.js",
    "src/runtime-env.js",
    # console/ merged into portal/ (vc-slack fbefeaa: "one front door");
    # the portal is a Vite workspace member, so there is no server.mjs twin.
    "portal/package.json",
    "scripts/doctor-bridge.sh",
    "scripts/install-launchagent.sh",
    "scripts/resolve-server-url.mjs",
    "deploy/com.vetcoders.vibecrafted-slack-bridge.plist.example",
)
COPY_PATHS = (
    "package.json",
    "pnpm-lock.yaml",
    "pnpm-workspace.yaml",
    "README.md",
    "bin",
    "src",
    "scripts",
    "deploy",
    "portal",
)


class ProviderError(RuntimeError):
    """The Slack provider cannot be installed without breaking provenance."""


def runtime_home() -> Path:
    """Resolve the Vibecrafted runtime data root (VIBECRAFTED_RUNTIME_HOME, else XDG)."""
    configured = os.environ.get("VIBECRAFTED_RUNTIME_HOME", "").strip()
    if configured:
        return Path(configured).expanduser()
    data_home = Path(
        os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
    ).expanduser()
    return data_home / "vibecrafted"


def config_home() -> Path:
    """Resolve the per-user Vibecrafted config directory (XDG_CONFIG_HOME or ~/.config)."""
    configured = os.environ.get("XDG_CONFIG_HOME", "").strip()
    root = Path(configured).expanduser() if configured else Path.home() / ".config"
    return root / "vibecrafted"


def launcher_bin() -> Path:
    """Resolve the directory where the public `vc-slack` launcher symlink is published."""
    configured = os.environ.get("VIBECRAFTED_LAUNCHER_BIN", "").strip()
    return (
        Path(configured).expanduser() if configured else Path.home() / ".local" / "bin"
    )


def discover_source(
    framework_source: Path, explicit: Path | None = None
) -> Path | None:
    """Find a vc-slack-agent checkout via explicit path, env var, or sibling directory.

    Returns the first resolved candidate that has every ``REQUIRED_FILES``
    entry present, or None if no candidate qualifies.
    """
    candidates = []
    env_source = os.environ.get("VIBECRAFTED_SLACK_AGENT_SOURCE", "").strip()
    if explicit is not None:
        candidates.append(explicit)
    if env_source:
        candidates.append(Path(env_source).expanduser())
    candidates.extend(
        (
            framework_source.parent / "vc-slack-agent",
            framework_source.parent / "vibecrafted-slack-agent",
        )
    )
    for candidate in candidates:
        resolved = candidate.resolve(strict=False)
        if all((resolved / relative).is_file() for relative in REQUIRED_FILES):
            return resolved
    return None


def _tree_digest(source: Path, *, include_portal_dist: bool) -> str:
    """Hash runtime files while excluding dependencies and checkout build residue."""
    digest = hashlib.sha256()
    for relative_root in COPY_PATHS:
        root = source / relative_root
        paths = [root] if root.is_file() else sorted(root.rglob("*"))
        for path in paths:
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(source)
            if "node_modules" in relative.parts:
                continue
            if not include_portal_dist and relative.parts[:2] == ("portal", "dist"):
                continue
            if relative.name == "com.vetcoders.vibecrafted-slack-bridge.plist":
                continue
            digest.update(relative.as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def _source_digest(source: Path) -> str:
    """Hash source inputs to the immutable generation id.

    Excludes symlinks and the local, machine-specific rendered plist so the
    digest only reflects files that participate in the reproducible build.
    Existing portal build output is excluded because staging rebuilds it.
    """
    return _tree_digest(source, include_portal_dist=False)


def _content_digest(generation: Path) -> str:
    """Hash the published runtime payload, including built portal assets."""
    return _tree_digest(generation, include_portal_dist=True)


def _copy_runtime(source: Path, destination: Path) -> None:
    """Copy the runtime COPY_PATHS into destination, excluding secrets/build artifacts.

    Raises ProviderError if any REQUIRED_FILES entry is missing from the
    copied payload (as a real, non-symlinked file).
    """
    for relative in COPY_PATHS:
        src = source / relative
        if not src.exists():
            continue
        dst = destination / relative
        if src.is_dir():
            shutil.copytree(
                src,
                dst,
                symlinks=False,
                ignore=shutil.ignore_patterns(
                    "node_modules",
                    ".env",
                    ".env.*",
                    "*.log",
                    "com.vetcoders.vibecrafted-slack-bridge.plist",
                ),
            )
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    for relative in REQUIRED_FILES:
        path = destination / relative
        if not path.is_file() or path.is_symlink():
            raise ProviderError(f"provider payload is missing {relative}")


def _atomic_symlink(target: Path, link: Path) -> None:
    """Point link at target via a temp-symlink + rename so readers never see a partial state."""
    link.parent.mkdir(parents=True, exist_ok=True)
    temporary = link.parent / f".{link.name}.tmp-{os.getpid()}"
    try:
        temporary.unlink(missing_ok=True)
        temporary.symlink_to(target)
        os.replace(temporary, link)
    finally:
        temporary.unlink(missing_ok=True)


def _migrate_env(source: Path, destination: Path) -> bool:
    """One-way migrate a legacy source-tree .env to destination/slack.env, then leave it.

    Refuses foreign-owned or oversized (>256KiB) env files, and never
    overwrites an existing destination secret. Returns True if a migration
    was performed.
    """
    source_env = source / ".env"
    target_env = destination / "slack.env"
    if target_env.exists():
        if target_env.is_symlink() or not target_env.is_file():
            raise ProviderError(f"Slack config is not a regular file: {target_env}")
        return False
    if not source_env.is_file() or source_env.is_symlink():
        return False
    info = source_env.stat(follow_symlinks=False)
    if info.st_uid != os.geteuid() or not stat.S_ISREG(info.st_mode):
        raise ProviderError("refusing to migrate a foreign Slack .env")
    payload = source_env.read_bytes()
    if len(payload) > 256 * 1024:
        raise ProviderError("Slack .env exceeds the bounded migration size")
    destination.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".slack.env.", dir=destination)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target_env)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def install(
    source: Path,
    *,
    provider_root: Path | None = None,
    bin_dir: Path | None = None,
    user_config_home: Path | None = None,
    pnpm: str | None = None,
) -> Path:
    """Install source as an immutable, content-addressed Slack provider generation.

    Content-addressed by ``_source_digest``: an existing generation for the
    same digest is reused rather than rebuilt. Runs a frozen pnpm workspace
    install and builds the operator portal inside staging, writes a provider
    manifest, publishes the `current` pointer and the public `vc-slack`
    launcher symlink, and migrates any legacy `.env`.
    Returns the generation directory.
    """
    source = source.resolve(strict=True)
    for relative in REQUIRED_FILES:
        if not (source / relative).is_file():
            raise ProviderError(f"Slack provider source is missing {relative}")
    digest = _source_digest(source)
    root = provider_root or runtime_home() / "providers" / PROVIDER_NAME
    generation = root / f"generation-{digest[:16]}"
    current = root / "current"
    if not generation.is_dir():
        root.mkdir(parents=True, exist_ok=True)
        staging = root / f".staging-{os.getpid()}-{digest[:8]}"
        if staging.exists() or staging.is_symlink():
            shutil.rmtree(staging)
        staging.mkdir(mode=0o755)
        try:
            _copy_runtime(source, staging)
            pnpm_bin = pnpm or shutil.which("pnpm")
            if not pnpm_bin:
                raise ProviderError("pnpm is required to install the Slack provider")
            subprocess.run(
                [
                    pnpm_bin,
                    "install",
                    "--frozen-lockfile",
                    "--ignore-scripts",
                ],
                cwd=staging,
                check=True,
            )
            subprocess.run(
                [pnpm_bin, "--dir", "portal", "run", "build"],
                cwd=staging,
                check=True,
            )
            if not (staging / "portal" / "dist" / "index.html").is_file():
                raise ProviderError(
                    "Slack portal build did not produce dist/index.html"
                )
            manifest = {
                "schema": "vibecrafted.slack-provider.v1",
                "source_sha256": digest,
                "content_sha256": _content_digest(staging),
                "entrypoint": "bin/vc-slack",
            }
            (staging / "provider-manifest.json").write_text(
                json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
            )
            if generation.exists():
                shutil.rmtree(staging)
            else:
                staging.rename(generation)
        except BaseException:
            if staging.exists():
                shutil.rmtree(staging)
            raise
    _atomic_symlink(generation, current)
    target = current / "bin" / "vc-slack"
    if not target.is_file():
        raise ProviderError("published Slack provider has no launcher")
    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    public = (bin_dir or launcher_bin()) / "vc-slack"
    _atomic_symlink(target, public)
    _migrate_env(source, user_config_home or config_home())
    return generation


def doctor(
    *, provider_root: Path | None = None, bin_dir: Path | None = None
) -> tuple[bool, str]:
    """Verify the published Slack provider is intact: pointer, launcher, and content digest.

    Returns (healthy, detail message).
    """
    root = provider_root or runtime_home() / "providers" / PROVIDER_NAME
    current = root / "current"
    public = (bin_dir or launcher_bin()) / "vc-slack"
    try:
        generation = current.resolve(strict=True)
        launcher = public.resolve(strict=True)
    except OSError as exc:
        return False, f"Slack provider is not published: {exc}"
    if generation.parent != root.resolve(
        strict=False
    ) or not generation.name.startswith("generation-"):
        return False, "Slack provider current pointer escapes its generation root"
    expected_launcher = generation / "bin" / "vc-slack"
    if not expected_launcher.is_file():
        return False, "Slack provider generation has no vc-slack launcher"
    if launcher != expected_launcher.resolve(strict=True):
        return False, "vc-slack does not resolve to the current immutable provider"
    manifest = generation / "provider-manifest.json"
    if not manifest.is_file() or not (generation / "node_modules").is_dir():
        return False, "Slack provider generation is incomplete"
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, "Slack provider manifest is unreadable"
    expected_digest = payload.get("content_sha256")
    if (
        not isinstance(expected_digest, str)
        or _content_digest(generation) != expected_digest
    ):
        return False, "Slack provider runtime files drifted after publication"
    return True, f"vc-slack -> {generation.name}"


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: `install` (discover+install, optionally required) or `doctor`."""
    parser = argparse.ArgumentParser(prog="vibecrafted-slack-provider")
    subparsers = parser.add_subparsers(dest="command", required=True)
    install_parser = subparsers.add_parser("install")
    install_parser.add_argument("--framework-source", type=Path, required=True)
    install_parser.add_argument("--source", type=Path)
    install_parser.add_argument("--required", action="store_true")
    subparsers.add_parser("doctor")
    args = parser.parse_args(argv)
    if args.command == "doctor":
        healthy, detail = doctor()
        print(detail)
        return 0 if healthy else 1
    source = discover_source(args.framework_source, args.source)
    if source is None:
        if args.required:
            raise ProviderError("vc-slack-agent source was not found")
        healthy, detail = doctor()
        print(
            detail
            if healthy
            else "Slack provider source absent; provider not installed"
        )
        return 0
    generation = install(source)
    print(f"[slack-provider] installed immutable generation {generation}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ProviderError, subprocess.SubprocessError) as exc:
        print(f"[slack-provider] FATAL: {exc}", file=os.sys.stderr)
        raise SystemExit(1) from exc
