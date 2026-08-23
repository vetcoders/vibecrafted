#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Multimodal OpenAI-Compatible Chat Client (stdlib only, no dependencies)

A lightweight CLI chat client that works with any OpenAI-compatible API endpoint.
Uses only Python's standard library - no requests, no httpx, no openai package.

Features:
- Streaming responses (SSE)
- Multimodal support: text, images, audio
- Internet search via DuckDuckGo
- Works with local LLMs (llama.cpp, vLLM, etc.)

Usage:
    # With local server (host-a, llama.cpp, etc.)
    ./chat-cli.py --base-url http://localhost:8080/v1

    # With custom model
    ./chat-cli.py --base-url http://host-a:10240/v1 --model libraxisai/a local-11b

    # With OpenAI
    ./chat-cli.py --base-url https://api.openai.com/v1 --api-key sk-... --model gpt-4o

    # Attach image/audio
    ./chat-cli.py --base-url http://localhost:8080/v1 --image ./photo.jpg

Commands (in chat):
    /image <path-or-url>    Attach image to next message
    /audio <path>           Attach audio file to next message
    /search <query>         Inject search context
    /clear                  Clear conversation history
    /exit                   Quit

Environment Variables:
    CHATCLIENT_BASE_URL     Default base URL
    CHATCLIENT_API_KEY      Default API key
    CHATCLIENT_MODEL        Default model

Created by Vetcoders (c)2024-2026
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import mimetypes
import os
import ssl
import sys
from collections.abc import Iterator
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, urlparse
from urllib.request import Request, urlopen

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "WARNING").upper(),
    format="%(asctime)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)


def validate_remote_url(url: str) -> str:
    """Reject non-http(s) URLs before any network call; returns the URL unchanged."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Only absolute http(s) URLs are allowed")
    return url


def safe_urlopen(request: Request, timeout: float):
    """Open ``request`` after validating its URL scheme; uses TLS context for https."""
    url = validate_remote_url(request.full_url)
    context = ssl.create_default_context() if url.startswith("https://") else None
    # Validation above rejects file:// and other non-network schemes before opening.
    # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
    return urlopen(request, timeout=timeout, context=context)


class C:
    """ANSI colors."""

    USER = "\033[94m"  # Blue
    ASSISTANT = "\033[92m"  # Green
    SYSTEM = "\033[93m"  # Yellow
    RESET = "\033[0m"


def is_url(s: str) -> bool:
    """Return True when ``s`` parses as an http(s) URL."""
    try:
        p = urlparse(s)
        return p.scheme in ("http", "https")
    except Exception:  # noqa: BLE001
        return False


def file_to_data_url(path: str) -> str:
    """Read a local file and encode it as a base64 ``data:`` URL for image attachments."""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"No such file: {path}")
    mime, _ = mimetypes.guess_type(path)
    if not mime:
        ext = (os.path.splitext(path)[1] or "").lower().lstrip(".")
        if ext in {"png", "jpg", "jpeg", "gif", "webp"}:
            mime = f"image/{'jpeg' if ext == 'jpg' else ext}"
        else:
            mime = "application/octet-stream"
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def read_audio_base64(path: str) -> tuple[str, str]:
    """Read a local audio file and return ``(base64_data, format)`` for the API payload."""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"No such file: {path}")
    ext = (os.path.splitext(path)[1] or "").lower().lstrip(".")
    fmt_map = {
        "wav": "wav",
        "mp3": "mp3",
        "m4a": "m4a",
        "aac": "m4a",
        "ogg": "ogg",
        "oga": "ogg",
        "flac": "flac",
        "webm": "webm",
        "opus": "ogg",
    }
    fmt = fmt_map.get(ext, ext or "wav")
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return b64, fmt


def internet_search(query: str, timeout: float = 6.0) -> str:
    """DuckDuckGo Instant Answer API (stdlib only)."""
    logger.debug("Internet search query: %s", query)
    try:
        url = f"https://api.duckduckgo.com/?q={quote_plus(query)}&format=json&no_html=1&skip_disambig=1"
        req = Request(url, headers={"User-Agent": "ChatCLI/1.0"})
        with safe_urlopen(req, timeout=timeout) as resp:
            if resp.getcode() != 200:
                return f"Search failed: HTTP {resp.getcode()}"
            data = json.loads(resp.read().decode("utf-8", "strict"))
    except (TimeoutError, HTTPError, URLError) as e:
        return f"Search failed: {e}"
    except Exception as e:  # noqa: BLE001
        return f"Search failed: {e}"

    result = data.get("AbstractText") or ""
    if not result:
        related = data.get("RelatedTopics") or []
        if isinstance(related, list) and related:
            first = related[0]
            if isinstance(first, dict):
                result = first.get("Text") or ""
    if not result:
        return "No concise result found."
    return (result[:500] + "...") if len(result) > 500 else result


def sse_post(
    url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float = 60.0
) -> Iterator[str]:
    """POST JSON and stream SSE responses."""
    body = json.dumps(payload).encode("utf-8")
    req_headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "User-Agent": "ChatCLI/1.0",
    }
    req_headers.update(headers or {})
    req = Request(url, data=body, headers=req_headers, method="POST")
    try:
        with safe_urlopen(req, timeout=timeout) as resp:
            status = resp.getcode()
            if status < 200 or status >= 300:
                raise HTTPError(
                    url, status, f"HTTP status {status}", resp.headers, None
                )
            while True:
                line = resp.readline()
                if not line:
                    break
                if line.startswith(b":") or not line.strip():
                    continue
                try:
                    decoded = line.decode("utf-8", "replace").strip()
                except (UnicodeError, ValueError, TypeError, KeyError):
                    continue
                if not decoded.startswith("data:"):
                    continue
                data_str = decoded[5:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    obj = json.loads(data_str)
                    delta = obj["choices"][0]["delta"]
                    content = delta.get("content")
                    if isinstance(content, str) and content:
                        yield content
                except (UnicodeError, ValueError, TypeError, KeyError):
                    continue
    except TimeoutError as e:
        raise TimeoutError(f"Request timed out after {timeout}s") from e


def post_once(
    url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float = 60.0
) -> dict[str, Any]:
    """Non-streaming POST."""
    body = json.dumps(payload).encode("utf-8")
    req_headers = {"Content-Type": "application/json", "User-Agent": "ChatCLI/1.0"}
    req_headers.update(headers or {})
    req = Request(url, data=body, headers=req_headers, method="POST")
    with safe_urlopen(req, timeout=timeout) as resp:
        status = resp.getcode()
        if status < 200 or status >= 300:
            raise HTTPError(url, status, f"HTTP status {status}", resp.headers, None)
        return json.loads(resp.read().decode("utf-8", "strict"))


def build_user_content(
    text: str | None, images: list[str], audios: list[str]
) -> list[dict[str, Any]]:
    """Assemble an OpenAI-compatible multimodal ``content`` list from text/images/audio.

    Attachment failures degrade to an inline error text part rather than raising.
    """
    parts: list[dict[str, Any]] = []
    if text:
        parts.append({"type": "text", "text": text})
    for img in images:
        try:
            url = img if is_url(img) else file_to_data_url(img)
            parts.append({"type": "image_url", "image_url": {"url": url}})
        except Exception as e:  # noqa: BLE001
            parts.append({"type": "text", "text": f"[Image attach failed: {e}]"})
    for a in audios:
        try:
            b64, fmt = read_audio_base64(a)
            parts.append(
                {"type": "input_audio", "input_audio": {"data": b64, "format": fmt}}
            )
        except Exception as e:  # noqa: BLE001
            parts.append({"type": "text", "text": f"[Audio attach failed: {e}]"})
    return parts


def print_system(msg: str) -> None:
    """Print a system-colored status line to stdout."""
    print(f"{C.SYSTEM}{msg}{C.RESET}")


def print_assistant_prefix() -> None:
    """Print the ``Assistant:`` prompt prefix without a trailing newline."""
    print(f"{C.ASSISTANT}Assistant: {C.RESET}", end="", flush=True)


def main() -> None:
    """Run the interactive REPL: parse CLI args, then loop reading/sending chat turns."""
    default_base = (
        os.environ.get("CHATCLIENT_BASE_URL") or os.environ.get("LLM_SERVER_URL") or ""
    )
    default_key = os.environ.get("CHATCLIENT_API_KEY") or "sk-dummy"
    default_model = os.environ.get("CHATCLIENT_MODEL") or "default"

    parser = argparse.ArgumentParser(
        description="Multimodal OpenAI-Compatible Chat Client (stdlib only)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Local LLM server
  ./chat-cli.py --base-url http://localhost:8080/v1

  # host-a with a local model
  ./chat-cli.py --base-url http://host-a:10240/v1 --model libraxisai/a local-11b

  # OpenAI
  ./chat-cli.py --base-url https://api.openai.com/v1 --api-key $OPENAI_API_KEY --model gpt-4o

Created by Vetcoders (c)2024-2026
""",
    )
    parser.add_argument(
        "--base-url",
        "-b",
        default=default_base,
        help="API base URL (or set CHATCLIENT_BASE_URL)",
    )
    parser.add_argument(
        "--api-key",
        "-k",
        default=default_key,
        help="API key (default: sk-dummy for local servers)",
    )
    parser.add_argument(
        "--model", "-m", default=default_model, help="Model name (default: 'default')"
    )
    parser.add_argument(
        "--image",
        action="append",
        default=[],
        help="Attach image path/URL to first message (repeatable)",
    )
    parser.add_argument(
        "--audio",
        action="append",
        default=[],
        help="Attach audio file to first message (repeatable)",
    )
    parser.add_argument("--no-stream", action="store_true", help="Disable streaming")
    parser.add_argument(
        "--timeout", type=float, default=60.0, help="Request timeout in seconds"
    )
    args = parser.parse_args()

    base_url = (args.base_url or "").rstrip("/")
    if not base_url:
        parser.error("--base-url is required (or set CHATCLIENT_BASE_URL)")
    validate_remote_url(base_url)

    headers = {"Authorization": f"Bearer {args.api_key}"}
    messages: list[dict[str, Any]] = []

    print_system("Chat CLI (OpenAI-Compatible, stdlib only)")
    print_system(f"Connected to: {base_url}")
    print_system(f"Model: {args.model}")
    print_system("Commands: /image, /audio, /search, /clear, /exit\n")

    pending_images: list[str] = list(args.image)
    pending_audios: list[str] = list(args.audio)

    while True:
        try:
            user_input = input(f"{C.USER}You: {C.RESET}").strip()
        except EOFError:
            print()
            break
        except KeyboardInterrupt:
            print_system("\nInterrupted.")
            break

        if not user_input:
            continue

        low = user_input.lower()
        if low in ("/exit", "exit", "quit", "/quit"):
            break
        if low in ("/clear", "clear"):
            messages.clear()
            print_system("History cleared.\n")
            continue

        if low.startswith("/image "):
            arg = user_input.split(" ", 1)[1].strip()
            if arg:
                pending_images.append(arg)
                print_system(f"[Queued image: {arg}]")
            continue
        if low.startswith("/audio "):
            arg = user_input.split(" ", 1)[1].strip()
            if arg:
                pending_audios.append(arg)
                print_system(f"[Queued audio: {arg}]")
            continue
        if low.startswith("/search "):
            q = user_input.split(" ", 1)[1].strip()
            if q:
                result = internet_search(q)
                messages.append(
                    {"role": "system", "content": f"Internet search result: {result}"}
                )
                print_system("[Added search context]")
            continue

        # Build multimodal user message
        content_parts = build_user_content(user_input, pending_images, pending_audios)
        user_message: dict[str, Any]
        if len(content_parts) == 1 and content_parts[0].get("type") == "text":
            user_message = {"role": "user", "content": content_parts[0]["text"]}
        else:
            user_message = {"role": "user", "content": content_parts}

        messages.append(user_message)
        pending_images.clear()
        pending_audios.clear()

        data: dict[str, Any] = {"model": args.model, "messages": messages}

        if not args.no_stream:
            data["stream"] = True
            print_assistant_prefix()
            full = ""
            try:
                for chunk in sse_post(
                    f"{base_url}/chat/completions", headers, data, timeout=args.timeout
                ):
                    print(chunk, end="", flush=True)
                    full += chunk
                print()
            except Exception as e:  # noqa: BLE001
                print()
                print_system(f"Error: {e}\n")
                continue
            messages.append({"role": "assistant", "content": full})
        else:
            try:
                resp = post_once(
                    f"{base_url}/chat/completions", headers, data, timeout=args.timeout
                )
            except Exception as e:  # noqa: BLE001
                print_system(f"Error: {e}\n")
                continue
            try:
                content = resp["choices"][0]["message"]["content"]
            except Exception:  # noqa: BLE001
                content = json.dumps(resp)
            print(f"{C.ASSISTANT}Assistant: {C.RESET}{content}\n")
            messages.append({"role": "assistant", "content": content})


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"{C.SYSTEM}Fatal error: {e}{C.RESET}", file=sys.stderr)
        sys.exit(1)
