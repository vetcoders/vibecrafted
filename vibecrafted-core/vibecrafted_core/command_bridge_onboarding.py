"""W1-03 — one-time host preparation gate for the command bridge.

Mission R08: an unprepared host must configure exactly one provider before the
command bridge admits work; completion means readiness only — no agent spawn
and no generative (paid) model request at the execution boundary. The
completion record is durable: a second client or a UI restart reads the same
record and never resets it. A later auth failure does not lock the operator
out of Home or a live conversation — only auth-requiring actions are blocked
while the probe finds no configured provider.

Secrets policy: probes are structural (file presence, env-var presence). No
secret value is ever read into the record, returned, or logged — only signal
labels such as ``file:.claude/.credentials.json`` or ``env:ANTHROPIC_API_KEY``.

The durable write reuses ``control_plane._write_json_durable`` (atomic tmp +
fsync + rename) instead of growing a second durability path.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import sys
import tempfile
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from vibecrafted_core.control_plane import _write_json_durable, control_plane_home

RECORD_VERSION = 1
RECORD_OWNER = "vibecrafted-core/command-bridge-onboarding"
RECORD_FILENAME = "command_bridge_onboarding.json"
IN_PROGRESS_FILENAME = "command_bridge_onboarding.in-progress.json"

SURFACE_HOME = "home"
SURFACE_CONVERSATION = "conversation"
SURFACE_AUTH_ACTION = "auth_action"
SURFACES = (SURFACE_HOME, SURFACE_CONVERSATION, SURFACE_AUTH_ACTION)

REASON_READY = "ready"
REASON_ONBOARDING_REQUIRED = "onboarding_required"
REASON_AUTH_EXPIRED = "auth_expired"
REASON_RECORD_INCOMPATIBLE = "record_incompatible"

# Known provider setup signals. File paths are relative to the user's home and
# mirror the fleet config map (vc-hello fleet_scan); env names mirror the
# secret keys the spawn path already treats as provider authority. Presence
# only — values are never inspected.
PROVIDER_AUTH_FILES: dict[str, tuple[str, ...]] = {
    "claude": (".claude/.credentials.json",),
    "codex": (".codex/auth.json",),
    "grok": (".grok/config.toml",),
    "kimi": (".kimi-code/config.toml",),
    # Copilot credentials may live in the OS credential store; the config file
    # is a setup signal only, not a claim that an auth token was verified.
    "copilot": (".copilot/config.json",),
}
PROVIDER_ENV_KEYS: dict[str, tuple[str, ...]] = {
    "claude": ("ANTHROPIC_API_KEY",),
    "codex": ("OPENAI_API_KEY", "OPENAI_API_KEY_CODEX"),
    "grok": ("XAI_API_KEY", "GROK_API_KEY"),
    "kimi": ("KIMI_API_KEY", "MOONSHOT_API_KEY"),
    "copilot": ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"),
}
PROVIDERS = tuple(PROVIDER_AUTH_FILES)


class OnboardingError(RuntimeError):
    """Base class for command-bridge onboarding failures."""


class UnknownProvider(OnboardingError):
    """Raised when a provider name is outside the known fleet."""


class ProviderNotConfigured(OnboardingError):
    """Raised when completion is attempted without a configured provider."""


class OnboardingRecordIncompatible(OnboardingError):
    """Existing completion record belongs to another owner/version.

    Recovery contract (W1-03): an incompatible owner of the completion state
    is never overwritten and no second record is created; the caller must
    surface a proposal to the integrator instead.
    """


@dataclass(frozen=True)
class ProviderProbe:
    """Structural auth signal for one provider; never carries secret values."""

    provider: str
    configured: bool
    evidence: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class OnboardingStatus:
    """Read model of the durable completion record."""

    state: str  # "pending" | "in_progress" | "ready" | "incompatible"
    provider: str | None = None
    record: dict[str, Any] | None = None


@dataclass(frozen=True)
class Admission:
    """Gate decision for one command-bridge surface."""

    surface: str
    allowed: bool
    reason: str
    state: str


def _user_home(user_home: Path | None) -> Path:
    return user_home if user_home is not None else Path.home()


def record_path(home: Path | None = None) -> Path:
    """Durable completion record location under the control-plane home."""
    root = home if home is not None else control_plane_home()
    return root / RECORD_FILENAME


def in_progress_path(home: Path | None = None) -> Path:
    root = home if home is not None else control_plane_home()
    return root / IN_PROGRESS_FILENAME


def probe_provider(
    provider: str,
    *,
    user_home: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> ProviderProbe:
    """Structurally probe whether one provider is configured on this host."""
    if provider not in PROVIDERS:
        raise UnknownProvider(
            f"unknown provider {provider!r} (known: {', '.join(PROVIDERS)})"
        )
    environ = os.environ if env is None else env
    home = _user_home(user_home)
    evidence: list[str] = []
    for relative in PROVIDER_AUTH_FILES[provider]:
        if (home / relative).is_file():
            evidence.append(f"file:{relative}")
    for name in PROVIDER_ENV_KEYS[provider]:
        if environ.get(name):
            evidence.append(f"env:{name}")
    return ProviderProbe(
        provider=provider, configured=bool(evidence), evidence=tuple(evidence)
    )


def configured_providers(
    *,
    user_home: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> list[str]:
    """All fleet providers with at least one structural auth signal."""
    return [
        provider
        for provider in PROVIDERS
        if probe_provider(provider, user_home=user_home, env=env).configured
    ]


def load_record(home: Path | None = None) -> dict[str, Any] | None:
    """Load the completion record, refusing foreign or future versions.

    Returns ``None`` when no record exists. Raises
    ``OnboardingRecordIncompatible`` when a record exists but was written by
    another owner or an unknown record version — the record is left untouched.
    """
    path = record_path(home)
    if not path.is_file():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OnboardingRecordIncompatible(
            f"unreadable onboarding record at {path}: {exc}"
        ) from exc
    if (
        not isinstance(record, dict)
        or record.get("record_version") != RECORD_VERSION
        or record.get("owner") != RECORD_OWNER
    ):
        raise OnboardingRecordIncompatible(
            f"onboarding record at {path} has an incompatible owner or version; "
            "refusing to replace it — surface a proposal to the integrator"
        )
    return record


def status(home: Path | None = None) -> OnboardingStatus:
    """Current onboarding state for this host."""
    try:
        record = load_record(home)
    except OnboardingRecordIncompatible:
        return OnboardingStatus(state="incompatible")
    if record is not None:
        return OnboardingStatus(
            state="ready", provider=record.get("provider"), record=record
        )
    if in_progress_path(home).is_file():
        return OnboardingStatus(state="in_progress")
    return OnboardingStatus(state="pending")


def begin(provider: str | None = None, *, home: Path | None = None) -> Path:
    """Mark preparation as started. The marker never counts as completion."""
    if provider is not None and provider not in PROVIDERS:
        raise UnknownProvider(
            f"unknown provider {provider!r} (known: {', '.join(PROVIDERS)})"
        )
    marker = in_progress_path(home)
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "owner": RECORD_OWNER,
        "record_version": RECORD_VERSION,
        "provider": provider,
        "started_at": datetime.now(UTC).isoformat(),
    }
    marker.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return marker


def cancel(*, home: Path | None = None) -> None:
    """Abort preparation: drop the in-progress marker, never write completion."""
    try:
        in_progress_path(home).unlink()
    except FileNotFoundError:
        pass


def complete(
    provider: str | None = None,
    *,
    home: Path | None = None,
    user_home: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Record readiness after exactly one configured provider is proven.

    Pure local state transition: no subprocess, no network, no model request.
    Idempotent for an existing compatible record; refuses (without writing)
    when no provider is configured or a foreign record owns the state.
    """
    existing = load_record(home)
    if existing is not None:
        return existing

    if provider is None:
        configured = configured_providers(user_home=user_home, env=env)
        if not configured:
            raise ProviderNotConfigured(
                "host preparation requires one configured provider; none of "
                f"{', '.join(PROVIDERS)} shows an auth signal"
            )
        provider = configured[0]

    probe = probe_provider(provider, user_home=user_home, env=env)
    if not probe.configured:
        raise ProviderNotConfigured(
            f"provider {provider!r} shows no auth signal; refusing to record "
            "a false completion"
        )

    record = {
        "record_version": RECORD_VERSION,
        "owner": RECORD_OWNER,
        "provider": probe.provider,
        "completed_at": datetime.now(UTC).isoformat(),
        "evidence": list(probe.evidence),
    }
    _write_json_durable(record_path(home), record)
    cancel(home=home)
    return record


def admit(
    surface: str,
    *,
    home: Path | None = None,
    user_home: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Admission:
    """Gate one command-bridge surface against the onboarding state.

    - pending / in_progress: every surface is refused with
      ``onboarding_required`` — first preparation is mandatory.
    - incompatible: fail closed with ``record_incompatible``.
    - ready: Home and live conversations stay open even when provider auth
      later expires; only auth-requiring actions re-probe the host and are
      refused with ``auth_expired`` while no provider is configured.
    """
    if surface not in SURFACES:
        raise ValueError(f"unknown surface {surface!r} (known: {', '.join(SURFACES)})")
    current = status(home)
    if current.state == "incompatible":
        return Admission(surface, False, REASON_RECORD_INCOMPATIBLE, current.state)
    if current.state != "ready":
        return Admission(surface, False, REASON_ONBOARDING_REQUIRED, current.state)
    if surface in (SURFACE_HOME, SURFACE_CONVERSATION):
        return Admission(surface, True, REASON_READY, current.state)
    if configured_providers(user_home=user_home, env=env):
        return Admission(surface, True, REASON_READY, current.state)
    return Admission(surface, False, REASON_AUTH_EXPIRED, current.state)


# ─── ZenEntry Typography & Monaspace Variant Helpers ────────────────────────

DEFAULT_PROPOSED_FONT_FAMILY = "Monaspace Xenon NF"
FALLBACK_FONT_FAMILY = "Spot Mono"
FONT_GLYPH_PREVIEW = "+  x  >  ❯  ▸  0O1lI  -> !=="

MONASPACE_FONT_VARIANTS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "Monaspace Xenon NF",
        "Slab-serif monospace (default proposal)",
        ("MonaspaceXenonNF", "Monaspace Xenon NF"),
    ),
    (
        "Monaspace Argon NF",
        "Humanist sans-serif monospace",
        ("MonaspaceArgonNF", "Monaspace Argon NF"),
    ),
    (
        "Monaspace Neon NF",
        "Neo-grotesque monospace",
        ("MonaspaceNeonNF", "Monaspace Neon NF"),
    ),
    (
        "Monaspace Radon NF",
        "Handwriting script monospace",
        ("MonaspaceRadonNF", "Monaspace Radon NF"),
    ),
    (
        "Monaspace Krypton NF",
        "Mechanical geometric monospace",
        ("MonaspaceKryptonNF", "Monaspace Krypton NF"),
    ),
)
MONASPACE_FAMILIES: tuple[str, ...] = tuple(item[0] for item in MONASPACE_FONT_VARIANTS)

_FONT_EXTENSIONS = {".ttf", ".otf", ".ttc"}
_FONT_SECTIONS = ("[font.normal]", "[font.bold]", "[font.italic]")
_DEFAULT_FONT_POLICY_SEED = """\
[font]
size = 19.5

[font.offset]
x = -3
y = -8

[font.normal]
family = "Spot Mono"
style = "Regular"

[font.bold]
family = "Spot Mono"
style = "Regular"

[font.italic]
family = "Spot Mono"
style = "Regular"
"""


class FontNotAvailableError(OnboardingError):
    """Raised when a new selection is not an available fixed-width family."""


@dataclass(frozen=True)
class FontVariantStatus:
    family: str
    label: str
    description: str
    font_file_present: bool
    font_file_path: str = ""
    active: bool = False
    preserved_custom: bool = False
    selectable: bool = True
    status_label: str = ""
    face_styles: tuple[str, str, str] = ("Regular", "Bold", "Italic")
    catalogue_source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "label": self.label,
            "description": self.description,
            "font_file_present": self.font_file_present,
            "font_file_path": self.font_file_path,
            "active": self.active,
            "preserved_custom": self.preserved_custom,
            "selectable": self.selectable,
            "status_label": self.status_label,
            "face_styles": self.face_styles,
            "catalogue_source": self.catalogue_source,
        }


def terminal_policy_path(
    env: Mapping[str, str] | None = None,
    *,
    home: Path | None = None,
) -> Path:
    """Resolve ``${XDG_CONFIG_HOME:-$HOME/.config}/vibecrafted/terminal-policy.toml``."""
    source_env = os.environ if env is None else env
    xdg = str(source_env.get("XDG_CONFIG_HOME") or "").strip()
    if xdg:
        config_base = Path(xdg).expanduser()
    else:
        resolved_home = Path(home).expanduser() if home is not None else Path.home()
        config_base = resolved_home / ".config"
    return config_base / "vibecrafted" / "terminal-policy.toml"


def default_font_dirs(*, home: Path | None = None) -> tuple[Path, Path]:
    """Return user and system font directories without executing any subprocess."""
    resolved_home = Path(home).expanduser() if home is not None else Path.home()
    return (resolved_home / "Library" / "Fonts", Path("/Library/Fonts"))


def _normalize_font_token(value: str) -> str:
    return re.sub(r"[\s_\-]+", "", value).lower()


def _find_font_file(tokens: Sequence[str], font_dirs: Sequence[Path]) -> Path | None:
    normalized_tokens = tuple(
        _normalize_font_token(token) for token in tokens if token.strip()
    )
    if not normalized_tokens:
        return None
    for directory in font_dirs:
        try:
            entries = sorted(
                Path(directory).iterdir(), key=lambda item: item.name.lower()
            )
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.suffix.lower() not in _FONT_EXTENSIONS:
                    continue
                if not entry.is_file():
                    continue
            except OSError:
                continue
            normalized_name = _normalize_font_token(entry.stem)
            if any(token in normalized_name for token in normalized_tokens):
                return entry
    return None


def _normalize_toml_section(line: str) -> str | None:
    match = re.match(r"^\s*\[([^\]]+)\]\s*(?:#.*)?$", line)
    if not match:
        return None
    inner = match.group(1).strip()
    parts = [p.strip().strip("\"'").strip() for p in re.split(r"\s*\.\s*", inner)]
    return f"[{'.'.join(parts)}]"


def _extract_family_from_toml_text(raw: str) -> str | None:
    try:
        parsed = tomllib.loads(raw)
        if isinstance(parsed, dict):
            font_table = parsed.get("font")
            if isinstance(font_table, dict):
                normal = font_table.get("normal")
                if isinstance(normal, dict):
                    family = normal.get("family")
                    if isinstance(family, str) and family.strip():
                        return family.strip()
                for subkey in ("bold", "italic"):
                    sub = font_table.get(subkey)
                    if isinstance(sub, dict):
                        family = sub.get("family")
                        if isinstance(family, str) and family.strip():
                            return family.strip()
    except (tomllib.TOMLDecodeError, ValueError):
        pass
    in_font_section = False
    for line in raw.splitlines():
        normalized = _normalize_toml_section(line)
        if normalized is not None:
            in_font_section = normalized in _FONT_SECTIONS
            continue
        if in_font_section:
            match = re.match(
                r'^\s*family\s*=\s*(?:"([^"]*)"|\x27([^\x27]*)\x27|([^#\r\n]+))',
                line,
            )
            if match:
                val = match.group(1) or match.group(2) or match.group(3) or ""
                if val.strip():
                    return val.strip()
    return None


def read_configured_font_family(
    policy_path: Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> str | None:
    """Return the existing configured ``[font.normal].family`` if non-empty."""
    target = (
        Path(policy_path)
        if policy_path is not None
        else terminal_policy_path(env, home=home)
    )
    try:
        if not target.is_file():
            return None
        raw = target.read_text(encoding="utf-8")
    except OSError:
        return None
    return _extract_family_from_toml_text(raw)


# CoreText symbolic traits (CTFontTraits.h). Names never determine eligibility.
_FONT_ITALIC = 1 << 0
_FONT_BOLD = 1 << 1
_FONT_MONOSPACE = 1 << 10


@dataclass(frozen=True)
class _FontFace:
    family: str
    style: str
    traits: int


def _native_font_faces() -> list[_FontFace]:
    """Read the process-visible CoreText catalogue, without GUI or registration.

    ctypes uses the OS C API directly; no PyObjC, compiler, subprocess, or font
    installation is required. Every Copy/Create reference is released here.
    """
    try:
        cf = ctypes.CDLL(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
        ct = ctypes.CDLL("/System/Library/Frameworks/CoreText.framework/CoreText")
        ptr = ctypes.c_void_p

        def bind(lib: Any, name: str, result: Any, args: list[Any]) -> Any:
            function = getattr(lib, name)
            function.restype = result
            function.argtypes = args
            return function

        release = bind(cf, "CFRelease", None, [ptr])
        count = bind(cf, "CFArrayGetCount", ctypes.c_long, [ptr])
        at = bind(cf, "CFArrayGetValueAtIndex", ptr, [ptr, ctypes.c_long])
        length = bind(cf, "CFStringGetLength", ctypes.c_long, [ptr])
        string = bind(
            cf,
            "CFStringGetCString",
            ctypes.c_bool,
            [ptr, ptr, ctypes.c_long, ctypes.c_uint32],
        )
        collection = bind(ct, "CTFontCollectionCreateFromAvailableFonts", ptr, [ptr])
        descriptors = bind(
            ct, "CTFontCollectionCreateMatchingFontDescriptors", ptr, [ptr]
        )
        create = bind(
            ct, "CTFontCreateWithFontDescriptor", ptr, [ptr, ctypes.c_double, ptr]
        )
        traits = bind(ct, "CTFontGetSymbolicTraits", ctypes.c_uint32, [ptr])
        family = bind(ct, "CTFontCopyFamilyName", ptr, [ptr])
        copy_name = bind(ct, "CTFontCopyName", ptr, [ptr, ptr])
        style_key = ptr.in_dll(ct, "kCTFontStyleNameKey")

        def style(font: Any) -> Any:
            return copy_name(font, style_key)

        def text(copy: Any, font: Any) -> str:
            value = copy(font)
            if not value:
                raise OnboardingError("CoreText returned an unnamed font face")
            try:
                buffer = ctypes.create_string_buffer(length(value) * 4 + 1)
                if not string(value, buffer, len(buffer), 0x08000100):  # UTF-8
                    raise OnboardingError("CoreText font name could not be decoded")
                return buffer.value.decode("utf-8")
            finally:
                release(value)

        owned_collection = collection(None)
        if not owned_collection:
            raise OnboardingError("CoreText font catalogue is unavailable")
        try:
            owned_descriptors = descriptors(owned_collection)
            if not owned_descriptors:
                raise OnboardingError("CoreText font catalogue has no descriptors")
            try:
                faces = []
                for index in range(count(owned_descriptors)):
                    font = create(at(owned_descriptors, index), 12.0, None)
                    if not font:
                        raise OnboardingError("CoreText could not inspect a font face")
                    try:
                        faces.append(
                            _FontFace(
                                text(family, font), text(style, font), traits(font)
                            )
                        )
                    finally:
                        release(font)
                return faces
            finally:
                release(owned_descriptors)
        finally:
            release(owned_collection)
    except (OSError, AttributeError, UnicodeError) as exc:
        raise OnboardingError(f"CoreText font catalogue unavailable: {exc}") from exc


def _font_face_styles(faces: Sequence[_FontFace]) -> tuple[str, str, str]:
    """Choose genuine face styles; missing bold/italic reuse the normal face."""
    ordered = sorted(
        faces,
        key=lambda f: (
            f.style.casefold() not in ("regular", "normal", "book"),
            f.style.casefold(),
            f.style,
        ),
    )
    normal = next(
        (f for f in ordered if not f.traits & (_FONT_BOLD | _FONT_ITALIC)), ordered[0]
    )
    bold = next(
        (
            f
            for f in sorted(ordered, key=lambda f: f.style.casefold() != "bold")
            if f.traits & _FONT_BOLD and not f.traits & _FONT_ITALIC
        ),
        normal,
    )
    italic = next(
        (
            f
            for f in sorted(
                ordered,
                key=lambda f: (
                    f.style.casefold() not in ("italic", "regular italic", "oblique")
                ),
            )
            if f.traits & _FONT_ITALIC and not f.traits & _FONT_BOLD
        ),
        normal,
    )
    return normal.style, bold.style, italic.style


def inspect_font_variants(
    *,
    policy_path: Path | None = None,
    font_dirs: Sequence[Path] | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> list[FontVariantStatus]:
    """Curated families first, then OS fixed-width families, without writes.

    Explicit font_dirs and non-macOS retain the curated file-based route only;
    arbitrary family names cannot prove fixed width through filename matching.
    A configured ineligible family remains visible as current, never eligible.
    """
    configured = read_configured_font_family(policy_path, env=env, home=home)
    native = sys.platform == "darwin" and font_dirs is None
    source = (
        "CoreText fixed-pitch traits"
        if native
        else "curated files; native catalogue unavailable"
    )
    families: dict[str, list[_FontFace]] = {}
    paths: dict[str, str] = {}
    if native:
        for face in _native_font_faces():
            if face.traits & _FONT_MONOSPACE:
                families.setdefault(face.family, []).append(face)
    else:
        dirs = (
            tuple(Path(d) for d in font_dirs)
            if font_dirs is not None
            else default_font_dirs(home=home)
        )
        for family, _description, tokens in MONASPACE_FONT_VARIANTS:
            found = _find_font_file(tokens, dirs)
            if found is not None:
                families[family] = [_FontFace(family, "Regular", _FONT_MONOSPACE)]
                paths[family] = str(found)

    descriptions = {
        family: description for family, description, _ in MONASPACE_FONT_VARIANTS
    }
    # Case-insensitive dedupe with deterministic genuine upstream spelling.
    canonical: dict[str, str] = {}
    for family in sorted(families, key=lambda name: (name.casefold(), name)):
        canonical.setdefault(family.casefold(), family)
    ordered = [
        canonical[name.casefold()]
        for name in MONASPACE_FAMILIES
        if name.casefold() in canonical
    ]
    ordered += [name for name in canonical.values() if name not in ordered]
    results = []
    for family in ordered:
        active = configured is not None and family.casefold() == configured.casefold()
        results.append(
            FontVariantStatus(
                family=family,
                label=family,
                description=descriptions.get(family, "Installed fixed-width family"),
                font_file_present=True,
                font_file_path=paths.get(family, ""),
                active=active,
                selectable=True,
                status_label="current" if active else "available",
                face_styles=_font_face_styles(families[family]),
                catalogue_source=source,
            )
        )
    if configured and configured.casefold() not in canonical:
        results.append(
            FontVariantStatus(
                family=configured,
                label=configured,
                description="Current policy preserved; unavailable or not fixed-width",
                font_file_present=False,
                active=True,
                preserved_custom=True,
                selectable=False,
                status_label="current · unavailable for selection",
                catalogue_source=source,
            )
        )
    return results


def proposed_font_family(
    *,
    policy_path: Path | None = None,
    font_dirs: Sequence[Path] | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> str:
    """Return the active/proposed font family respecting existing policy first."""
    variants = inspect_font_variants(
        policy_path=policy_path,
        font_dirs=font_dirs,
        env=env,
        home=home,
    )
    for variant in variants:
        if variant.active:
            return variant.family
    return variants[0].family if variants else FALLBACK_FONT_FAMILY


def _rewrite_font_sections(raw: str, family: str, styles: tuple[str, str, str]) -> str:
    lines = raw.splitlines()
    seen_sections: set[str] = set()
    updated_lines: list[str] = []
    escaped_family = json.dumps(family)
    section_styles = dict(zip(_FONT_SECTIONS, styles, strict=True))
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        normalized_section = _normalize_toml_section(line)
        if normalized_section in _FONT_SECTIONS:
            seen_sections.add(normalized_section)
            updated_lines.append(line)
            idx += 1
            replaced_in_section = False
            replaced_style = False
            section_body: list[str] = []
            while idx < len(lines):
                next_line = lines[idx]
                if re.match(r"^\s*\[[^\]]+\]\s*(?:#.*)?$", next_line):
                    break
                match = re.match(
                    r'^(\s*)family\s*=\s*(?:"[^"]*"|\x27[^\x27]*\x27|[^#\r\n]*?)(\s*#.*)?$',
                    next_line,
                )
                if not replaced_in_section and match:
                    indent = match.group(1) or ""
                    comment = match.group(2) or ""
                    section_body.append(f"{indent}family = {escaped_family}{comment}")
                    replaced_in_section = True
                elif re.match(r"^\s*style\s*=", next_line):
                    # Match the quoted TOML value before looking for its comment.
                    style_match = re.match(
                        r"""^(\s*)style\s*=\s*(?:"(?:\\.|[^"\\])*"|'[^']*')(\s*#.*)?\s*$""",
                        next_line,
                    )
                    if style_match is None:
                        raise ValueError(
                            "terminal font style must be a single-line string"
                        )
                    indent, suffix = style_match.groups()
                    suffix = suffix or ""
                    section_body.append(
                        f"{indent}style = {json.dumps(section_styles[normalized_section])}{suffix}"
                    )
                    replaced_style = True
                else:
                    section_body.append(next_line)
                idx += 1
            if not replaced_in_section:
                section_body.insert(0, f"family = {escaped_family}")
            if not replaced_style:
                section_body.insert(
                    1, f"style = {json.dumps(section_styles[normalized_section])}"
                )
            updated_lines.extend(section_body)
            continue
        updated_lines.append(line)
        idx += 1

    for section in _FONT_SECTIONS:
        if section not in seen_sections:
            if updated_lines and updated_lines[-1].strip() != "":
                updated_lines.append("")
            updated_lines.append(section)
            updated_lines.append(f"family = {escaped_family}")
            updated_lines.append(f"style = {json.dumps(section_styles[section])}")

    return "\n".join(updated_lines) + "\n"


def select_font_family(
    family: str,
    *,
    policy_path: Path | None = None,
    font_dirs: Sequence[Path] | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Apply an eligible family and genuine styles through the existing owner.

    Re-selecting an unavailable or proportional configured family is a no-op.
    Eligible selections harmonize all three faces, including the current family.
    Unrelated preferences, font size and offsets survive the atomic write.
    """
    clean = str(family or "").strip()
    if not clean or any(ord(ch) < 32 or ord(ch) == 127 for ch in clean):
        raise OnboardingError(f"invalid font family {family!r}")

    target = (
        Path(policy_path)
        if policy_path is not None
        else terminal_policy_path(env, home=home)
    )
    configured = read_configured_font_family(target, env=env, home=home)
    variants = inspect_font_variants(
        policy_path=target,
        font_dirs=font_dirs,
        env=env,
        home=home,
    )
    chosen = next(
        (
            variant
            for variant in variants
            if variant.family == clean and variant.selectable
        ),
        None,
    )
    if chosen is None:
        if configured is not None and clean == configured:
            return target
        raise FontNotAvailableError(f"{clean!r} is not an available fixed-width family")

    existing_raw = (
        target.read_text(encoding="utf-8")
        if target.is_file()
        else _DEFAULT_FONT_POLICY_SEED
    )
    if target.is_file() and existing_raw.strip():
        try:
            tomllib.loads(existing_raw)
        except tomllib.TOMLDecodeError as exc:
            raise ValueError("terminal-policy.toml is malformed") from exc

    updated_raw = _rewrite_font_sections(existing_raw, clean, chosen.face_styles)
    try:
        tomllib.loads(updated_raw)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError("rewritten terminal-policy.toml is invalid") from exc

    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(updated_raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return target


apply_font_family = select_font_family
