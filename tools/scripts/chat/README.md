# Chat Tools

CLI tools for interacting with LLMs.

## chat-cli.py

Lightweight multimodal chat client for any OpenAI-compatible API.
Zero dependencies - uses only Python's standard library.

### Features

- Streaming responses (SSE)
- Multimodal: text, images, audio
- Works with local LLMs (llama.cpp, vLLM, mlx-lm, etc.)
- Internet search injection (DuckDuckGo)
- No external dependencies

### Requirements

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (optional, for shebang execution)

### Usage

```bash
chmod +x chat-cli.py

# Local server (llama.cpp, vLLM, etc.)
./chat-cli.py --base-url http://localhost:8080/v1

# host-a with a local
./chat-cli.py -b http://host-a:10240/v1 -m libraxisai/a local-11b-v3.0-mlx

# OpenAI
./chat-cli.py -b https://api.openai.com/v1 -k $OPENAI_API_KEY -m gpt-4o

# With image attachment
./chat-cli.py -b http://localhost:8080/v1 --image ./photo.jpg
```

### Chat Commands

| Command                | Description                       |
| ---------------------- | --------------------------------- |
| `/image <path-or-url>` | Attach image to next message      |
| `/audio <path>`        | Attach audio file to next message |
| `/search <query>`      | Inject DuckDuckGo search context  |
| `/clear`               | Clear conversation history        |
| `/exit`                | Quit                              |

### Environment Variables

| Variable              | Description        |
| --------------------- | ------------------ |
| `CHATCLIENT_BASE_URL` | Default base URL   |
| `CHATCLIENT_API_KEY`  | Default API key    |
| `CHATCLIENT_MODEL`    | Default model name |

### Examples

```bash
# Quick alias
alias svetliq='./chat-cli.py -b http://host-a:10240/v1 -m libraxisai/a local-11b'

# Chat session
$ svetliq
Chat CLI (OpenAI-Compatible, stdlib only)
Connected to: http://host-a:10240/v1
Model: libraxisai/a local-11b

You: Kim jestes?
Assistant: Jestem a local - polski model AI specjalizujacy sie w medycynie weterynaryjnej...
```

### Why stdlib only?

- No `requests`, `httpx`, or `openai` package needed
- Works immediately after git clone
- No virtual environment required for basic usage
- Avoids `ModuleNotFoundError` issues

---

_Copyright © 2024–2026 Vetcoders_
