"""Local container execution environment for interactive Agent Workspaces.

The runtime-policy key stays ``local-vm`` for configuration compatibility, but
what runs is a persistent, per-project Docker/Colima *container* built from the
dev-container recipe that ships with every generation
(``vibecrafted_core/runtime/dev-container``). It is not a VM and not a security
boundary; the hardened Runtime Pack carrier (``vibecrafted-vm/Containerfile``)
is a separate product.

Ownership split:

* image identity is the recipe digest (``vibecrafted-dev:recipe-<digest>`` plus
  a label), never ``latest`` or a file mtime;
* the container belongs to the recipe's own Compose project (``vc-<slug>``, the
  name ``vibecrafted-vm/dev-up.sh`` uses), so agent history lives in that
  project's named volumes and survives ``down``/rebuild;
* this module never removes volumes, never mounts the host ``HOME`` and never
  prints credential values -- provider keys travel as ``docker exec -e NAME``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RUNTIME_KEY = "local-vm"
DISPLAY_NAME = "local container"
RECIPE_DIR = Path(__file__).resolve().parent / "runtime" / "dev-container"
# Everything the build context COPYs plus the compose file that runs it. The
# digest over these bytes is the image identity.
RECIPE_FILES = (
    "Dockerfile.dev",
    "compose.dev.yaml",
    "dev-entry.sh",
    "entry.sh",
    "zshrc.template",
)
RECIPE_LABEL = "io.vibecrafted.dev-container.recipe"
IMAGE_REPOSITORY = "vibecrafted-dev"
SERVICE = "dev"
WORKDIR = "/workspace"
RUN_STAGING_ROOT = "/root/.vibecrafted/agent-runs"
# Fleet provider -> binary the recipe installs (Dockerfile.dev). The
# contract test reads the recipe so this map cannot drift from it.
CONTAINER_PROVIDERS: dict[str, str] = {
    "claude": "claude",
    "codex": "codex",
    "kimi": "kimi",
}
ENGINE_TTL_SECONDS = 15.0
IMAGE_TTL_SECONDS = 15.0
_RUNNING_TIMEOUT_SECONDS = 60.0
_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_EXTRA_CLI_DIRS = (
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/Applications/Docker.app/Contents/Resources/bin",
)

Log = Callable[[str], None]


class ContainerError(RuntimeError):
    """A readable refusal from one preparation stage."""

    def __init__(self, message: str, *, stage: str) -> None:
        super().__init__(message)
        self.stage = stage


class ContainerCancelled(ContainerError):
    """The User interrupted a long preparation step."""

    def __init__(self, message: str = "local container preparation cancelled") -> None:
        super().__init__(message, stage="cancelled")


@dataclass(frozen=True)
class EngineStatus:
    available: bool
    cli: str = ""
    context: str = ""
    server_version: str = ""
    reason: str = ""


@dataclass(frozen=True)
class ContainerRecord:
    container_id: str
    name: str
    state: str
    image: str
    project: str
    workspace_source: str
    # Immutable provenance of what this container actually runs: the image ID
    # it was created from and the recipe label it inherited at creation. The
    # tag in ``image`` is mutable and can point elsewhere by now.
    image_id: str = ""
    recipe: str = ""


@dataclass(frozen=True)
class ContainerTarget:
    cli: str
    project: str
    container_id: str
    container_name: str
    image: str
    recipe_digest: str
    host_root: str
    engine_context: str = ""
    workdir: str = WORKDIR
    image_id: str = ""

    def receipt(self) -> dict[str, Any]:
        return {
            "environment": DISPLAY_NAME,
            "runtime_policy": RUNTIME_KEY,
            "engine_context": self.engine_context,
            "compose_project": self.project,
            "container_id": self.container_id,
            "container_name": self.container_name,
            "image": self.image,
            "image_id": self.image_id,
            "recipe_digest": self.recipe_digest,
            "host_root": self.host_root,
            "workdir": self.workdir,
        }


_engine_cache: dict[str, tuple[float, EngineStatus]] = {}
_image_cache: dict[str, tuple[float, str | None]] = {}


def _search_path(env: Mapping[str, str] | None) -> str:
    environ = os.environ if env is None else env
    entries = [
        item for item in str(environ.get("PATH") or "").split(os.pathsep) if item
    ]
    for extra in _EXTRA_CLI_DIRS:
        if extra not in entries:
            entries.append(extra)
    return os.pathsep.join(entries)


def docker_cli(env: Mapping[str, str] | None = None) -> str | None:
    """The container CLI this host offers: docker first, podman as fallback."""
    search = _search_path(env)
    return shutil.which("docker", path=search) or shutil.which("podman", path=search)


def _cli_env(env: Mapping[str, str] | None) -> dict[str, str]:
    child = dict(os.environ if env is None else env)
    child["PATH"] = _search_path(child)
    return child


def _run(
    argv: Sequence[str],
    env: Mapping[str, str] | None,
    *,
    timeout: float = 30.0,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        list(argv),
        env=_cli_env(env),
        input=input_bytes,
        capture_output=True,
        check=False,
        timeout=timeout,
    )


def _text(raw: bytes | str | None) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", "replace").strip()
    return raw.strip()


def _colima_profile(context: str) -> str:
    if context == "colima":
        return ""
    if context.startswith("colima-"):
        return context[len("colima-") :]
    return ""


def start_instruction(context: str) -> str:
    if context.startswith("colima"):
        profile = _colima_profile(context)
        suffix = f" --profile {profile}" if profile else ""
        return f"start it with `colima start{suffix}`"
    if context in {"desktop-linux", "default"}:
        return "start Docker Desktop (or run `colima start`)"
    return f"start the engine behind Docker context {context!r}"


def engine_status(
    env: Mapping[str, str] | None = None, *, refresh: bool = False
) -> EngineStatus:
    """Probe CLI, daemon and compose once per TTL; never fall back to the host."""
    cli = docker_cli(env)
    if cli is None:
        return EngineStatus(
            False,
            reason=(
                "Docker is not installed: install Colima (`brew install colima docker "
                "docker-compose`) or Docker Desktop, start it, then Launch again"
            ),
        )
    now = time.monotonic()
    cached = _engine_cache.get(cli)
    if not refresh and cached is not None and now - cached[0] < ENGINE_TTL_SECONDS:
        return cached[1]
    status = _probe_engine(cli, env)
    _engine_cache[cli] = (now, status)
    return status


def _probe_engine(cli: str, env: Mapping[str, str] | None) -> EngineStatus:
    context = ""
    try:
        shown = _run([cli, "context", "show"], env, timeout=5)
        if shown.returncode == 0:
            context = _text(shown.stdout)
    except (OSError, subprocess.TimeoutExpired):
        context = ""
    try:
        info = _run([cli, "info", "--format", "{{.ServerVersion}}"], env, timeout=8)
    except subprocess.TimeoutExpired:
        return EngineStatus(
            False,
            cli=cli,
            context=context,
            reason=(
                f"Docker daemon did not answer within 8s (context {context or 'unknown'}); "
                f"{start_instruction(context)}"
            ),
        )
    except OSError as exc:
        return EngineStatus(
            False, cli=cli, context=context, reason=f"cannot run {cli}: {exc}"
        )
    version = _text(info.stdout)
    if info.returncode != 0 or not version:
        return EngineStatus(
            False,
            cli=cli,
            context=context,
            reason=(
                f"Docker daemon is not running (context {context or 'unknown'}); "
                f"{start_instruction(context)}, then Launch again"
            ),
        )
    try:
        compose = _run([cli, "compose", "version", "--short"], env, timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        compose = None
    if compose is None or compose.returncode != 0:
        return EngineStatus(
            False,
            cli=cli,
            context=context,
            server_version=version,
            reason=(
                "docker compose is missing: install it (`brew install docker-compose`) "
                "and register it as a Docker CLI plugin"
            ),
        )
    return EngineStatus(True, cli=cli, context=context, server_version=version)


def recipe_dir() -> Path:
    """The shipped recipe directory, refusing an incomplete copy."""
    missing = [name for name in RECIPE_FILES if not (RECIPE_DIR / name).is_file()]
    if missing:
        raise ContainerError(
            "the local container recipe is incomplete in this runtime "
            f"({RECIPE_DIR}): missing {', '.join(missing)}",
            stage="recipe",
        )
    return RECIPE_DIR


def recipe_digest(directory: Path | None = None) -> str:
    """Content identity of the recipe: names and bytes of every recipe file."""
    root = directory or recipe_dir()
    digest = hashlib.sha256()
    for name in RECIPE_FILES:
        digest.update(name.encode("utf-8") + b"\0")
        digest.update((root / name).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def image_tag(digest: str) -> str:
    return f"{IMAGE_REPOSITORY}:recipe-{digest}"


def project_slug(root: Path) -> str:
    """Same slug ``vibecrafted-vm/dev-up.sh`` derives, so both entries share history."""
    slug = re.sub(r"[^a-z0-9]+", "-", root.name.lower()).strip("-")
    return slug or "workspace"


def project_candidates(root: Path) -> tuple[str, str]:
    """``vc-<slug>`` first; a root-qualified name when another repo owns it."""
    resolved = root.expanduser().resolve()
    slug = project_slug(resolved)
    qualifier = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:8]
    return f"vc-{slug}", f"vc-{slug}-{qualifier}"


def image_recipe(
    cli: str, tag: str, env: Mapping[str, str] | None = None, *, refresh: bool = False
) -> str | None:
    """Recipe label of a local image tag; ``None`` when the tag is absent."""
    now = time.monotonic()
    cached = _image_cache.get(tag)
    if not refresh and cached is not None and now - cached[0] < IMAGE_TTL_SECONDS:
        return cached[1]
    try:
        result = _run(
            [cli, "image", "inspect", "--format", "{{json .Config.Labels}}", tag],
            env,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    label: str | None = None
    if result.returncode == 0:
        try:
            labels = json.loads(_text(result.stdout) or "null") or {}
        except json.JSONDecodeError:
            labels = {}
        label = str(labels.get(RECIPE_LABEL) or "") if isinstance(labels, dict) else ""
    _image_cache[tag] = (now, label)
    return label


def container_capability(
    provider: str, env: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """Environment readiness for one provider, without building anything."""
    supported = ", ".join(sorted(CONTAINER_PROVIDERS))
    if provider not in CONTAINER_PROVIDERS:
        return {
            "available": False,
            "substrate": False,
            "reason": (
                f"{provider} is not installed in the local container recipe "
                f"(it carries {supported})"
            ),
        }
    try:
        directory = recipe_dir()
    except ContainerError as exc:
        return {"available": False, "substrate": False, "reason": str(exc)}
    status = engine_status(env)
    if not status.available:
        return {"available": False, "substrate": False, "reason": status.reason}
    tag = image_tag(recipe_digest(directory))
    ready = image_recipe(status.cli, tag, env) == recipe_digest(directory)
    return {
        "available": True,
        "substrate": True,
        "engine_context": status.context,
        "image": tag,
        "image_ready": ready,
        "reason": "",
    }


def _stream(
    argv: Sequence[str],
    env: Mapping[str, str] | None,
    log: Log,
    *,
    stage: str,
    failure: str,
) -> None:
    """Run one long step, relaying every output line; Ctrl-C stops it."""
    try:
        process = subprocess.Popen(
            list(argv),
            env=_cli_env(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except OSError as exc:
        raise ContainerError(f"{failure}: {exc}", stage=stage) from exc
    tail: list[str] = []
    try:
        assert process.stdout is not None
        for raw in process.stdout:
            line = raw.decode("utf-8", "replace").rstrip()
            if line:
                tail = [*tail[-19:], line]
            log(line)
        returncode = process.wait()
    except KeyboardInterrupt as exc:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        raise ContainerCancelled() from exc
    if returncode != 0:
        detail = "\n".join(tail[-6:])
        raise ContainerError(
            f"{failure} (exit {returncode})" + (f":\n{detail}" if detail else ""),
            stage=stage,
        )


def build_image(
    cli: str,
    directory: Path,
    tag: str,
    digest: str,
    env: Mapping[str, str] | None,
    log: Log,
) -> None:
    from . import __version__

    _stream(
        [
            cli,
            "build",
            "-f",
            str(directory / "Dockerfile.dev"),
            "-t",
            tag,
            "--label",
            f"{RECIPE_LABEL}={digest}",
            "--label",
            f"io.vibecrafted.dev-container.version={__version__}",
            str(directory),
        ],
        env,
        log,
        stage="build",
        failure=f"building {tag} failed",
    )
    _image_cache.pop(tag, None)


def find_project_container(
    cli: str, project: str, env: Mapping[str, str] | None = None
) -> ContainerRecord | None:
    """The recipe service container of one Compose project, if it exists."""
    listed = _run(
        [
            cli,
            "ps",
            "-a",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--filter",
            f"label=com.docker.compose.service={SERVICE}",
            "--format",
            "{{.ID}}",
        ],
        env,
        timeout=15,
    )
    ids = [line for line in _text(listed.stdout).splitlines() if line.strip()]
    if listed.returncode != 0 or not ids:
        return None
    inspected = _run([cli, "inspect", ids[0]], env, timeout=15)
    if inspected.returncode != 0:
        return None
    try:
        payload = json.loads(_text(inspected.stdout))[0]
    except (json.JSONDecodeError, IndexError, TypeError):
        return None
    source = ""
    for mount in payload.get("Mounts") or []:
        if isinstance(mount, dict) and mount.get("Destination") == WORKDIR:
            source = str(mount.get("Source") or "")
    state = payload.get("State") or {}
    config = payload.get("Config") or {}
    labels = config.get("Labels") or {}
    return ContainerRecord(
        container_id=str(payload.get("Id") or ids[0]),
        name=str(payload.get("Name") or "").lstrip("/"),
        state=str(state.get("Status") or ""),
        image=str(config.get("Image") or ""),
        project=project,
        workspace_source=source,
        image_id=str(payload.get("Image") or ""),
        recipe=str(labels.get(RECIPE_LABEL) or "") if isinstance(labels, dict) else "",
    )


def _same_path(left: str, right: Path) -> bool:
    try:
        return Path(left).resolve() == right.resolve()
    except (OSError, RuntimeError, ValueError):
        return False


def select_project(
    cli: str, root: Path, env: Mapping[str, str] | None = None
) -> tuple[str, ContainerRecord | None]:
    """The project that owns *root*; never reuse a container serving another path."""
    primary, qualified = project_candidates(root)
    first = find_project_container(cli, primary, env)
    if first is None or _same_path(first.workspace_source, root):
        return primary, first
    return qualified, find_project_container(cli, qualified, env)


def _host_sample(root: Path) -> list[str]:
    try:
        names = sorted(entry.name for entry in root.iterdir())
    except OSError:
        return []
    preferred = [name for name in (".git", "AGENTS.md", "README.md") if name in names]
    return preferred or names[:3]


def _visibility_error(root: Path, context: str) -> ContainerError:
    home = Path.home()
    if context.startswith("colima"):
        profile = _colima_profile(context)
        suffix = f" --profile {profile}" if profile else ""
        how = (
            f"Colima shares only {home} by default. Add this path as a mount "
            f"(`colima stop{suffix} && colima start{suffix} --mount {root}:w`, or "
            f"`mounts:` in ~/.colima/{profile or 'default'}/colima.yaml)"
        )
    else:
        how = "Share this path with the Docker VM (Docker Desktop: Settings → Resources → File sharing)"
    return ContainerError(
        f"The container cannot see {root}: its /workspace would be empty. {how}, "
        "then Launch again.",
        stage="mount",
    )


def _probe_visibility(
    cli: str, tag: str, root: Path, context: str, env: Mapping[str, str] | None
) -> None:
    """Prove the Docker VM shares *root* before any container mounts it."""
    sample = _host_sample(root)
    if not sample:
        return
    result = _run(
        [
            cli,
            "run",
            "--rm",
            "--entrypoint",
            "ls",
            "-v",
            f"{root}:/probe:ro",
            tag,
            "-A",
            "/probe",
        ],
        env,
        timeout=60,
    )
    seen = set(_text(result.stdout).splitlines())
    if result.returncode != 0 or not set(sample) <= seen:
        raise _visibility_error(root, context)


def _verify_running_visibility(
    cli: str,
    record: ContainerRecord,
    root: Path,
    context: str,
    env: Mapping[str, str] | None,
) -> None:
    sample = _host_sample(root)
    if not sample:
        return
    result = _run(
        [cli, "exec", record.container_id, "ls", "-A", WORKDIR], env, timeout=20
    )
    if result.returncode != 0:
        raise ContainerError(
            f"cannot read {WORKDIR} in {record.name}: {_text(result.stderr) or 'exec failed'}",
            stage="start",
        )
    if not set(sample) <= set(_text(result.stdout).splitlines()):
        raise _visibility_error(root, context)


def compose_up(
    cli: str,
    directory: Path,
    project: str,
    root: Path,
    tag: str,
    env: Mapping[str, str] | None,
    log: Log,
) -> None:
    compose_env = _cli_env(env)
    compose_env.update(
        {
            "VC_WORKSPACE_DIR": str(root),
            "VC_DEV_IMAGE": tag,
            # The host checkout is mounted live; a first-boot `uv sync` would
            # write a Linux .venv into the User's repository.
            "VC_DEV_SKIP_SYNC": "1",
        }
    )
    _stream(
        [
            cli,
            "compose",
            "-p",
            project,
            "-f",
            str(directory / "compose.dev.yaml"),
            "up",
            "-d",
            "--no-build",
            "--pull",
            "never",
        ],
        compose_env,
        log,
        stage="start",
        failure=f"starting container project {project} failed",
    )


def _container_state(
    cli: str, container_id: str, env: Mapping[str, str] | None
) -> dict[str, Any]:
    result = _run(
        [cli, "inspect", "--format", "{{json .State}}", container_id], env, timeout=15
    )
    if result.returncode != 0:
        return {}
    try:
        state = json.loads(_text(result.stdout) or "{}")
    except json.JSONDecodeError:
        return {}
    return state if isinstance(state, dict) else {}


def _log_tail(cli: str, container_id: str, env: Mapping[str, str] | None) -> str:
    try:
        result = _run([cli, "logs", "--tail", "8", container_id], env, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return "\n".join(
        line
        for line in (_text(result.stdout) + "\n" + _text(result.stderr)).splitlines()
        if line
    )[-1200:]


def _wait_running(
    cli: str, project: str, env: Mapping[str, str] | None
) -> ContainerRecord:
    """Running *and stable*: the same start on two polls, not a restart loop."""
    deadline = time.monotonic() + _RUNNING_TIMEOUT_SECONDS
    previous_start = ""
    while True:
        record = find_project_container(cli, project, env)
        state = _container_state(cli, record.container_id, env) if record else {}
        started = str(state.get("StartedAt") or "")
        if (
            record is not None
            and state.get("Running") is True
            and not state.get("Restarting")
            and started
            and started == previous_start
        ):
            return record
        previous_start = started if state.get("Running") else ""
        if time.monotonic() >= deadline:
            if record is None:
                raise ContainerError(
                    f"container for project {project} was not created", stage="start"
                )
            tail = _log_tail(cli, record.container_id, env)
            raise ContainerError(
                f"container {record.name} does not stay running "
                f"(status {state.get('Status') or record.state}, exit {state.get('ExitCode')})"
                + (f"; last log lines:\n{tail}" if tail else ""),
                stage="start",
            )
        time.sleep(1.5)


def ensure_container(
    root: str | os.PathLike[str],
    *,
    env: Mapping[str, str] | None = None,
    log: Log = print,
) -> ContainerTarget:
    """Make this project's container ready: image by recipe, then start, then verify."""
    host_root = Path(root).expanduser().resolve()
    if not host_root.is_dir():
        raise ContainerError(
            f"project folder does not exist: {host_root}", stage="project"
        )
    status = engine_status(env, refresh=True)
    if not status.available:
        raise ContainerError(status.reason, stage="engine")
    cli = status.cli
    directory = recipe_dir()
    digest = recipe_digest(directory)
    tag = image_tag(digest)
    # Decide about a running container before any long build: an upgrade
    # never ends Agents already working in it.
    project, record = select_project(cli, host_root, env)
    reuse = record is not None and record.state == "running"
    if reuse:
        assert record is not None
        # Only the container's own creation-time recipe label certifies what
        # runs: a tag may have been retagged or rebuilt since.
        running_recipe = record.recipe or "unknown"
        if (
            not _same_path(record.workspace_source, host_root)
            or record.recipe != digest
        ):
            # Compose would recreate it and end every Agent working inside;
            # the volumes would survive, the running processes would not.
            raise ContainerError(
                f"container {record.name} is running {record.image} "
                f"({record.image_id or 'image id unknown'}, recipe {running_recipe}); "
                f"this runtime ships recipe {digest}. Running Agents are never replaced: "
                "finish or close the Agents in it, then stop exactly this container "
                f"with `{Path(cli).name} stop {record.container_id}` (history volumes "
                "are kept) and Launch again.",
                stage="drift",
            )
        log(
            f"[1/3] Image {record.image} ({record.image_id or 'image id unknown'}, "
            f"recipe {running_recipe}, Docker context {status.context or 'default'}) "
            "— already serving this project"
        )
        log(f"[2/3] Container project {project}")
        log("      already running with this recipe")
    else:
        log(f"[1/3] Image {tag} (Docker context {status.context or 'default'})")
        if image_recipe(cli, tag, env, refresh=True) != digest:
            log(
                "      not built for this recipe yet — building runtime/dev-container/"
                "Dockerfile.dev (first build takes several minutes; Ctrl-C cancels)"
            )
            build_image(cli, directory, tag, digest, env, log)
            if image_recipe(cli, tag, env, refresh=True) != digest:
                raise ContainerError(
                    f"built image {tag} does not carry recipe label {digest}",
                    stage="build",
                )
        log("      ready")
        log(f"[2/3] Container project {project}")
        _probe_visibility(cli, tag, host_root, status.context, env)
        compose_up(cli, directory, project, host_root, tag, env, log)
    record = _wait_running(cli, project, env)
    if record.recipe != digest:
        raise ContainerError(
            f"container {record.name} started from {record.image_id or record.image} "
            f"whose recipe label is {record.recipe or 'missing'}, not {digest}",
            stage="start",
        )
    if not _same_path(record.workspace_source, host_root):
        raise ContainerError(
            f"container {record.name} mounts {record.workspace_source or 'nothing'} "
            f"at {WORKDIR}, not {host_root}",
            stage="mount",
        )
    _verify_running_visibility(cli, record, host_root, status.context, env)
    log(f"[3/3] {host_root} → {record.name}:{WORKDIR}")
    return ContainerTarget(
        cli=cli,
        project=project,
        container_id=record.container_id,
        container_name=record.name,
        # The image actually serving the project, never assumed from the tag.
        image=record.image,
        image_id=record.image_id,
        recipe_digest=record.recipe,
        host_root=str(host_root),
        engine_context=status.context,
    )


def _run_dir(run_id: str) -> str:
    if not _SAFE_RUN_ID.fullmatch(run_id):
        raise ContainerError(
            f"unsafe run id for container staging: {run_id!r}", stage="stage"
        )
    return f"{RUN_STAGING_ROOT}/{run_id}"


def stage_text(
    target: ContainerTarget,
    run_id: str,
    name: str,
    text: str,
    env: Mapping[str, str] | None = None,
) -> str:
    """Write one private run file inside the container; bytes never touch argv."""
    if "/" in name or name in {"", ".", ".."}:
        raise ContainerError(f"unsafe staged file name: {name!r}", stage="stage")
    directory = _run_dir(run_id)
    result = _run(
        [
            target.cli,
            "exec",
            "-i",
            target.container_id,
            "sh",
            "-c",
            'umask 077 && mkdir -p "$1" && cat > "$1/$2" && touch "$1/.started"',
            "sh",
            directory,
            name,
        ],
        env,
        timeout=30,
        input_bytes=text.encode("utf-8"),
    )
    if result.returncode != 0:
        raise ContainerError(
            f"cannot stage {name} into {target.container_name}: {_text(result.stderr)}",
            stage="stage",
        )
    return f"{directory}/{name}"


def credential_names(provider: str, env: Mapping[str, str]) -> tuple[str, ...]:
    """Provider credential variables present in *env*; names only, never values."""
    from .command_bridge_onboarding import PROVIDER_ENV_KEYS

    return tuple(name for name in PROVIDER_ENV_KEYS.get(provider, ()) if env.get(name))


def exec_argv(
    target: ContainerTarget,
    argv: Sequence[str],
    *,
    run_id: str,
    pass_names: Sequence[str] = (),
    extra_env: Mapping[str, str] | None = None,
) -> list[str]:
    """``docker exec -it`` into the project's container, cwd at the mounted root.

    The provider records its in-container process identity first (pid, kernel
    starttime, boot id): a closed tab ends only the host-side ``docker exec``
    client, never the process it started, so the owner needs a verifiable
    identity to end the provider with its tab -- and nothing else.
    """
    if not argv:
        raise ContainerError("container command must not be empty", stage="exec")
    command = [target.cli, "exec", "-it", "-w", target.workdir]
    for name in ("TERM", "COLORTERM", *pass_names):
        command.extend(["-e", name])
    for key, value in (extra_env or {}).items():
        command.extend(["-e", f"{key}={value}"])
    command.append(target.container_id)
    command.extend(["sh", "-c", _RECORD_SCRIPT, "sh", _run_dir(run_id), *argv])
    return command


PROC_ROOT = "/proc"
IDENTITY_FILE = "provider.identity"
# Same identity shape as process_control.process_start_token: the kernel
# starttime (field 22 of /proc/<pid>/stat, read after the last ")") plus the
# kernel boot id. A PID alone is not an identity: run directories live in a
# named volume and outlive container restarts, where PIDs start over -- the
# starttime catches that reuse. The boot id belongs to the Docker VM kernel
# (unchanged by `docker restart`) and catches a VM reboot, where starttime
# values could repeat.
_STARTTIME = "sed 's/.*) //' \"$P/$1/stat\" 2>/dev/null | cut -d' ' -f20"
_RECORD_SCRIPT = (
    "d=$1; shift; P=/proc; "
    f'st=$(set -- "$$"; {_STARTTIME}); '
    'b=$(cat "$P/sys/kernel/random/boot_id"); '
    '[ -n "$st" ] && [ -n "$b" ] && '
    'printf "%s start:%s boot:%s\\n" "$$" "$st" "$b" > "$d/provider.identity.tmp" && '
    'mv "$d/provider.identity.tmp" "$d/provider.identity" && exec "$@"'
)
# $1 run dir, $2 grace seconds, $3 proc root. Every signal is preceded by a
# fresh identity check; a stale, reused or vanished identity is never
# signalled, and the record is cleared so it can never become actionable.
_TEARDOWN_SCRIPT = (
    'd=$1; g=$2; P=${3:-/proc}; f="$d/provider.identity"; '
    '[ -f "$f" ] || { echo none; exit 0; }; '
    'read -r pid st boot < "$f" || { rm -f "$f"; echo stale; exit 0; }; '
    f'same() {{ [ "boot:$(cat "$P/sys/kernel/random/boot_id" 2>/dev/null)" = "$boot" ] && '
    f'[ "start:$({_STARTTIME})" = "$st" ]; }}; '
    'if ! same "$pid"; then rm -f "$f"; '
    'if [ -e "$P/$pid" ]; then echo stale; else echo exited; fi; exit 0; fi; '
    # `exec -t` makes the provider a session leader: signal its group so
    # wrapper launchers (node -> native binary) end together.
    'kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null; i=0; '
    'while same "$pid" && [ "$i" -lt "$g" ]; do sleep 1; i=$((i+1)); done; '
    'if same "$pid"; then kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null; '
    'rm -f "$f"; echo killed; '
    'elif [ -e "$P/$pid" ]; then rm -f "$f"; echo stale; '
    'else rm -f "$f"; echo terminated; fi'
)
# $1 staging root, $2 proc root: run ids whose recorded provider is the same
# live process now; stale records are cleared, never reported.
_SWEEP_SCRIPT = (
    'P=${2:-/proc}; b="boot:$(cat "$P/sys/kernel/random/boot_id" 2>/dev/null)"; '
    'for f in "$1"/*/provider.identity; do [ -f "$f" ] || continue; '
    'read -r pid st boot < "$f" || { rm -f "$f"; continue; }; '
    f'if [ "$boot" = "$b" ] && [ "start:$(set -- "$pid"; {_STARTTIME})" = "$st" ]; then '
    'basename "$(dirname "$f")"; else rm -f "$f"; fi; done; true'
)


def terminate_provider_detached(
    target: ContainerTarget,
    run_id: str,
    env: Mapping[str, str] | None = None,
    *,
    grace_seconds: int = 3,
) -> bool:
    """Start the teardown in its own session so it outlives a killed tab.

    Frame ends a closed tab's process group right after the hangup; a
    teardown run by that owner can die half way. This one cannot.
    """
    try:
        subprocess.Popen(
            [
                target.cli,
                "exec",
                target.container_id,
                "sh",
                "-c",
                _TEARDOWN_SCRIPT,
                "sh",
                _run_dir(run_id),
                str(grace_seconds),
                PROC_ROOT,
            ],
            env=_cli_env(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except (OSError, ContainerError):
        return False
    return True


def terminate_provider(
    target: ContainerTarget,
    run_id: str,
    env: Mapping[str, str] | None = None,
    *,
    grace_seconds: int = 3,
) -> str:
    """End the provider this run started inside the container, if still alive."""
    try:
        result = _run(
            [
                target.cli,
                "exec",
                target.container_id,
                "sh",
                "-c",
                _TEARDOWN_SCRIPT,
                "sh",
                _run_dir(run_id),
                str(grace_seconds),
                PROC_ROOT,
            ],
            env,
            timeout=grace_seconds + 20,
        )
    except (OSError, subprocess.TimeoutExpired, ContainerError) as exc:
        return f"unknown:{exc}"
    return _text(result.stdout) or ("exited" if result.returncode == 0 else "unknown")


def sweep_orphan_providers(
    target: ContainerTarget,
    *,
    is_live: Callable[[str], bool],
    env: Mapping[str, str] | None = None,
) -> list[str]:
    """End providers whose host owner is gone (a tab killed before teardown).

    Only records whose identity still names the same live process are
    candidates; the teardown re-verifies that identity before every signal.
    """
    try:
        result = _run(
            [
                target.cli,
                "exec",
                target.container_id,
                "sh",
                "-c",
                _SWEEP_SCRIPT,
                "sh",
                RUN_STAGING_ROOT,
                PROC_ROOT,
            ],
            env,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    ended: list[str] = []
    for run_id in _text(result.stdout).splitlines():
        run_id = run_id.strip()
        if not _SAFE_RUN_ID.fullmatch(run_id) or is_live(run_id):
            continue
        if terminate_provider(target, run_id, env, grace_seconds=2) in {
            "terminated",
            "killed",
        }:
            ended.append(run_id)
    return ended


_CODEX_PROBE = r"""
import json, os, sys
run_dir, workdir = sys.argv[1], sys.argv[2]
marker = os.path.join(run_dir, ".started")
try:
    since = os.stat(marker).st_mtime
except OSError:
    sys.exit(0)
root = os.path.join(os.path.expanduser("~"), ".codex", "sessions")
found = set()
for base, _dirs, files in os.walk(root):
    for name in files:
        if not (name.startswith("rollout-") and name.endswith(".jsonl")):
            continue
        path = os.path.join(base, name)
        try:
            if os.stat(path).st_mtime < since:
                continue
            with open(path, encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if '"session_meta"' not in line:
                        continue
                    payload = json.loads(line).get("payload") or {}
                    if os.path.realpath(str(payload.get("cwd") or "")) == os.path.realpath(workdir):
                        found.add(str(payload.get("id") or ""))
                    break
        except (OSError, ValueError):
            continue
found.discard("")
if len(found) == 1:
    print(found.pop())
"""


def prove_native_session(
    target: ContainerTarget,
    provider: str,
    run_id: str,
    *,
    requested: str = "",
    env: Mapping[str, str] | None = None,
) -> str:
    """Provider-owned evidence of the conversation this run created, or ``""``."""
    try:
        if provider == "claude" and requested:
            result = _run(
                [
                    target.cli,
                    "exec",
                    target.container_id,
                    "sh",
                    "-c",
                    'ls /root/.claude/projects/*/"$1".jsonl >/dev/null 2>&1',
                    "sh",
                    requested,
                ],
                env,
                timeout=20,
            )
            return requested if result.returncode == 0 else ""
        if provider == "codex":
            result = _run(
                [
                    target.cli,
                    "exec",
                    target.container_id,
                    "python3",
                    "-c",
                    _CODEX_PROBE,
                    _run_dir(run_id),
                    target.workdir,
                ],
                env,
                timeout=30,
            )
            identity = _text(result.stdout)
            return identity if result.returncode == 0 and identity else ""
    except (OSError, subprocess.TimeoutExpired, ContainerError):
        return ""
    return ""
