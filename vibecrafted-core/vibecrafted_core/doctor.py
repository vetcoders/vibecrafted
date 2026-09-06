"""Package-API doctor: wraps the installer doctor and adds runtime health checks."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.parsers.expat import ExpatError

import tomllib

from .package_resources import (
    deck_path,
    release_contract_paths,
    runtime_path,
    skills_path,
)
from .vc_frame_delivery import (
    OPERATOR_SCRIPT_NAMES,
    tools_current_path,
    vc_frame_user_config_dir,
)

_INSTALLER_MODULE: Any | None = None


@dataclass(frozen=True)
class _Finding:
    """Duck-typed finding compatible with the installer's DoctorFinding."""

    level: str
    component: str
    message: str


def _vc_frame_launcher_findings(
    which: Callable[[str], str | None] = shutil.which,
) -> list[_Finding]:
    """PATH ``vc-frame`` must be the product wrapper, not a raw Mach-O.

    A copied binary or an old wrapper without the Darwin ``/tmp`` pin is how
    Claude/CLI keep overflowing macOS sockaddr_un after the app was fixed.
    """

    resolved = which("vc-frame")
    if not resolved:
        return [
            _Finding(
                "warn",
                "vc-frame:path",
                (
                    "optional: vc-frame (cockpit) not on PATH — headless runs work "
                    "without it; it ships with the Vibecrafted desktop app, or "
                    "maintainers build it via `make install` with the vc-frame "
                    "checkout beside this repo"
                ),
            )
        ]
    path = Path(resolved)
    try:
        target = path.resolve()
    except OSError:
        target = path
    try:
        head = path.read_text(encoding="utf-8", errors="ignore")[:4096]
    except OSError as exc:
        return [_Finding("warn", "vc-frame:path", f"cannot read {path}: {exc}")]
    if not head.lstrip().startswith("#!"):
        return [
            _Finding(
                "fail",
                "vc-frame:path",
                f"vc-frame on PATH ({path}) is a raw binary, not the product "
                "wrapper. Claude/CLI will use TMPDIR sockets and overflow "
                "macOS sockaddr_un. Reinstall the verified Runtime Pack.",
            )
        ]
    pin_owner = path
    if "pin_darwin_socket_dir" not in head:
        exec_target = _launcher_exec_target(head)
        runtime_home = _runtime_home_root()
        target_head = ""
        if exec_target is not None and _is_inside(exec_target, runtime_home):
            try:
                target_head = exec_target.read_text(encoding="utf-8", errors="ignore")[
                    :4096
                ]
            except OSError:
                target_head = ""
        if "pin_darwin_socket_dir" not in target_head:
            return [
                _Finding(
                    "fail",
                    "vc-frame:path",
                    f"vc-frame on PATH ({path}) is a wrapper without the Darwin "
                    "/tmp socket pin. Update scripts/vc-frame-product-entry.sh "
                    "and reinstall the product entry.",
                )
            ]
        pin_owner = exec_target
    native = pin_owner.parent.parent / "libexec" / "vc-frame"
    if not _is_native_executable(native):
        return [
            _Finding(
                "fail",
                "vc-frame:path",
                f"product wrapper on PATH resolves through {pin_owner}, but its "
                f"generation has no native vc-frame at {native}. The wrapper is "
                "not a usable installation; install a complete Runtime Pack.",
            )
        ]
    kind = "symlink" if path.is_symlink() else "file"
    return [
        _Finding(
            "ok",
            "vc-frame:path",
            f"product wrapper on PATH ({kind} {path} -> {target}; pin={pin_owner})",
        )
    ]


def _is_native_executable(path: Path) -> bool:
    """Recognise the native provider without executing a potentially live TUI."""
    try:
        if not path.is_file() or not os.access(path, os.X_OK):
            return False
        magic = path.read_bytes()[:4]
    except OSError:
        return False
    return magic == b"\x7fELF" or magic in {
        b"\xca\xfe\xba\xbe",
        b"\xbe\xba\xfe\xca",
        b"\xfe\xed\xfa\xce",
        b"\xce\xfa\xed\xfe",
        b"\xfe\xed\xfa\xcf",
        b"\xcf\xfa\xed\xfe",
    }


def _uv_tool_shim() -> Path:
    """Return the expected path of the uv-tool-installed `vibecrafted` shim."""
    data_home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(data_home) / "uv" / "tools" / "vibecrafted" / "bin" / "vibecrafted"


def _loaded_checkout_root(package_dir: Path) -> Path | None:
    """Return the git checkout root when the loaded package is a living tree.

    A living tree is a monorepo layout (``<root>/vibecrafted-core/vibecrafted_core``
    plus ``<root>/scripts/vetcoders_install.py``) that still carries ``.git``.
    Staged generations copy the same layout without ``.git``, so the git probe is
    what separates "imported from the checkout" (cwd or an editable ``.pth``
    pointing at the source tree) from a genuine editable install elsewhere.
    """
    from .runtime_receipt import find_git_dir

    if package_dir.parent.name != "vibecrafted-core":
        return None
    root = package_dir.parent.parent
    if not (root / "scripts" / "vetcoders_install.py").is_file():
        return None
    return root if find_git_dir(package_dir) is not None else None


def _runtime_home_root() -> Path:
    """The installed runtime home, resolved — the ownership boundary for PATH.

    `docs/runtime/INSTALLED_RUNTIME_CAPSULE.md` states the gate in exactly these
    terms: doctor fails "when the public launcher resolves outside
    `~/.local/share/vibecrafted`". Ownership is the directory a launcher lands
    in, never the shape of its name.
    """
    from .runtime_paths import vibecrafted_runtime_home

    home = vibecrafted_runtime_home()
    try:
        return home.resolve(strict=True)
    except OSError:
        return home


def _is_inside(candidate: Path, root: Path) -> bool:
    """True when `candidate` is `root` itself or lives beneath it."""
    return candidate == root or root in candidate.parents


def _launcher_exec_target(head: str) -> Path | None:
    """Resolve the executable a generated bash launcher hands control to.

    `Vibecrafted.app` writes `~/.local/bin/<tool>` as a small env preamble ending
    in `exec '<runtime_root>/bin/<tool>' "$@"`. That wrapper is a regular file,
    so `resolve()` never reaches the runtime it actually runs — the `exec` line
    is its only honest statement of ownership. It is believed only when the
    named target really exists and is executable, so a wrapper cannot talk its
    way into an installed root it does not enter.
    """
    for line in reversed(head.splitlines()):
        stripped = line.strip()
        if not stripped.startswith("exec "):
            continue
        try:
            argv = shlex.split(stripped)
        except ValueError:
            return None
        if len(argv) < 2:
            return None
        target = Path(argv[1])
        if not target.is_file() or not os.access(target, os.X_OK):
            return None
        try:
            return target.resolve(strict=True)
        except OSError:
            return None
    return None


def _entered_runtime_version(
    target: Path, runtime_home: Path
) -> tuple[Path, str] | None:
    """Read the VERSION of the runtime root the PATH launcher actually enters.

    Walks up from the exec target to the nearest ancestor inside the runtime
    home that carries a `VERSION` file — `releases/<version>/VERSION` for the
    app channel, the generation directory for the `make install` channel.
    """
    from .runtime_paths import read_version_file

    node = target.parent
    while _is_inside(node, runtime_home) and node != runtime_home:
        if (node / "VERSION").is_file():
            return node, read_version_file(node)
        node = node.parent
    return None


def _launcher_shim_findings(
    which: Callable[[str], str | None] = shutil.which,
) -> list[_Finding]:
    """Verify that `vibecrafted` enters an installed owner, never a checkout."""
    resolved = which("vibecrafted")
    if not resolved:
        return [
            _Finding(
                "warn",
                "launcher",
                "vibecrafted not found on PATH — run the installer or "
                "`uv tool install vibecrafted`",
            )
        ]
    path = Path(resolved)
    try:
        head = path.read_text(encoding="utf-8", errors="ignore")[:4096]
    except OSError as exc:
        return [_Finding("warn", "launcher", f"cannot read {path}: {exc}")]

    findings: list[_Finding] = []
    entered: tuple[Path, str] | None = None
    if "vibecrafted_core.cli" in head and "import main" in head:
        findings.append(
            _Finding("ok", "launcher", f"Python package entrypoint on PATH -> {path}")
        )
    elif head.lstrip().startswith("#!") and "bash" in head.splitlines()[0]:
        try:
            deck = path.resolve(strict=True)
        except OSError:
            deck = path
        runtime_home = _runtime_home_root()
        target = _launcher_exec_target(head)
        if _is_inside(deck, runtime_home):
            findings.append(
                _Finding(
                    "ok",
                    "launcher",
                    f"immutable runtime command deck on PATH -> {deck}",
                )
            )
        elif target is not None and _is_inside(target, runtime_home):
            findings.append(
                _Finding(
                    "ok",
                    "launcher",
                    f"installed runtime launcher on PATH -> {path} -> {target}",
                )
            )
            entered = _entered_runtime_version(target, runtime_home)
        else:
            shim = _uv_tool_shim()
            shim_hint = f" (uv-tool shim lives at {shim})" if shim.exists() else ""
            return [
                _Finding(
                    "fail",
                    "launcher",
                    f"vibecrafted on PATH ({path}) is a checkout/legacy bash deck: "
                    f"neither it nor the launcher it execs lands inside the "
                    f"installed runtime home ({runtime_home}){shim_hint}. "
                    "Reinstall so an installed owner wins PATH.",
                )
            ]
    else:
        findings.append(
            _Finding(
                "warn",
                "launcher",
                f"vibecrafted on PATH ({path}) is neither a package entrypoint "
                f"nor the known deck — verify the install channel",
            )
        )

    # Version identity: bare package VERSION (no +gSHA) means an unstamped
    # editable / living-tree checkout. Even when resolve lifts to the staged
    # stamp for --version honesty, surface the PATH shadow so doctor is not
    # "190 ok" while Homebrew editable wins the binary.
    from . import __file__ as package_file
    from . import __version__ as resolved_version
    from .runtime_paths import (
        read_staged_tools_version,
        read_version_file,
        version_is_stamped,
        vibecrafted_tools_home,
    )

    staged = read_staged_tools_version()
    package_dir = Path(package_file).resolve().parent
    package_version = read_version_file(package_dir)
    tools_home = vibecrafted_tools_home().resolve()
    package_outside_tools = tools_home not in package_dir.parents
    checkout_root = _loaded_checkout_root(package_dir)

    if not version_is_stamped(resolved_version):
        staged_hint = (
            f" Staged install stamp is {staged}."
            if version_is_stamped(staged)
            else " Run `make install` to stamp tools/vibecrafted-current."
        )
        cause_hint = (
            f"Cause: this process imported the living tree at {checkout_root} "
            f"(cwd inside the checkout, or an editable .pth pointing at it) — "
            f"re-run doctor from outside the checkout to read the installed "
            f"launcher."
            if checkout_root is not None
            else "Common cause: Homebrew/pip editable install of the living "
            "tree shadows ~/.local/bin (PATH order). Uninstall the "
            "editable package or put ~/.local/bin first."
        )
        findings.append(
            _Finding(
                "fail",
                "version",
                f"vibecrafted --version is unstamped ({resolved_version}) — "
                f"install identity must be X.Y.Z+gSHORTSHA.{staged_hint} "
                f"{cause_hint}",
            )
        )
    elif entered is not None and entered[1] not in ("unknown", resolved_version):
        entered_root, entered_version = entered
        findings.append(
            _Finding(
                "warn",
                "version",
                f"doctor resolves install identity {resolved_version}, but the "
                f"PATH launcher enters {entered_root} which is stamped "
                f"{entered_version}. Two installed runtimes are live and the "
                f"reported version is not the one that runs. Re-run the "
                f"installer for the channel you want to own PATH.",
            )
        )
    else:
        findings.append(
            _Finding("ok", "version", f"stamped install identity {resolved_version}")
        )

    if (
        package_outside_tools
        and not version_is_stamped(package_version)
        and version_is_stamped(staged)
    ):
        if checkout_root is None:
            findings.append(
                _Finding(
                    "fail",
                    "launcher",
                    f"loaded package tree is unstamped ({package_version} at "
                    f"{package_dir}) while make-install stamp is {staged}. "
                    f"PATH winner {path} is almost certainly a pip/Homebrew "
                    f"editable living-tree install. Uninstall it "
                    f"(`python3 -m pip uninstall vibecrafted`) or ensure "
                    f"~/.local/bin precedes Homebrew on PATH.",
                )
            )
        else:
            # Import came from the source checkout, not from a rogue install:
            # doctor cannot see the installed package while cwd (or an editable
            # .pth) puts the living tree first on sys.path. Never tell the
            # operator to uninstall a healthy install over a cwd artefact.
            launcher_is_installed_owner = any(
                finding.component == "launcher" and finding.level == "ok"
                for finding in findings
            )
            cwd_shadow_only = launcher_is_installed_owner and version_is_stamped(
                resolved_version
            )
            tail = (
                f"PATH launcher {path} itself resolves the stamped identity "
                f"{resolved_version}, so the install looks healthy — re-run "
                f"doctor from outside the checkout (e.g. `cd ~ && vibecrafted "
                f"doctor`) to verify the installed launcher. Do not uninstall "
                f"anything based on this finding."
                if cwd_shadow_only
                else f"PATH winner {path} is not a proven installed owner "
                f"either — re-run doctor from outside the checkout to verify "
                f"the installed launcher, and reinstall only if it still "
                f"reports an unstamped identity."
            )
            findings.append(
                _Finding(
                    "warn" if cwd_shadow_only else "fail",
                    "launcher",
                    f"loaded package tree is the living checkout at "
                    f"{checkout_root} ({package_version} at {package_dir}), "
                    f"not the make-install stamp {staged}. {tail}",
                )
            )
    elif version_is_stamped(staged) and resolved_version != staged:
        findings.append(
            _Finding(
                "warn",
                "version",
                f"resolved version {resolved_version} differs from staged "
                f"tools stamp {staged} — re-run make install or clear an "
                f"editable PATH shadow",
            )
        )

    return findings


def _codex_mcp_config_findings(config_path: Path | None = None) -> list[_Finding]:
    """Reject the known streamable-HTTP-to-SSE endpoint mismatch before startup."""

    path = (
        config_path
        or Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "config.toml"
    )
    if not path.is_file():
        return []
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return [_Finding("warn", "codex:mcp-config", f"cannot parse {path}: {exc}")]
    servers = payload.get("mcp_servers")
    if not isinstance(servers, dict):
        return []
    findings: list[_Finding] = []
    for name, raw in sorted(servers.items()):
        if not isinstance(raw, dict):
            continue
        transport = str(raw.get("transport") or raw.get("type") or "").lower()
        url = str(raw.get("url") or "").rstrip("/")
        if transport == "streamable_http" and url.endswith(("/messages", "/sse")):
            findings.append(
                _Finding(
                    "fail",
                    "codex:mcp-config",
                    f"mcp_servers.{name} uses streamable_http with SSE-style endpoint "
                    f"{url}. Disable this alias or configure the server's real "
                    "streamable-HTTP endpoint; keep a verified stdio entry when that "
                    "is the service's supported transport.",
                )
            )
    if not findings:
        findings.append(
            _Finding("ok", "codex:mcp-config", "no obvious HTTP/SSE transport mismatch")
        )
    return findings


def _server_supervision_findings(
    *,
    platform: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    config_factory: Callable[..., Any] | None = None,
    status_reader: Callable[[Any], Any] | None = None,
) -> list[_Finding]:
    """Fail closed when the macOS control-plane service is not truly supervised."""
    resolved_platform = sys.platform if platform is None else platform
    if resolved_platform != "darwin":
        return [
            _Finding(
                "ok",
                "server-supervisor",
                f"LaunchAgent supervision not applicable on {resolved_platform}",
            )
        ]

    # The public wrapper records the launcher used before it prepends the
    # immutable generation's bin directory to PATH and execs the Python CLI.
    # Re-running `which` after that handoff finds the inner deck, whose hash is
    # intentionally different from the LaunchAgent's declared launcher.
    declared_launcher = os.environ.get("VIBECRAFTED_DECLARED_LAUNCHER", "").strip()
    resolved_launcher = (
        declared_launcher
        if declared_launcher and Path(declared_launcher).is_file()
        else which("vibecrafted")
    )
    if not resolved_launcher:
        return [
            _Finding(
                "fail",
                "server-supervisor",
                "cannot verify supervised control plane because `vibecrafted` is "
                "not on PATH — reinstall Vibecrafted",
            )
        ]

    if config_factory is None or status_reader is None:
        from .server_supervisor import default_config, service_status

        config_factory = config_factory or default_config
        status_reader = status_reader or service_status

    try:
        # The launcher that wins PATH is the installed runtime authority.  A
        # leftover uv-tool shim may point at an older generation and must not
        # make doctor inspect a different service identity than the public
        # `vibecrafted server service status` command.
        service_launcher = Path(resolved_launcher)
        config = config_factory(launcher=service_launcher)
        status = status_reader(config)
    except (
        OSError,
        RuntimeError,
        ValueError,
        subprocess.SubprocessError,
        ExpatError,
    ) as exc:
        return [
            _Finding(
                "fail",
                "server-supervisor",
                "cannot prove the installed control-plane supervisor healthy: "
                f"{exc}. Run `vibecrafted server service install`",
            )
        ]

    supervisor_pid = getattr(status, "supervisor_pid", None)
    if not bool(getattr(status, "installed", False)):
        # Never installed is not a broken install. Headless runs, observe,
        # await and reports work without the LaunchAgent; only the live
        # dashboard/server surface needs it. A stranger who never asked for a
        # daemon must not see a red line for one.
        return [
            _Finding(
                "warn",
                "server-supervisor",
                "optional: control-plane server service is not installed — "
                "headless runs, observe/await and reports work without it; "
                "for the live server/dashboard run "
                "`vibecrafted server service install`",
            )
        ]
    required = {
        "installed": True,
        "loaded": bool(getattr(status, "loaded", False)),
        "supervisor_live": bool(getattr(status, "supervisor_live", False)),
        "supervisor_verified": bool(getattr(status, "supervisor_verified", False)),
        "supervisor_service_managed": bool(
            getattr(status, "supervisor_service_managed", False)
        ),
        "build_current": bool(getattr(status, "build_current", False)),
        "pair_healthy": bool(getattr(status, "pair_healthy", False)),
        "supervisor_pid": supervisor_pid is not None,
    }
    failed = [name for name, healthy in required.items() if not healthy]
    if failed:
        return [
            _Finding(
                "fail",
                "server-supervisor",
                "control plane is not durably supervised "
                f"(failed: {', '.join(failed)}). Run "
                "`vibecrafted server service install` and re-run doctor",
            )
        ]

    return [
        _Finding(
            "ok",
            "server-supervisor",
            "verified LaunchAgent-managed supervisor and healthy server/guardian "
            f"pair (pid={supervisor_pid}, current build)",
        )
    ]


def _repo_root_from_source() -> Path | None:
    """Return the monorepo root when this package is loaded from a checkout."""
    package_root = Path(__file__).resolve().parents[1]
    candidate = package_root.parent if package_root.name == "vibecrafted-core" else None
    if candidate and (candidate / "scripts" / "vetcoders_install.py").is_file():
        return candidate
    return None


def _installer_module() -> Any:
    """Lazily load and cache the `vetcoders_install` module (checkout or import)."""
    global _INSTALLER_MODULE
    if _INSTALLER_MODULE is not None:
        return _INSTALLER_MODULE

    repo_root = _repo_root_from_source()
    if repo_root is not None:
        installer_path = repo_root / "scripts" / "vetcoders_install.py"
        spec = importlib.util.spec_from_file_location(
            "vibecrafted_runtime_vetcoders_install", installer_path
        )
        if spec is None or spec.loader is None:
            raise ModuleNotFoundError(f"Cannot load installer module: {installer_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        previous_path = list(sys.path)
        try:
            sys.path.insert(0, str(repo_root))
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(spec.name, None)
            raise
        finally:
            sys.path[:] = previous_path
        _INSTALLER_MODULE = module
        return module

    import vetcoders_install  # type: ignore[import-not-found]

    _INSTALLER_MODULE = vetcoders_install
    return vetcoders_install


def _packaged_asset_findings() -> list[_Finding]:
    """Verify runtime, UI and release-trust assets under the installed package."""
    checks = [
        (
            "runtime",
            runtime_path() / "scripts" / "await.sh",
            "packaged runtime scripts present",
        ),
        (
            "skills",
            skills_path() / "vc-justdo" / "SKILL.md",
            "packaged canonical skills present",
        ),
        ("deck", deck_path(), "packaged command deck present"),
    ]
    release_assets = release_contract_paths()
    checks.extend(
        (
            "release-contract",
            path,
            f"packaged release contract present: {path.name}",
        )
        for path in release_assets
    )
    findings: list[_Finding] = []
    for component, path, ok_message in checks:
        if path.is_file() and not path.is_symlink():
            findings.append(_Finding("ok", component, ok_message))
        else:
            findings.append(
                _Finding("fail", component, f"missing package asset: {path}")
            )
    return findings


def _config_entry_matches(candidate: Path, expected: Path) -> bool:
    """True only for an unaliased physical copy with identical closed contents."""

    def inventory(root: Path) -> dict[str, tuple[str, str, int]] | None:
        if root.is_symlink():
            return None
        if root.is_file():
            try:
                return {
                    ".": (
                        "file",
                        hashlib.sha256(root.read_bytes()).hexdigest(),
                        root.stat().st_mode & 0o777,
                    )
                }
            except OSError:
                return None
        if not root.is_dir():
            return None
        entries: dict[str, tuple[str, str, int]] = {}
        for path in sorted(root.rglob("*")):
            if path.name == ".DS_Store":
                continue
            if path.is_symlink():
                return None
            relative = path.relative_to(root).as_posix()
            if path.is_dir():
                entries[relative] = ("dir", "", path.stat().st_mode & 0o777)
            elif path.is_file():
                try:
                    entries[relative] = (
                        "file",
                        hashlib.sha256(path.read_bytes()).hexdigest(),
                        path.stat().st_mode & 0o777,
                    )
                except OSError:
                    return None
            else:
                return None
        return entries

    try:
        candidate_inventory = inventory(candidate)
        return (
            candidate_inventory is not None
            and candidate_inventory == inventory(expected)
        )
    except OSError:
        return False


def _runtime_config_generation(tools_home: Path | None = None) -> Path:
    """Inspect the launcher's selected generation; never select a dev config."""
    selected = (
        os.environ.get("VIBECRAFTED_RUNTIME_ROOT") if tools_home is None else None
    )
    root = Path(selected).expanduser() if selected else tools_current_path(tools_home)
    if not root.is_absolute():
        raise OSError(f"selected runtime path is not absolute: {root}")
    return root.resolve(strict=True)


def _vc_frame_delivery_findings(
    *,
    home: Path | None = None,
    tools_home: Path | None = None,
    path_env: str | None = None,
) -> list[_Finding]:
    """Inspect installer-owned copies, allowing preferences only in config.kdl."""
    findings: list[_Finding] = []
    view = vc_frame_user_config_dir(home)
    repair = "repair via the verified Runtime Pack runtime-install --payload-root PATH"
    try:
        generation = _runtime_config_generation(tools_home)
    except (OSError, RuntimeError) as exc:
        return [
            _Finding(
                "fail", "vc-frame:runtime",
                f"selected runtime unavailable: {exc}; {repair}",
            )
        ]
    generated = (
        generation / "vibecrafted-core/vibecrafted_core/runtime/generated/vc-frame"
    )
    for root in (view, generated):
        if not root.is_dir() or any(p.is_symlink() for p in (root, *root.parents)):
            findings.append(
                _Finding(
                    "fail", "vc-frame:view",
                    f"missing or aliased physical config tree: {root}; {repair}",
                )
            )
    if findings:
        return findings
    for variable, expected in (
        ("VC_FRAME_CONFIG_DIR", view),
        ("VC_FRAME_CONFIG_FILE", view / "config.kdl"),
    ):
        selected = os.environ.get(variable)
        if selected and Path(selected).expanduser().absolute() != expected.absolute():
            findings.append(
                _Finding(
                    "fail", "vc-frame:view",
                    f"{variable} routes outside product configuration at {expected}",
                )
            )

    names = {"config.kdl", "layouts", "themes", *OPERATOR_SCRIPT_NAMES}
    names.update(path.name for path in generated.iterdir() if path.name != ".DS_Store")
    for name in sorted(names):
        path, default = view / name, generated / name
        if name in {"layouts", "themes"}:
            expected_type = default.is_dir()
        elif name in {"config.kdl", *OPERATOR_SCRIPT_NAMES}:
            expected_type = default.is_file()
        else:
            expected_type = default.is_dir() or default.is_file()
        if (
            not expected_type
            or default.is_symlink()
            or (name in OPERATOR_SCRIPT_NAMES and not os.access(default, os.X_OK))
        ):
            findings.append(
                _Finding(
                    "fail", "vc-frame:runtime",
                    f"missing, unusable or aliased shipped asset: {default}; {repair}",
                )
            )
            continue
        if name == "config.kdl":
            try:
                if not path.is_file() or path.is_symlink():
                    raise OSError("expected a physical config.kdl")
                text = path.read_text(encoding="utf-8")
                if not text.strip() or "\0" in text:
                    raise OSError("empty or invalid config text")
            except (OSError, UnicodeError) as exc:
                findings.append(
                    _Finding("fail", "vc-frame:view", f"{path}: {exc}; {repair}")
                )
                continue
            detail = (
                "shipped defaults"
                if _config_entry_matches(path, default)
                else "user preferences (configuration syntax not validated)"
            )
            findings.append(
                _Finding("ok", "vc-frame:view", f"physical {path}: {detail}")
            )
        elif not _config_entry_matches(path, default):
            findings.append(
                _Finding(
                    "fail", "vc-frame:view",
                    f"missing, modified or misrouted owned asset: {path}; {repair}",
                )
            )
        else:
            findings.append(
                _Finding(
                    "ok", "vc-frame:view",
                    f"installed physical asset matches selected generation: {path}",
                )
            )
    return findings


def _vc_frame_truth_drift_findings(
    *,
    home: Path | None = None,
    tools_home: Path | None = None,
) -> list[_Finding]:
    """Verify sealed defaults against the generation's existing manifest.

    Host adaptation happened at install time. Today's PATH or checkout cannot
    redefine those defaults; doctor neither rematerializes nor repairs them.
    Product preferences are checked separately from immutable generation bytes.
    """
    try:
        generation = _runtime_config_generation(tools_home)
        installer = _installer_module()
        manifest, error = installer._load_runtime_generation_manifest(generation)
        if manifest is None:
            raise OSError(error or "missing runtime generation manifest")
        relative = (
            "vibecrafted-core/vibecrafted_core/runtime/generated/vc-frame/config.kdl"
        )
        path = generation / relative
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise OSError(f"aliased shipped defaults: {path}")
        expected = manifest["hashes"].get(relative)
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if not expected or actual != expected:
            raise OSError(
                f"shipped config.kdl differs from its generation manifest: {path}"
            )
    except (OSError, RuntimeError, ValueError, ImportError) as exc:
        return [
            _Finding(
                "fail", "vc-frame:truth",
                f"{exc}; reinstall the verified Runtime Pack",
            )
        ]
    return [
        _Finding(
            "ok", "vc-frame:truth",
            "shipped config.kdl matches the sealed generation manifest "
            f"at {generation}",
        )
    ]


_RELEASE_REPO_DEFAULT = "vetcoders/vibecrafted"
_RELEASE_SOURCE_GATE_WORKFLOW = "Release source gate"
_RELEASE_OPERATOR_BUTTON = (
    "operator button: tag/publish "
    "(git tag -a v<VERSION> && git push origin v<VERSION>; "
    "wait for Release source gate green; then "
    "scripts/publish-vibecrafted-release.sh)"
)
_GH_TIMEOUT_SEC = 20


def _release_tag_from_version(version: str) -> str:
    """Map a VERSION file or GitHub tagName onto a comparable ``vX.Y.Z`` tag."""
    raw = version.strip()
    if not raw or raw == "unknown":
        return ""
    if raw[0] in "vV" and len(raw) > 1 and raw[1].isdigit():
        raw = raw[1:]
    plus = raw.find("+")
    if plus >= 0:
        raw = raw[:plus]
    raw = raw.strip()
    return f"v{raw}" if raw else ""


def _invoke_gh(
    argv: Sequence[str],
    *,
    runner: Callable[..., Any],
) -> tuple[int, str, str]:
    """Run one ``gh`` argv and return (rc, stdout, stderr). Never raises."""
    try:
        completed = runner(
            list(argv),
            check=False,
            capture_output=True,
            text=True,
            timeout=_GH_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        return 124, "", f"gh timed out after {_GH_TIMEOUT_SEC}s"
    except OSError as exc:
        return 127, "", str(exc)
    return (
        int(getattr(completed, "returncode", 1) or 0),
        str(getattr(completed, "stdout", "") or ""),
        str(getattr(completed, "stderr", "") or ""),
    )


def _release_drift_findings(
    *,
    which: Callable[[str], str | None] = shutil.which,
    runner: Callable[..., Any] | None = None,
    repo_root: Path | None = None,
    release_repo: str | None = None,
) -> list[_Finding]:
    """Compare local VERSION to GitHub Latest and the last source-gate run.

    This is the C7 release valve: doctor must not stay green while Latest is
    stuck on an old tag (last successful source gate was v3.5.0). Missing
    ``gh`` is a loud warn, never a fake pass. A mismatch or a non-success
    gate conclusion is red and names the tag/publish operator button.
    """
    run = runner or subprocess.run
    repo = (
        release_repo
        or os.environ.get("VIBECRAFTED_RELEASE_REPO")
        or _RELEASE_REPO_DEFAULT
    )
    root = repo_root if repo_root is not None else _repo_root_from_source()
    findings: list[_Finding] = []

    version = "unknown"
    version_path = (root / "VERSION") if root is not None else None
    if version_path is not None and version_path.is_file():
        try:
            version = version_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            findings.append(
                _Finding(
                    "fail",
                    "release:version",
                    f"cannot read {version_path}: {exc}. {_RELEASE_OPERATOR_BUTTON}",
                )
            )
            version = "unknown"
    expected_tag = _release_tag_from_version(version)
    if expected_tag:
        findings.append(
            _Finding(
                "ok",
                "release:version",
                f"local VERSION {version} ({version_path}) → expected tag {expected_tag}",
            )
        )
    else:
        findings.append(
            _Finding(
                "fail",
                "release:version",
                "local VERSION missing or empty "
                f"at {version_path or '(no checkout)'}. {_RELEASE_OPERATOR_BUTTON}",
            )
        )

    gh = which("gh")
    if not gh:
        missing = (
            "gh is not on PATH — cannot probe GitHub Latest vs VERSION. "
            "This is not a green pass; the release valve is unproven. "
            "Install GitHub CLI (https://cli.github.com/). "
            f"{_RELEASE_OPERATOR_BUTTON}"
        )
        findings.append(_Finding("warn", "release:github-latest", missing))
        findings.append(
            _Finding(
                "warn",
                "release:source-gate",
                "gh is not on PATH — cannot probe the latest "
                f"{_RELEASE_SOURCE_GATE_WORKFLOW} conclusion. "
                f"{_RELEASE_OPERATOR_BUTTON}",
            )
        )
        return findings

    latest_rc, latest_out, latest_err = _invoke_gh(
        [gh, "release", "view", "--repo", repo, "--json", "tagName"],
        runner=run,
    )
    latest_tag = ""
    if latest_rc == 0:
        try:
            payload = json.loads(latest_out)
        except json.JSONDecodeError as exc:
            findings.append(
                _Finding(
                    "fail",
                    "release:github-latest",
                    "gh release view returned unreadable JSON "
                    f"({exc}). {_RELEASE_OPERATOR_BUTTON}",
                )
            )
        else:
            if isinstance(payload, dict):
                latest_tag = str(payload.get("tagName") or "").strip()
            if not latest_tag:
                findings.append(
                    _Finding(
                        "fail",
                        "release:github-latest",
                        "gh release view returned no tagName. "
                        f"{_RELEASE_OPERATOR_BUTTON}",
                    )
                )
    else:
        detail = (latest_err or latest_out).strip() or f"exit {latest_rc}"
        findings.append(
            _Finding(
                "fail",
                "release:github-latest",
                f"gh release view failed ({detail}). {_RELEASE_OPERATOR_BUTTON}",
            )
        )

    if latest_tag:
        latest_norm = _release_tag_from_version(latest_tag)
        if expected_tag and latest_norm == expected_tag:
            findings.append(
                _Finding(
                    "ok",
                    "release:github-latest",
                    f"VERSION {version} matches GitHub Latest {latest_tag}",
                )
            )
        else:
            findings.append(
                _Finding(
                    "fail",
                    "release:github-latest",
                    f"VERSION {version} ≠ GitHub Latest {latest_tag} "
                    f"(expected {expected_tag or 'v<VERSION>'}). "
                    f"{_RELEASE_OPERATOR_BUTTON}",
                )
            )

    gate_rc, gate_out, gate_err = _invoke_gh(
        [
            gh,
            "run",
            "list",
            "--repo",
            repo,
            "--workflow",
            _RELEASE_SOURCE_GATE_WORKFLOW,
            "--limit",
            "1",
            "--json",
            "conclusion,status,displayTitle,databaseId,headSha",
        ],
        runner=run,
    )
    if gate_rc != 0:
        detail = (gate_err or gate_out).strip() or f"exit {gate_rc}"
        findings.append(
            _Finding(
                "fail",
                "release:source-gate",
                f"gh run list for {_RELEASE_SOURCE_GATE_WORKFLOW} failed "
                f"({detail}). {_RELEASE_OPERATOR_BUTTON}",
            )
        )
        return findings
    try:
        gate_payload = json.loads(gate_out)
    except json.JSONDecodeError as exc:
        findings.append(
            _Finding(
                "fail",
                "release:source-gate",
                "gh run list returned unreadable JSON "
                f"({exc}). {_RELEASE_OPERATOR_BUTTON}",
            )
        )
        return findings
    if not isinstance(gate_payload, list) or not gate_payload:
        findings.append(
            _Finding(
                "fail",
                "release:source-gate",
                f"no {_RELEASE_SOURCE_GATE_WORKFLOW} run exists. "
                f"{_RELEASE_OPERATOR_BUTTON}",
            )
        )
        return findings
    row = gate_payload[0] if isinstance(gate_payload[0], dict) else {}
    conclusion = str(row.get("conclusion") or "").strip().lower()
    status = str(row.get("status") or "").strip().lower()
    title = str(row.get("displayTitle") or "").strip() or "(untitled)"
    run_id = row.get("databaseId")
    if conclusion == "success" and status in ("", "completed"):
        findings.append(
            _Finding(
                "ok",
                "release:source-gate",
                f"{_RELEASE_SOURCE_GATE_WORKFLOW} latest is success "
                f"({title}, run={run_id})",
            )
        )
    else:
        findings.append(
            _Finding(
                "fail",
                "release:source-gate",
                f"{_RELEASE_SOURCE_GATE_WORKFLOW} latest is "
                f"{conclusion or status or 'unknown'} ({title}, run={run_id}). "
                f"{_RELEASE_OPERATOR_BUTTON}",
            )
        )
    return findings


def doctor_run(
    store_path: str | Path | None = None,
    state: Any | None = None,
    *,
    release: bool = False,
) -> list[Any]:
    """Run the existing Vibecrafted installer doctor through a package API."""
    try:
        installer = _installer_module()
    except ModuleNotFoundError:
        findings = _packaged_asset_findings()
    else:
        resolved_store = (
            Path(store_path)
            if store_path is not None
            else installer._canonical_store_path(installer.vibecrafted_home())
        )
        resolved_state = (
            state if state is not None else installer.InstallState.load(resolved_store)
        )
        findings = list(installer.run_doctor(resolved_store, resolved_state))
        findings.extend(_packaged_asset_findings())
    findings.extend(_launcher_shim_findings())
    findings.extend(_vc_frame_launcher_findings())
    findings.extend(_codex_mcp_config_findings())
    findings.extend(_server_supervision_findings())
    findings.extend(_vc_frame_delivery_findings())
    findings.extend(_vc_frame_truth_drift_findings())
    if release:
        findings.extend(_release_drift_findings())
    return findings


def doctor_summary(findings: Sequence[Any]) -> dict[str, Any]:
    """Reduce a findings sequence to ok/warn/fail counts plus a serialized list."""
    oks = sum(1 for finding in findings if finding.level == "ok")
    warnings = sum(1 for finding in findings if finding.level == "warn")
    failures = sum(1 for finding in findings if finding.level == "fail")
    healthy = failures == 0
    return {
        "ok": oks,
        "warnings": warnings,
        "failures": failures,
        "healthy": healthy,
        "authority": {
            "available": True,
            "healthy": healthy,
            "ok_count": oks,
            "failure_count": failures,
            "warning_count": warnings,
        },
        "findings": [
            {
                "level": finding.level,
                "component": finding.component,
                "message": finding.message,
            }
            for finding in findings
        ],
    }
