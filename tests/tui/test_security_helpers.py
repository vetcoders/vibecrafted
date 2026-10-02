import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CHAT_CLI_PATH = REPO_ROOT / "tools" / "scripts" / "chat" / "chat-cli.py"
BRAVE_SEARCH_PATH = (
    REPO_ROOT
    / "vibecrafted-core"
    / "vibecrafted_core"
    / "skills"
    / "vc-research"
    / "engines"
    / "brave_search.py"
)


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_tls_context_requires_certificate_validation() -> None:
    brave_search = load_module("brave_search", BRAVE_SEARCH_PATH)
    context = brave_search.build_tls_context()

    assert context.verify_mode.name == "CERT_REQUIRED"
    assert context.check_hostname is True


def test_validate_remote_url_accepts_network_schemes_only() -> None:
    chat_cli = load_module("chat_cli", CHAT_CLI_PATH)

    assert (
        chat_cli.validate_remote_url("https://api.openai.com/v1")
        == "https://api.openai.com/v1"
    )
    assert (
        chat_cli.validate_remote_url("http://localhost:8080/v1")
        == "http://localhost:8080/v1"
    )


def test_validate_remote_url_rejects_file_scheme() -> None:
    chat_cli = load_module("chat_cli", CHAT_CLI_PATH)

    try:
        chat_cli.validate_remote_url("file:///etc/passwd")
    except ValueError as exc:
        assert "http(s)" in str(exc)
    else:  # explicit failure branch for readability
        raise AssertionError("file:// URL should be rejected")
