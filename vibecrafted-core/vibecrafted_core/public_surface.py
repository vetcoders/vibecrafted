"""Public-surface floor for the vc-dou → vc-hydrate → vc-release pipeline.

One probe, three stages. It answers two questions for any configured product:

1. Does every public surface that speaks a version say the same version?
2. Can a stranger install the thing today from what we publish?

``vc-dou`` owns the live comparison and is red when the world disagrees.
``vc-hydrate`` owns the checkout a packager can actually edit: version
projections, the README install section, command pins, and brand spelling.
``vc-release`` owns the ship gate. A GitHub Release for the version being
published may still be absent (this stage is what creates it); every other
required surface must already agree, and ``--require-release`` refuses to
call the ship done until the Release object exists. A tag with no Release
is not a finished release.

Product config lives in ``packaging/public-surfaces/<product>.toml``.
The probe reads registries and pages. It does not tag, publish, or deploy.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SCHEMA = 1
USER_AGENT = "vibecrafted-public-surface-floor"
SEMVER_RE = re.compile(
    r"v?(?P<core>(?:0|[1-9]\d*)\.\d+\.\d+)(?P<pre>[-+][0-9A-Za-z.-]+)?",
    re.IGNORECASE,
)
INSTALL_HEADING_RE = re.compile(
    r"^(#{1,3})\s+(install(?:ation)?|quick start)\b",
    re.IGNORECASE,
)
COMMAND_RE = re.compile(
    r"\b(curl\b|wget\b|npm\s+i(?:nstall)?\b|pnpm\s+|yarn\s+|cargo\s+install\b|"
    r"pipx?\s+install\b|uv\s+tool\s+install\b|brew\s+install\b|go\s+install\b)",
    re.IGNORECASE,
)
PIN_RE = re.compile(r"(?<![\w.])v?(\d+\.\d+\.\d+)(?![\w.])", re.IGNORECASE)
FENCE_RE = re.compile(r"^```", re.MULTILINE)
KIND_DEFAULTS: dict[str, dict[str, Any]] = {
    "github_release": {"dou": "fail", "release": "fail", "install": True},
    "orphan_tags": {"dou": "fail", "release": "warn", "install": False},
    "npm": {"dou": "fail", "release": "fail", "install": True},
    "crates": {"dou": "fail", "release": "fail", "install": True},
    "pypi": {"dou": "fail", "release": "fail", "install": True},
    "homebrew": {"dou": "warn", "release": "warn", "install": True},
    "json_url": {"dou": "fail", "release": "fail", "install": True},
    "html_regex": {"dou": "fail", "release": "warn", "install": False},
    "text_url": {"dou": "fail", "release": "fail", "install": False},
    "remote_readme": {"dou": "fail", "release": "fail", "install": True},
    "brand_html": {"dou": "warn", "release": "warn", "install": False},
}
SEVERITIES = {"fail", "warn", "ignore"}


class FloorConfigError(ValueError):
    """The product file is missing a field or names an unknown check."""


@dataclass
class FetchResult:
    status: int
    body: str
    error: str = ""
    headers: Mapping[str, str] = field(default_factory=dict)


Fetch = Callable[[str, Mapping[str, str] | None], FetchResult]


@dataclass
class Finding:
    level: str
    surface: str
    message: str
    observed: str = ""
    expected: str = ""
    blocks_install: bool = False

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FloorReport:
    stage: str
    product: str
    reference: str
    findings: list[Finding]
    install_cta: str

    @property
    def fail_count(self) -> int:
        return sum(1 for item in self.findings if item.level == "fail")

    @property
    def warn_count(self) -> int:
        return sum(1 for item in self.findings if item.level == "warn")

    @property
    def failed(self) -> bool:
        return self.fail_count > 0

    def exit_code(self) -> int:
        return 1 if self.failed else 0

    def to_json(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "product": self.product,
            "reference": self.reference,
            "install_cta": self.install_cta,
            "fails": self.fail_count,
            "warns": self.warn_count,
            "floor": "FAIL" if self.failed else "WARN" if self.warn_count else "PASS",
            "findings": [item.to_json() for item in self.findings],
        }


def normalize_version(raw: str) -> str:
    """Return the comparable ``X.Y.Z`` core, ignoring a ``v`` prefix and ``+build``.

    A pre-release suffix (``-rc.1``) is kept, so ``1.2.3-rc.1`` does not
    compare equal to ``1.2.3``. ``0.6.0+44f395c2`` compares equal to ``0.6.0``.
    """
    text = raw.strip().strip("\"'")
    match = SEMVER_RE.search(text)
    if match is None:
        return text
    core = match.group("core")
    pre = match.group("pre") or ""
    if pre.startswith("+"):
        pre = ""
    return f"{core}{pre}"


def catalog_dir(repo: Path | None = None) -> Path:
    """Directory of per-product floor files.

    Prefers ``<repo>/packaging/public-surfaces`` and otherwise the catalog
    shipped beside this module in a source checkout.
    """
    candidates: list[Path] = []
    if repo is not None:
        candidates.append(repo / "packaging" / "public-surfaces")
    candidates.append(
        Path(__file__).resolve().parents[2] / "packaging" / "public-surfaces"
    )
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


def load_product(path: Path) -> dict[str, Any]:
    """Load and lightly validate one product TOML file."""
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise FloorConfigError(f"cannot read {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise FloorConfigError(f"{path} must be a TOML table")
    schema = raw.get("schema", SCHEMA)
    if schema != SCHEMA:
        raise FloorConfigError(f"{path} schema {schema} is not {SCHEMA}")
    product = str(raw.get("product") or path.stem).strip()
    if not product:
        raise FloorConfigError(f"{path} is missing product")
    expect = str(raw.get("expect") or "").strip()
    if not expect:
        raise FloorConfigError(f"{path} is missing expect")
    surfaces = raw.get("surface") or raw.get("surfaces") or []
    if not isinstance(surfaces, list):
        raise FloorConfigError(f"{path} surface entries must be an array")
    normalized: list[dict[str, Any]] = []
    for index, surface in enumerate(surfaces, start=1):
        if not isinstance(surface, dict):
            raise FloorConfigError(f"{path} surface {index} is not a table")
        kind = str(surface.get("kind") or "").strip()
        if kind not in KIND_DEFAULTS:
            raise FloorConfigError(f"{path} surface {index} has unknown kind {kind!r}")
        defaults = KIND_DEFAULTS[kind]
        item = dict(surface)
        item["kind"] = kind
        item["id"] = str(surface.get("id") or f"{kind}-{index}")
        for key in ("dou", "release"):
            value = str(item.get(key) or defaults[key])
            if value not in SEVERITIES:
                raise FloorConfigError(
                    f"{path} surface {item['id']} {key}={value!r} "
                    "must be fail, warn, or ignore"
                )
            item[key] = value
        if "install" not in item:
            item["install"] = bool(defaults["install"])
        else:
            item["install"] = bool(item["install"])
        normalized.append(item)
    raw = dict(raw)
    raw["product"] = product
    raw["expect"] = expect
    raw["surface"] = normalized
    raw["_path"] = str(path)
    return raw


def urllib_fetch(
    url: str,
    headers: Mapping[str, str] | None = None,
    *,
    timeout: float = 20,
) -> FetchResult:
    """GET ``url``. HTTP errors become a result; they do not raise."""
    request_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(url, headers=request_headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read(2_000_000)
            return FetchResult(
                int(response.status),
                payload.decode("utf-8", "replace"),
                "",
                {key: value for key, value in response.headers.items()},
            )
    except urllib.error.HTTPError as exc:
        detail = exc.read(4000).decode("utf-8", "replace")
        return FetchResult(int(exc.code), detail, f"HTTP {exc.code}")
    except Exception as exc:  # noqa: BLE001 — one probe must not kill the report
        return FetchResult(0, "", f"{type(exc).__name__}: {exc}")


def render_report(report: FloorReport) -> str:
    """Plain-text report. One surface per line, then the install verdict."""
    lines = [
        (
            f"public-surface floor  stage={report.stage}  "
            f"product={report.product}  reference={report.reference or '-'}"
        )
    ]
    for item in report.findings:
        observed = f"  observed={item.observed}" if item.observed else ""
        expected = f"  expected={item.expected}" if item.expected else ""
        lines.append(
            f"{item.level.upper():<8} {item.surface}{observed}{expected}  {item.message}"
        )
    lines.append(f"install_cta: {report.install_cta}")
    if report.failed:
        verdict = "FAIL"
    elif report.warn_count:
        verdict = "WARN"
    else:
        verdict = "PASS"
    lines.append(
        f"floor: {verdict}  fails={report.fail_count}  warns={report.warn_count}"
    )
    return "\n".join(lines) + "\n"


def run_floor(
    stage: str,
    config: Mapping[str, Any],
    *,
    repo: Path | None = None,
    fetch: Fetch | None = None,
    acknowledge: Sequence[str] = (),
    allow_missing_release: Sequence[str] = (),
    require_release: bool = False,
    timeout: float = 20,
) -> FloorReport:
    """Run one stage of the floor. ``fetch`` defaults to live HTTP."""
    if stage not in {"dou", "hydrate", "release"}:
        raise FloorConfigError(f"unknown stage {stage!r}")
    getter = fetch or (
        lambda url, headers=None: urllib_fetch(url, headers, timeout=timeout)
    )
    findings: list[Finding] = []
    reference = _resolve_reference(
        stage, config, repo=repo, fetch=getter, findings=findings
    )
    local = _local_checkout_applies(config, repo)
    if stage in {"dou", "hydrate", "release"} and local and repo is not None:
        _check_local(config, repo, reference, findings)
    elif stage == "hydrate":
        findings.append(
            Finding(
                "fail",
                "checkout",
                "no product checkout to hydrate "
                f"(marker {config.get('local_root_file') or 'unset'} is absent)",
            )
        )
    if stage in {"dou", "release"}:
        _check_live(
            stage,
            config,
            reference=reference,
            fetch=getter,
            findings=findings,
            allow_missing_release=set(allow_missing_release),
            require_release=require_release,
        )
    acknowledged = set(acknowledge)
    adjusted: list[Finding] = []
    for item in findings:
        if item.level == "fail" and item.surface in acknowledged:
            adjusted.append(
                Finding(
                    "warn",
                    item.surface,
                    f"acknowledged: {item.message}",
                    item.observed,
                    item.expected,
                    item.blocks_install,
                )
            )
        else:
            adjusted.append(item)
    install_cta = _install_cta(config, adjusted, reference, local)
    if stage == "hydrate" and install_cta == "yes":
        install_cta = "local"
    return FloorReport(
        stage=stage,
        product=str(config.get("product") or ""),
        reference=reference,
        findings=adjusted,
        install_cta=install_cta,
    )


def floor_main(stage: str, argv: Sequence[str] | None = None) -> int:
    """CLI entry used by ``vc-dou --floor``, ``vc-hydrate --floor``, ``vc-release --floor``."""
    parser = argparse.ArgumentParser(
        prog=f"vc-{stage} --floor",
        description=(
            "Compare a product's public version surfaces. "
            "Read-only: does not tag, publish, or deploy."
        ),
    )
    parser.add_argument("--product", default="", help="catalog name, e.g. loctree")
    parser.add_argument("--config", default="", help="path to a product TOML file")
    parser.add_argument("--repo", default="", help="product checkout (default: cwd)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--acknowledge", action="append", default=[])
    parser.add_argument(
        "--allow-missing-release",
        action="append",
        default=[],
        help="tag the publisher is about to turn into a GitHub Release",
    )
    parser.add_argument(
        "--require-release",
        action="store_true",
        help="fail when the reference version has no GitHub Release yet",
    )
    parser.add_argument("--timeout", type=float, default=20)
    args = parser.parse_args(list(argv or []))
    repo = Path(args.repo).resolve() if args.repo else Path.cwd()
    try:
        if args.config:
            config = load_product(Path(args.config))
        else:
            product = args.product or "vibecrafted"
            path = catalog_dir(repo) / f"{product}.toml"
            if not path.is_file():
                raise FloorConfigError(
                    f"no floor config for {product!r} at {path}. "
                    "Pass --config or add packaging/public-surfaces/"
                    f"{product}.toml"
                )
            config = load_product(path)
    except FloorConfigError as exc:
        print(f"public-surface floor: {exc}", file=sys.stderr)
        return 2
    try:
        report = run_floor(
            stage,
            config,
            repo=repo,
            acknowledge=args.acknowledge,
            allow_missing_release=args.allow_missing_release,
            require_release=args.require_release,
            timeout=args.timeout,
        )
    except FloorConfigError as exc:
        print(f"public-surface floor: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report.to_json(), indent=2))
    else:
        print(render_report(report), end="")
    return report.exit_code()


def _resolve_reference(
    stage: str,
    config: Mapping[str, Any],
    *,
    repo: Path | None,
    fetch: Fetch,
    findings: list[Finding],
) -> str:
    expect = str(config.get("expect") or "")
    if expect == "agree":
        return ""
    if expect.startswith("literal:"):
        return normalize_version(expect.split(":", 1)[1])
    if expect.startswith("file:"):
        relative = expect.split(":", 1)[1]
        if repo is None:
            findings.append(
                Finding("fail", "expect", f"{relative} requested but no repo was given")
            )
            return ""
        path = repo / relative
        if not path.is_file():
            findings.append(Finding("fail", "expect", f"missing {path}"))
            return ""
        return normalize_version(path.read_text(encoding="utf-8"))
    if expect.startswith("surface:"):
        target = expect.split(":", 1)[1]
        if stage == "hydrate":
            return ""
        for surface in config.get("surface") or []:
            if surface.get("id") == target:
                observed, error = _observe_version(surface, fetch)
                if error or not observed:
                    findings.append(
                        Finding(
                            "fail" if stage == "release" else "warn",
                            target,
                            f"cannot use {target} as the reference ({error or 'no version'})",
                        )
                    )
                    return ""
                return observed
        raise FloorConfigError(f"expect names unknown surface {target!r}")
    raise FloorConfigError(
        f"expect must be file:, literal:, surface:, or agree (got {expect!r})"
    )


def _local_checkout_applies(config: Mapping[str, Any], repo: Path | None) -> bool:
    marker = str(config.get("local_root_file") or "").strip()
    if repo is None or not marker:
        return False
    return (repo / marker).is_file()


def _check_local(
    config: Mapping[str, Any],
    repo: Path,
    reference: str,
    findings: list[Finding],
) -> None:
    if config.get("version_bump"):
        _check_version_bump(repo, findings)
    for projection in config.get("projection") or []:
        if isinstance(projection, dict):
            _check_projection(repo, projection, reference, findings)
    readme = config.get("readme") or {}
    if isinstance(readme, dict) and readme.get("path"):
        _check_readme_file(
            repo / str(readme["path"]),
            readme,
            reference,
            findings,
            surface="readme",
        )
    brand = config.get("brand") or {}
    if isinstance(brand, dict):
        _check_brand_tree(repo, brand, findings)


def _check_version_bump(repo: Path, findings: list[Finding]) -> None:
    script = repo / "scripts" / "version_bump.py"
    version_file = repo / "VERSION"
    if not script.is_file():
        findings.append(Finding("fail", "version_bump", f"missing {script}"))
        return
    completed = subprocess.run(
        [sys.executable, str(script), "--check", "--file", str(version_file)],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if completed.returncode == 0:
        findings.append(
            Finding(
                "ok",
                "version_bump",
                "in-repo projections match VERSION",
            )
        )
        return
    detail = (completed.stderr or completed.stdout).strip().splitlines()
    message = detail[-1] if detail else f"exit {completed.returncode}"
    findings.append(Finding("fail", "version_bump", message, blocks_install=True))


def _check_projection(
    repo: Path,
    projection: Mapping[str, Any],
    reference: str,
    findings: list[Finding],
) -> None:
    surface_id = str(projection.get("id") or projection.get("path") or "projection")
    path = repo / str(projection.get("path") or "")
    if not path.is_file():
        findings.append(
            Finding("fail", surface_id, f"missing {path}", blocks_install=True)
        )
        return
    text = path.read_text(encoding="utf-8")
    kind = str(projection.get("kind") or "file")
    try:
        observed_raw = _projection_value(kind, text, projection)
    except FloorConfigError as exc:
        findings.append(Finding("fail", surface_id, str(exc), blocks_install=True))
        return
    observed = normalize_version(observed_raw)
    if reference and observed != reference:
        findings.append(
            Finding(
                "fail",
                surface_id,
                f"{path.name} {kind} disagrees with the reference",
                observed,
                reference,
                True,
            )
        )
    else:
        findings.append(
            Finding("ok", surface_id, f"{path.name} matches", observed, reference)
        )


def _projection_value(kind: str, text: str, projection: Mapping[str, Any]) -> str:
    if kind == "file":
        return text.strip()
    if kind == "pyproject":
        return _toml_table_version(text, "project")
    if kind == "cargo":
        return _toml_table_version(text, "package")
    if kind == "json_pointer":
        value = _pointer(json.loads(text), str(projection.get("pointer") or "/version"))
        if value is None:
            raise FloorConfigError(f"pointer {projection.get('pointer')} missing")
        return str(value)
    if kind == "readme_badge":
        match = re.search(r"badge/version-(\d+\.\d+\.\d+)-", text)
        if match is None:
            match = re.search(r"Version (\d+\.\d+\.\d+)", text)
        if match is None:
            raise FloorConfigError("README badge version missing")
        return match.group(1)
    raise FloorConfigError(f"unknown projection kind {kind!r}")


def _toml_table_version(text: str, table: str) -> str:
    in_table = False
    pattern = re.compile(r'^\s*version\s*=\s*"([^"]+)"')
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_table = stripped == f"[{table}]"
            continue
        if in_table and (match := pattern.match(line)):
            return match.group(1)
    raise FloorConfigError(f"no [{table}] version")


def _pointer(document: Any, pointer: str) -> Any:
    current = document
    for part in pointer.strip("/").split("/"):
        if not part:
            continue
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _check_readme_file(
    path: Path,
    readme: Mapping[str, Any],
    reference: str,
    findings: list[Finding],
    *,
    surface: str,
) -> None:
    if not path.is_file():
        level = "fail" if readme.get("require_install", True) else "warn"
        findings.append(
            Finding(level, surface, f"missing {path}", blocks_install=level == "fail")
        )
        return
    _check_readme_text(
        path.read_text(encoding="utf-8"),
        readme,
        reference,
        findings,
        surface=surface,
        label=path.name,
    )


def _check_readme_text(
    text: str,
    readme: Mapping[str, Any],
    reference: str,
    findings: list[Finding],
    *,
    surface: str,
    label: str,
) -> None:
    section = _install_section(text)
    require = bool(readme.get("require_install", True))
    version_pattern = str(readme.get("version_pattern") or "")
    if version_pattern:
        match = re.search(version_pattern, text)
        if match is None:
            findings.append(
                Finding("warn", f"{surface}_version", f"{label} version pattern missed")
            )
        else:
            observed = normalize_version(
                match.groupdict().get("version") or match.group(1)
            )
            _compare_version(
                f"{surface}_version",
                observed,
                reference,
                findings,
                noun=f"{label} stated version",
                mismatch_level="fail",
                blocks_install=True,
            )
    if section is None:
        if require:
            findings.append(
                Finding(
                    "fail",
                    surface,
                    f"{label} has no Install section a stranger can follow",
                    blocks_install=True,
                )
            )
        return
    if COMMAND_RE.search(section) is None:
        findings.append(
            Finding(
                "fail",
                surface,
                f"{label} Install section has no install command",
                blocks_install=True,
            )
        )
    else:
        findings.append(Finding("ok", surface, f"{label} has an install command"))
    command_level = str(readme.get("pin_commands") or "fail")
    prose_level = str(readme.get("pin_prose") or "warn")
    command_pins, prose_pins = _pins(section)
    if not reference:
        if command_pins:
            findings.append(
                Finding(
                    "ok",
                    f"{surface}_pin",
                    "install command pins " + ", ".join(sorted(command_pins)),
                    ",".join(sorted(command_pins)),
                )
            )
        return
    _report_pins(
        f"{surface}_pin",
        command_pins,
        reference,
        findings,
        command_level,
        f"{label} install command pins a different version",
        blocks_install=True,
    )
    _report_pins(
        f"{surface}_prose",
        prose_pins - command_pins,
        reference,
        findings,
        prose_level,
        f"{label} install prose names a different version",
        blocks_install=False,
    )


def _install_section(text: str) -> str | None:
    lines = text.splitlines()
    start: int | None = None
    level = 0
    for index, line in enumerate(lines):
        match = INSTALL_HEADING_RE.match(line)
        if match:
            start = index
            level = len(match.group(1))
            break
    if start is None:
        return None
    end = len(lines)
    for index in range(start + 1, len(lines)):
        match = re.match(r"^(#{1,3})\s+\S", lines[index])
        if match and len(match.group(1)) <= level:
            end = index
            break
    return "\n".join(lines[start:end])


def _pins(section: str) -> tuple[set[str], set[str]]:
    """Versions in fenced commands, and versions anywhere in the section."""
    command_pins: set[str] = set()
    in_fence = False
    fence_lines: list[str] = []
    for line in section.splitlines():
        if FENCE_RE.match(line):
            if in_fence:
                command_pins.update(PIN_RE.findall("\n".join(fence_lines)))
                fence_lines = []
            in_fence = not in_fence
            continue
        if in_fence:
            fence_lines.append(line)
        elif COMMAND_RE.search(line):
            command_pins.update(PIN_RE.findall(line))
    if fence_lines:
        command_pins.update(PIN_RE.findall("\n".join(fence_lines)))
    prose_pins = set(PIN_RE.findall(section))
    return {normalize_version(pin) for pin in command_pins}, {
        normalize_version(pin) for pin in prose_pins
    }


def _report_pins(
    surface: str,
    pins: set[str],
    reference: str,
    findings: list[Finding],
    level: str,
    message: str,
    *,
    blocks_install: bool,
) -> None:
    if level == "ignore":
        return
    drifted = sorted(pin for pin in pins if pin and pin != reference)
    if not drifted:
        return
    findings.append(
        Finding(
            level if level in {"fail", "warn"} else "warn",
            surface,
            f"{message}: {', '.join(drifted)}",
            ",".join(drifted),
            reference,
            blocks_install and level == "fail",
        )
    )


def _check_brand_tree(
    repo: Path, brand: Mapping[str, Any], findings: list[Finding]
) -> None:
    forbidden = [str(item) for item in brand.get("forbidden") or []]
    globs = [str(item) for item in brand.get("repo_globs") or []]
    if not forbidden or not globs:
        return
    files: list[Path] = []
    for pattern in globs:
        files.extend(path for path in repo.glob(pattern) if path.is_file())
    if not files:
        findings.append(
            Finding(
                "warn", "brand_repo", "brand scan matched no files; check repo_globs"
            )
        )
        return
    hits: list[str] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for word in forbidden:
            count = len(
                re.findall(rf"(?<![A-Za-z]){re.escape(word)}(?![A-Za-z])", text)
            )
            if count:
                hits.append(f"{path.relative_to(repo)} x{count} {word}")
    if hits:
        findings.append(
            Finding(
                "fail",
                "brand_repo",
                "forbidden brand spelling in the checkout: " + "; ".join(hits[:12]),
                blocks_install=False,
            )
        )
    else:
        canonical = str(brand.get("canonical") or "")
        findings.append(
            Finding(
                "ok",
                "brand_repo",
                f"scanned {len(files)} files; canonical form {canonical or 'unset'}",
            )
        )


def _check_live(
    stage: str,
    config: Mapping[str, Any],
    *,
    reference: str,
    fetch: Fetch,
    findings: list[Finding],
    allow_missing_release: set[str],
    require_release: bool,
) -> None:
    observed_versions: list[tuple[str, str, bool]] = []
    for surface in config.get("surface") or []:
        kind = surface["kind"]
        severity = surface["dou"] if stage == "dou" else surface["release"]
        if severity == "ignore":
            continue
        if kind == "orphan_tags":
            _check_orphan_tags(
                stage,
                surface,
                fetch,
                findings,
                allow_missing_release,
                severity,
            )
            continue
        if kind == "brand_html":
            _check_brand_html(surface, fetch, findings, severity)
            continue
        if kind == "remote_readme":
            _check_remote_readme(surface, fetch, findings, reference, severity)
            continue
        observed, error = _observe_version(surface, fetch)
        install = bool(surface.get("install"))
        if error or not observed:
            level = "fail" if stage == "release" and severity == "fail" else "warn"
            if severity == "warn":
                level = "warn"
            findings.append(
                Finding(
                    level,
                    surface["id"],
                    f"unproven, not a green pass ({error or 'no version'})",
                    blocks_install=install,
                )
            )
            continue
        observed_versions.append((surface["id"], observed, install))
        if not reference:
            findings.append(
                Finding("ok", surface["id"], "read", observed, blocks_install=False)
            )
            continue
        mismatch_level = severity
        if (
            kind == "github_release"
            and stage == "release"
            and not require_release
            and observed != reference
        ):
            mismatch_level = "warn"
            message = (
                "latest GitHub Release is not this version yet. "
                "A tag without a Release does not finish vc-release; "
                "re-run with --require-release after the Release exists"
            )
        else:
            message = "disagrees with the reference"
        if observed == reference:
            findings.append(
                Finding("ok", surface["id"], "matches", observed, reference)
            )
        else:
            findings.append(
                Finding(
                    mismatch_level,
                    surface["id"],
                    message,
                    observed,
                    reference,
                    install,
                )
            )
    if not reference:
        seen = {surface for surface, _version, _install in observed_versions}
        for item in findings:
            if item.surface in seen or not item.observed or item.level != "ok":
                continue
            if item.surface.endswith("_pin"):
                for pin in item.observed.split(","):
                    if pin:
                        observed_versions.append((item.surface, pin, True))
            elif item.surface.endswith("_version"):
                observed_versions.append((item.surface, item.observed, True))
        _consensus(observed_versions, findings)


def _consensus(
    observed: list[tuple[str, str, bool]],
    findings: list[Finding],
) -> None:
    versions = {version for _surface, version, _install in observed if version}
    if len(versions) <= 1:
        only = next(iter(versions), "")
        findings.append(
            Finding(
                "ok", "consensus", f"live surfaces agree on {only or 'nothing'}", only
            )
        )
        return
    detail = ", ".join(
        f"{surface}={version}" for surface, version, _install in observed
    )
    blocks = any(
        install for _surface, version, install in observed if version in versions
    )
    findings.append(
        Finding(
            "fail",
            "consensus",
            f"public surfaces disagree: {detail}",
            ",".join(sorted(versions)),
            "",
            blocks,
        )
    )


def _compare_version(
    surface: str,
    observed: str,
    reference: str,
    findings: list[Finding],
    *,
    noun: str,
    mismatch_level: str,
    blocks_install: bool,
) -> None:
    if not reference:
        findings.append(Finding("ok", surface, f"{noun} observed", observed))
        return
    if observed == reference:
        findings.append(Finding("ok", surface, f"{noun} matches", observed, reference))
        return
    findings.append(
        Finding(
            mismatch_level,
            surface,
            f"{noun} disagrees",
            observed,
            reference,
            blocks_install and mismatch_level == "fail",
        )
    )


def _check_orphan_tags(
    stage: str,
    surface: Mapping[str, Any],
    fetch: Fetch,
    findings: list[Finding],
    allow_missing_release: set[str],
    severity: str,
) -> None:
    repo = str(surface.get("repo") or "")
    tags_result = _github_get(fetch, f"/repos/{repo}/tags?per_page=100")
    releases_result = _github_get(fetch, f"/repos/{repo}/releases?per_page=100")
    if tags_result.status != 200 or releases_result.status != 200:
        level = "fail" if stage == "release" and severity == "fail" else "warn"
        findings.append(
            Finding(
                level,
                surface["id"],
                "unproven, not a green pass "
                f"(tags HTTP {tags_result.status}, releases HTTP {releases_result.status})",
            )
        )
        return
    try:
        tags = json.loads(tags_result.body)
        releases = json.loads(releases_result.body)
    except json.JSONDecodeError as exc:
        findings.append(
            Finding("warn", surface["id"], f"unproven, not a green pass ({exc})")
        )
        return
    release_tags = {
        normalize_version(str(item.get("tag_name") or ""))
        for item in releases
        if isinstance(item, dict)
    }
    allowed = {normalize_version(tag) for tag in allow_missing_release}
    orphans: list[str] = []
    for item in tags:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if not re.fullmatch(r"v?\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", name):
            continue
        normalized = normalize_version(name)
        if normalized in release_tags or normalized in allowed:
            continue
        orphans.append(name)
    if not orphans:
        findings.append(
            Finding("ok", surface["id"], f"every scanned tag on {repo} has a Release")
        )
        return
    shown = ", ".join(orphans[:12])
    extra = f" (+{len(orphans) - 12} more)" if len(orphans) > 12 else ""
    findings.append(
        Finding(
            severity,
            surface["id"],
            f"{len(orphans)} tag(s) on {repo} have no GitHub Release: {shown}{extra}",
            str(len(orphans)),
        )
    )


def _check_brand_html(
    surface: Mapping[str, Any],
    fetch: Fetch,
    findings: list[Finding],
    severity: str,
) -> None:
    url = str(surface.get("url") or "")
    result = fetch(url, {"Accept": "text/html"})
    if result.status != 200:
        findings.append(
            Finding(
                "warn",
                surface["id"],
                f"unproven, not a green pass ({result.error or 'HTTP ' + str(result.status)})",
            )
        )
        return
    forbidden = [str(item) for item in surface.get("forbidden") or ["VetCoders"]]
    hits = []
    for word in forbidden:
        count = len(
            re.findall(rf"(?<![A-Za-z]){re.escape(word)}(?![A-Za-z])", result.body)
        )
        if count:
            hits.append(f"{word} x{count}")
    if hits:
        findings.append(
            Finding(
                severity,
                surface["id"],
                f"forbidden brand spelling on {url}: {', '.join(hits)}",
            )
        )
    else:
        findings.append(
            Finding("ok", surface["id"], f"no forbidden brand spelling on {url}")
        )


def _check_remote_readme(
    surface: Mapping[str, Any],
    fetch: Fetch,
    findings: list[Finding],
    reference: str,
    severity: str,
) -> None:
    url = str(surface.get("url") or "")
    result = fetch(url, {"Accept": "text/plain"})
    if result.status != 200:
        level = "fail" if severity == "fail" else "warn"
        findings.append(
            Finding(
                level,
                surface["id"],
                f"unproven, not a green pass ({result.error or result.status})",
                blocks_install=level == "fail",
            )
        )
        return
    before = len(findings)
    _check_readme_text(
        result.body,
        surface,
        reference,
        findings,
        surface=str(surface["id"]),
        label=url,
    )
    if severity == "warn":
        for index in range(before, len(findings)):
            item = findings[index]
            if item.level == "fail":
                findings[index] = Finding(
                    "warn",
                    item.surface,
                    item.message,
                    item.observed,
                    item.expected,
                    False,
                )


def _observe_version(
    surface: Mapping[str, Any],
    fetch: Fetch,
) -> tuple[str, str]:
    kind = surface["kind"]
    if kind == "npm":
        package = str(surface.get("package") or "")
        url = "https://registry.npmjs.org/" + urllib.parse.quote(package, safe="")
        result = fetch(url, {"Accept": "application/json"})
        return _json_version(result, "/dist-tags/latest")
    if kind == "crates":
        name = str(surface.get("package") or "")
        result = fetch(
            f"https://crates.io/api/v1/crates/{urllib.parse.quote(name, safe='')}",
            {"Accept": "application/json"},
        )
        return _json_version(result, "/crate/max_stable_version")
    if kind == "pypi":
        name = str(surface.get("package") or "")
        result = fetch(
            f"https://pypi.org/pypi/{urllib.parse.quote(name, safe='')}/json",
            {"Accept": "application/json"},
        )
        return _json_version(result, "/info/version")
    if kind == "github_release":
        result = _github_get(fetch, f"/repos/{surface.get('repo')}/releases/latest")
        if result.status == 404:
            return "", "no GitHub Release"
        return _json_version(result, "/tag_name")
    if kind in {"json_url", "homebrew", "html_regex", "text_url"}:
        url = str(surface.get("url") or "")
        accept = "application/json" if kind == "json_url" else "text/plain"
        result = fetch(url, {"Accept": accept})
        if result.status != 200:
            return "", result.error or f"HTTP {result.status}"
        if kind == "json_url":
            return _json_version(result, str(surface.get("pointer") or "/version"))
        if kind == "homebrew":
            match = re.search(r'version\s+"([^"]+)"', result.body)
            if match is None:
                return "", "formula has no version"
            return normalize_version(match.group(1)), ""
        pattern = str(surface.get("pattern") or "")
        if not pattern:
            return "", "missing pattern"
        match = re.search(pattern, result.body)
        if match is None:
            return "", "pattern missed"
        raw = match.groupdict().get("version") or match.group(1)
        return normalize_version(raw), ""
    return "", f"kind {kind} has no version"


def _json_version(result: FetchResult, pointer: str) -> tuple[str, str]:
    if result.status != 200:
        return "", result.error or f"HTTP {result.status}"
    try:
        document = json.loads(result.body)
    except json.JSONDecodeError as exc:
        return "", f"invalid JSON ({exc})"
    value = _pointer(document, pointer)
    if value is None or value == "":
        return "", f"pointer {pointer} missing"
    return normalize_version(str(value)), ""


def _github_get(fetch: Fetch, path: str) -> FetchResult:
    return fetch(
        "https://api.github.com" + path,
        {"Accept": "application/vnd.github+json"},
    )


def _install_cta(
    config: Mapping[str, Any],
    findings: list[Finding],
    reference: str,
    local: bool,
) -> str:
    """``yes`` only when a stranger can install without being lied to.

    Requires an install section when the product asks for one and a checkout
    is present, and zero install-blocking findings. Disagreement on any
    surface marked ``install`` blocks the call to action.
    """
    readme = config.get("readme") or {}
    readme_required = (
        local and isinstance(readme, dict) and readme.get("require_install", True)
    )
    if readme_required and any(
        item.surface == "readme" and item.level == "fail" for item in findings
    ):
        return "no"
    if any(item.blocks_install and item.level in {"fail", "warn"} for item in findings):
        return "no"
    if str(config.get("expect")) == "agree" and any(
        item.surface == "consensus" and item.level == "fail" for item in findings
    ):
        return "no"
    if not reference and str(config.get("expect")) != "agree":
        return "no"
    return "yes"


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m vibecrafted_core.public_surface <stage>``."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in {"dou", "hydrate", "release"}:
        print(
            "usage: python -m vibecrafted_core.public_surface "
            "{dou|hydrate|release} [--product NAME] [--config PATH]",
            file=sys.stderr,
        )
        return 2
    return floor_main(args[0], args[1:])


if __name__ == "__main__":
    raise SystemExit(main())
