"""Package-API doctor: wraps the installer doctor and adds runtime health checks."""

from __future__ import annotations

import hashlib
import importlib.util
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
    classify_view_path,
    frontier_root,
    list_dangling_frontier_links,
    prefer_repo_vc_frame,
    resolve_clipboard_command,
    resolve_pane_shell,
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
                "vc-frame not found on PATH — run `make install` or the foundations installer",
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
                "macOS sockaddr_un. Re-run the foundations installer.",
            )
        ]
    if "pin_darwin_socket_dir" not in head:
        return [
            _Finding(
                "fail",
                "vc-frame:path",
                f"vc-frame on PATH ({path}) is a wrapper without the Darwin "
                "/tmp socket pin. Update scripts/vc-frame-product-entry.sh "
                "and reinstall the product entry.",
            )
        ]
    kind = "symlink" if path.is_symlink() else "file"
    return [
        _Finding(
            "ok",
            "vc-frame:path",
            f"product wrapper on PATH ({kind} {path} -> {target})",
        )
    ]


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

    resolved_launcher = which("vibecrafted")
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
        service_launcher = _uv_tool_shim()
        if not service_launcher.is_file():
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


def _vc_frame_delivery_findings(
    *,
    home: Path | None = None,
    tools_home: Path | None = None,
    path_env: str | None = None,
) -> list[_Finding]:
    """Config delivery health: view channel, themes, pane-shell, frontier zombies."""
    findings: list[_Finding] = []
    view = vc_frame_user_config_dir(home)
    current = tools_current_path(tools_home)
    package = current / "vibecrafted-core" / "vibecrafted_core"
    store_cfg = package / "config" / "vc-frame"
    checkout = None
    try:
        from .frontier_assets import vc_frame_config_source

        checkout = vc_frame_config_source()
    except FileNotFoundError:
        pass
    use_repo = prefer_repo_vc_frame()
    generated = package / "runtime" / "generated" / "vc-frame"
    materialized_paths = (
        generated / "config.kdl",
        generated / "layouts",
        generated / "themes",
        generated / "vc-composer.sh",
    )
    materialized = all(
        path.is_file() if path.suffix else path.is_dir() for path in materialized_paths
    )
    if use_repo:
        findings.append(
            _Finding(
                "ok",
                "vc-frame:runtime",
                "dev-checkout channel does not require a published config generation",
            )
        )
        view_repair = "`vibecrafted config install --prefer-repo`"
    elif materialized:
        findings.append(
            _Finding(
                "ok",
                "vc-frame:runtime",
                f"pre-materialized config present under {generated}",
            )
        )
        view_repair = "`vibecrafted config install`"
    else:
        findings.append(
            _Finding(
                "fail",
                "vc-frame:runtime",
                f"published runtime has no complete pre-materialized config "
                f"under {generated} — run `vibecrafted update`",
            )
        )
        view_repair = "`vibecrafted update`"

    channels: list[str] = []
    for name in ("config.kdl", "layouts", "themes"):
        path = view / name
        ch = classify_view_path(path, store_current=store_cfg, checkout=checkout)
        channels.append(ch)
        if ch == "DANGLING":
            findings.append(
                _Finding(
                    "fail",
                    "vc-frame:view",
                    f"{path} is a dangling symlink — run {view_repair}",
                )
            )
        elif ch == "STALE-FILE":
            findings.append(
                _Finding(
                    "fail",
                    "vc-frame:view",
                    f"{path} is a regular file shadowing the store view — "
                    f"run {view_repair} (backs up as .stale.* when wiring)",
                )
            )
        elif ch == "missing":
            findings.append(
                _Finding(
                    "warn",
                    "vc-frame:view",
                    f"{path} missing — run {view_repair}",
                )
            )
        elif ch == "foreign":
            findings.append(
                _Finding(
                    "warn",
                    "vc-frame:view",
                    f"{path} is user-managed (foreign) — not store/dev view",
                )
            )
        else:
            findings.append(_Finding("ok", "vc-frame:view", f"{name}: {ch} -> {path}"))

    # themes presence under view or source
    themes_dir = view / "themes"
    if themes_dir.is_dir() or themes_dir.is_symlink():
        try:
            resolved = themes_dir.resolve(strict=True)
            theme_files = list(resolved.glob("*.kdl"))
            if theme_files:
                findings.append(
                    _Finding(
                        "ok",
                        "vc-frame:themes",
                        f"{len(theme_files)} theme file(s) under {themes_dir}",
                    )
                )
            else:
                findings.append(
                    _Finding(
                        "warn",
                        "vc-frame:themes",
                        f"themes dir empty: {themes_dir}",
                    )
                )
        except OSError:
            findings.append(
                _Finding(
                    "fail",
                    "vc-frame:themes",
                    f"themes path does not resolve: {themes_dir}",
                )
            )
    else:
        findings.append(
            _Finding(
                "warn",
                "vc-frame:themes",
                f"themes view missing at {themes_dir}",
            )
        )

    # Host commands: every shipped KDL must match the available shell/clipboard.
    shell = resolve_pane_shell(path_env)
    clipboard = resolve_clipboard_command(path_env)
    layouts = view / "layouts"
    unresolved: list[str] = []
    kdl_files: list[Path] = []
    config_file = view / "config.kdl"
    if config_file.exists():
        kdl_files.append(config_file)
    if layouts.exists():
        try:
            kdl_files.extend(sorted(layouts.resolve().glob("*.kdl")))
        except OSError:
            pass
    for kdl_file in kdl_files:
        try:
            text = kdl_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if shell != "zsh" and any(
            token in text
            for token in (
                'command="zsh"',
                'default_shell "zsh"',
                "exec zsh -l",
                "exec /bin/zsh -l",
            )
        ):
            unresolved.append(f"{kdl_file.name}:zsh")
        if clipboard is None and (
            'copy_command "pbcopy"' in text or "pbcopy <" in text
        ):
            unresolved.append(f"{kdl_file.name}:pbcopy")
    if unresolved:
        remediation = (
            "dev checkout is intentionally raw; install the referenced host "
            "commands or unset VIBECRAFTED_PREFER_REPO_VC_FRAME and run "
            "`vibecrafted update`"
            if use_repo
            else "republish host-adapted config via `vibecrafted update`"
        )
        findings.append(
            _Finding(
                "warn",
                "vc-frame:pane-shell",
                f"unresolved host commands for shell={shell!r}, "
                f"clipboard={clipboard or 'internal'}: {', '.join(unresolved)}; "
                f"{remediation}",
            )
        )
    else:
        findings.append(
            _Finding(
                "ok",
                "vc-frame:pane-shell",
                f"host commands ok (shell={shell}, clipboard={clipboard or 'internal'})",
            )
        )

    # frontier zombies
    froot = frontier_root(home)
    zombies = list_dangling_frontier_links(froot)
    if zombies:
        findings.append(
            _Finding(
                "fail",
                "frontier:zombies",
                f"{len(zombies)} dangling link(s) under {froot} — "
                f"re-run install-frontier-config.sh or `vibecrafted update`",
            )
        )
    else:
        findings.append(
            _Finding(
                "ok",
                "frontier:zombies",
                f"no dangling frontier links under {froot}",
            )
        )

    # Operator scripts + Super/Cmd contract on both projections. The runtime
    # pins VC_FRAME_CONFIG_DIR to frontier first; a STALE-FILE composer there
    # shadows every install that only rewires ~/.config/vc-frame.
    frontier_cfg = froot / "vc-frame"
    for projection, label in (
        (view, "view"),
        (frontier_cfg, "frontier"),
    ):
        missing_scripts = [
            name
            for name in OPERATOR_SCRIPT_NAMES
            if name != "auto-theme.sh" and not (projection / name).exists()
        ]
        stale_scripts = [
            name
            for name in OPERATOR_SCRIPT_NAMES
            if (projection / name).is_file() and not (projection / name).is_symlink()
        ]
        if missing_scripts:
            findings.append(
                _Finding(
                    "fail",
                    f"vc-frame:operator-scripts:{label}",
                    f"missing {', '.join(missing_scripts)} under {projection} — "
                    f"{view_repair}",
                )
            )
        elif stale_scripts:
            findings.append(
                _Finding(
                    "fail",
                    f"vc-frame:operator-scripts:{label}",
                    f"STALE-FILE (not install-managed link) for "
                    f"{', '.join(stale_scripts)} under {projection} — "
                    f"{view_repair} (backs up and re-wires)",
                )
            )
        else:
            findings.append(
                _Finding(
                    "ok",
                    f"vc-frame:operator-scripts:{label}",
                    f"operator scripts projected under {projection}",
                )
            )

        cfg = projection / "config.kdl"
        if cfg.is_file() or cfg.is_symlink():
            try:
                text = cfg.read_text(encoding="utf-8")
            except OSError as exc:
                findings.append(
                    _Finding(
                        "fail",
                        f"vc-frame:key-contract:{label}",
                        f"cannot read {cfg}: {exc}",
                    )
                )
            else:
                kitty_on = (
                    "support_kitty_keyboard_protocol true" in text
                    or "support_kitty_keyboard_protocol true" in text
                )
                has_super = 'bind "Super' in text or 'bind "Super' in text
                if not kitty_on:
                    findings.append(
                        _Finding(
                            "fail",
                            f"vc-frame:key-contract:{label}",
                            f"{cfg} has support_kitty_keyboard_protocol off — "
                            "Super/Cmd chords will never reach keybinds; "
                            f"{view_repair}",
                        )
                    )
                elif not has_super:
                    findings.append(
                        _Finding(
                            "fail",
                            f"vc-frame:key-contract:{label}",
                            f"{cfg} enables kitty protocol but binds no Super/* "
                            f"chords — Cmd switcher/Composer are dead; {view_repair}",
                        )
                    )
                else:
                    findings.append(
                        _Finding(
                            "ok",
                            f"vc-frame:key-contract:{label}",
                            "kitty protocol on + Super/* binds present",
                        )
                    )

    if use_repo:
        findings.append(
            _Finding(
                "ok",
                "vc-frame:channel",
                "VIBECRAFTED_PREFER_REPO_VC_FRAME=1 (dev-checkout preferred)",
            )
        )
    return findings


_TRUTH_PATTERNS = ("config.kdl", "auto-theme.sh", "layouts/*.kdl", "themes/*.kdl")


def _hash_config_tree(root: Path) -> dict[str, str]:
    """sha256 map of the canonical vc-frame config files under one truth root."""
    hashes: dict[str, str] = {}
    if not root.is_dir():
        return hashes
    for pattern in _TRUTH_PATTERNS:
        for path in sorted(root.glob(pattern)):
            if not path.is_file():
                continue
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                continue
            hashes[str(path.relative_to(root))] = digest
    return hashes


def _diverged_files(left: dict[str, str], right: dict[str, str]) -> list[str]:
    """Return sorted filenames whose hash differs (or is missing) between the two maps."""
    return sorted(
        name for name in left.keys() | right.keys() if left.get(name) != right.get(name)
    )


def _vc_frame_truth_drift_findings(
    *,
    home: Path | None = None,
    tools_home: Path | None = None,
) -> list[_Finding]:
    """Content drift across the vc-frame config truths.

    The delivery checks prove the FORM of the view (symlink channels, dangling
    links). This proves the CONTENT: the published generation must agree with
    itself (package config/ vs package runtime/generated/), the dev checkout
    may run ahead of the store but never silently, and no projection link may
    resolve into a parked generation instead of vibecrafted-current.
    """
    findings: list[_Finding] = []
    current = tools_current_path(tools_home)
    package = current / "vibecrafted-core" / "vibecrafted_core"
    store_cfg = package / "config" / "vc-frame"
    generated = package / "runtime" / "generated" / "vc-frame"

    store_map = _hash_config_tree(store_cfg)
    generated_map = _hash_config_tree(generated)
    if store_map and generated_map:
        # generated/ is HOST-ADAPTED from config/ (pane-shell + clipboard
        # substitution in every kdl), so the raw trees legitimately differ on
        # any host whose adaptation differs from the shipped defaults (a
        # Linux box without pbcopy diverges on every layout). Compare against
        # a fresh materialization built by the same production code instead
        # of raw hashes — self-agreement modulo intended adaptation.
        expected_map = store_map
        try:
            import tempfile

            from .vc_frame_staging import (
                materialize_vc_frame_config,
                resolve_clipboard_command,
                resolve_pane_shell,
            )

            with tempfile.TemporaryDirectory(prefix="vc-doctor-truth-") as tmp:
                expected_root = Path(tmp) / "vc-frame"
                materialize_vc_frame_config(
                    store_cfg,
                    expected_root,
                    pane_shell=resolve_pane_shell(),
                    clipboard_command=resolve_clipboard_command(),
                )
                expected_map = _hash_config_tree(expected_root)
        except OSError:
            # Incomplete store tree — raw comparison still beats no signal.
            expected_map = store_map
        split = _diverged_files(expected_map, generated_map)
        if split:
            findings.append(
                _Finding(
                    "fail",
                    "vc-frame:truth",
                    "published generation disagrees with itself "
                    f"(config/ vs runtime/generated/): {', '.join(split[:6])}"
                    f"{' …' if len(split) > 6 else ''} — run `vibecrafted update`",
                )
            )
        else:
            findings.append(
                _Finding(
                    "ok",
                    "vc-frame:truth",
                    f"store truths agree ({len(store_map)} file(s) hashed)",
                )
            )

    checkout: Path | None = None
    try:
        from .frontier_assets import vc_frame_config_source

        checkout = vc_frame_config_source()
    except FileNotFoundError:
        checkout = None
    if checkout is not None and store_map:
        drift = _diverged_files(_hash_config_tree(checkout), store_map)
        if drift:
            findings.append(
                _Finding(
                    "warn",
                    "vc-frame:truth",
                    f"dev checkout differs from published store on {len(drift)} "
                    f"file(s): {', '.join(drift[:6])}"
                    f"{' …' if len(drift) > 6 else ''} — legal mid-development; "
                    "republish via `vibecrafted update` before trusting "
                    "env-less sessions",
                )
            )
        else:
            findings.append(
                _Finding("ok", "vc-frame:truth", "dev checkout matches published store")
            )

    tools_root = current.parent
    try:
        current_real = current.resolve(strict=True)
    except OSError:
        return findings
    projection_roots = (
        vc_frame_user_config_dir(home),
        frontier_root(home) / "vc-frame",
    )
    stale: list[Path] = []
    for root in projection_roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_symlink():
                continue
            try:
                target = path.resolve(strict=True)
            except OSError:
                continue  # dangling links are the delivery check's finding
            if target.is_relative_to(tools_root) and not target.is_relative_to(
                current_real
            ):
                stale.append(path)
    if stale:
        listed = ", ".join(str(path) for path in stale[:4])
        findings.append(
            _Finding(
                "fail",
                "vc-frame:truth",
                f"{len(stale)} projection link(s) resolve into a parked "
                f"generation instead of vibecrafted-current: {listed}"
                f"{' …' if len(stale) > 4 else ''} — re-run `vibecrafted update`",
            )
        )
    else:
        findings.append(
            _Finding(
                "ok",
                "vc-frame:truth",
                "all projection links resolve inside vibecrafted-current",
            )
        )
    return findings


def doctor_run(
    store_path: str | Path | None = None,
    state: Any | None = None,
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
