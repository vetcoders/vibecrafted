# Fixed bootstrap, invoked as generation/bin/python3 -I -B -c. No request text in argv.
import hashlib
import importlib.util
import json
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path


def fail(code):
    return {"ok": False, "code": code}


def main():
    root_text = os.environ.get("VIBECRAFTED_RUNTIME_ROOT", "")
    if not root_text or not Path(root_text).is_absolute():
        return fail("unavailable")
    root = Path(root_text).resolve(strict=True)
    # Check the manifest-bound installer and its eager script imports before
    # importing any generation code. Remaining runtime files use the existing
    # selected-generation trust contract, not a new signature claim.
    manifest = json.loads((root / "runtime-manifest.json").read_text("utf-8"))
    if (
        manifest.get("schema") != "vibecrafted.runtime-generation.v2"
        or manifest.get("version") != root.name
    ):
        return fail("unavailable")
    hashes = manifest.get("hashes")
    if not isinstance(hashes, dict):
        return fail("unavailable")
    for relative in (
        "scripts/vetcoders_install.py",
        "scripts/distribution_manifest.py",
        "scripts/installer_brand.py",
    ):
        path = root / relative
        if (
            path.is_symlink()
            or not path.is_file()
            or not isinstance(hashes.get(relative), str)
        ):
            return fail("unavailable")
        if hashlib.sha256(path.read_bytes()).hexdigest() != hashes[relative]:
            return fail("unavailable")
    sys.path.insert(0, str(root / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "_vc_skills_installer", root / "scripts/vetcoders_install.py"
    )
    if spec is None or spec.loader is None:
        return fail("unavailable")
    installer = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = installer
    spec.loader.exec_module(installer)
    paths = installer._runtime_install_paths()
    runtime_home = paths["runtime_home"]
    current = runtime_home / "tools/vibecrafted-current"
    active = json.loads((runtime_home / "active.json").read_text("utf-8"))
    receipt = installer._load_runtime_install_receipt(
        installer._runtime_receipt_path(runtime_home)
    )
    if (
        not receipt
        or receipt.get("roots") != {key: str(value) for key, value in paths.items()}
        or installer._runtime_rescue_pending_publication_reason(receipt)
        or active.get("schema") != "vibecrafted.active-runtime.v1"
        or active.get("version") != root.name
        or active.get("runtime_root") != str(root)
        or receipt.get("version") != root.name
        or current.resolve(strict=True) != root
        or receipt.get("owned_symlinks", {}).get(str(current)) != str(root)
        or root != runtime_home / "releases" / root.name
        or installer._runtime_generation_payload_errors(root)
    ):
        return fail("unavailable")
    request = json.load(sys.stdin)
    if not isinstance(request, dict) or request.get("op") not in ("read", "save"):
        return fail("invalid")
    if request["op"] == "read":
        result = installer._load_local_skill_owner().read_skill(request.get("name"))
        result["writable"] = callable(getattr(installer, "publish_local_skill", None))
        return result
    publisher = getattr(installer, "publish_local_skill", None)
    if not callable(publisher):
        return fail("unavailable")
    return publisher(request.get("name"), request.get("text"), request.get("revision"))


try:
    # Installer lease recovery may print; stdout is the one JSON protocol.
    with open(os.devnull, "w", encoding="utf-8") as sink, redirect_stdout(sink):
        result = main()
except (
    OSError,
    ValueError,
    TypeError,
    RuntimeError,
    AttributeError,
    ImportError,
    KeyError,
) as error:
    code = getattr(error, "code", "unavailable")
    result = fail(
        code
        if code in ("conflict", "invalid", "not_found", "unsafe", "io")
        else "unavailable"
    )
print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
