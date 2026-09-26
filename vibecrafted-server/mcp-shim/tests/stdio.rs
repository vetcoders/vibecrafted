//! Binary proof: a closed vc-server still finishes `initialize` with an empty
//! tool list, and a live HTTP peer receives the shim PID header.

use std::io::{Read, Write};
use std::net::TcpListener;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc;
use std::thread;
use std::time::Duration;

use serde_json::Value;

struct ChildGuard(Option<Child>);

impl Drop for ChildGuard {
    fn drop(&mut self) {
        if let Some(child) = self.0.as_mut() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

fn drive(extra_env: &[(&str, &str)], script: &str) -> String {
    let mut command = Command::new(env!("CARGO_BIN_EXE_vc-mcp-shim"));
    command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .env("VC_MCP_TIMEOUT_MS", "400");
    for (key, value) in extra_env {
        command.env(key, value);
    }
    let mut child = command.spawn().expect("spawn vc-mcp-shim");
    let pid = child.id();
    let mut stdin = child.stdin.take().expect("stdin");
    let stdout = child.stdout.take().expect("stdout");
    stdin.write_all(script.as_bytes()).expect("write script");
    drop(stdin);
    let (tx, rx) = mpsc::channel();
    thread::spawn(move || {
        let mut text = String::new();
        let mut stdout = stdout;
        let _ = stdout.read_to_string(&mut text);
        let _ = tx.send(text);
    });
    let text = rx
        .recv_timeout(Duration::from_secs(8))
        .unwrap_or_else(|_| format!("shim pid {pid} produced no stdout"));
    let mut guard = ChildGuard(Some(child));
    if let Some(child) = guard.0.as_mut() {
        let _ = child.kill();
        let _ = child.wait();
    }
    guard.0 = None;
    text
}

fn read_http(stream: &mut std::net::TcpStream) -> String {
    let mut buf = Vec::new();
    let mut chunk = [0_u8; 4096];
    let deadline = std::time::Instant::now() + Duration::from_secs(3);
    while std::time::Instant::now() < deadline {
        match stream.read(&mut chunk) {
            Ok(0) => break,
            Ok(n) => {
                buf.extend_from_slice(&chunk[..n]);
                if let Some(header_end) = header_end(&buf) {
                    let header = String::from_utf8_lossy(&buf[..header_end]);
                    let length = content_length(&header).unwrap_or(0);
                    if buf.len() - header_end >= length {
                        break;
                    }
                }
            }
            Err(error)
                if error.kind() == std::io::ErrorKind::WouldBlock
                    || error.kind() == std::io::ErrorKind::TimedOut =>
            {
                if header_end(&buf).is_some() {
                    break;
                }
            }
            Err(_) => break,
        }
    }
    String::from_utf8_lossy(&buf).into_owned()
}

fn header_end(buf: &[u8]) -> Option<usize> {
    buf.windows(4)
        .position(|window| window == b"\r\n\r\n")
        .map(|index| index + 4)
}

fn content_length(header: &str) -> Option<usize> {
    header.lines().find_map(|line| {
        let (name, value) = line.split_once(':')?;
        name.eq_ignore_ascii_case("content-length")
            .then(|| value.trim().parse().ok())
            .flatten()
    })
}

fn lines(text: &str) -> Vec<Value> {
    text.lines()
        .filter(|line| !line.trim().is_empty())
        .map(|line| serde_json::from_str(line).unwrap_or_else(|err| panic!("{err}: {line}")))
        .collect()
}

#[test]
fn unreachable_server_initializes_with_an_empty_tool_list() {
    let text = drive(
        &[("VC_MCP_URL", "http://127.0.0.1:1/mcp")],
        concat!(
            r#"{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}"#,
            "\n",
            r#"{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}"#,
            "\n",
        ),
    );
    let messages = lines(&text);
    assert!(
        messages.len() >= 2,
        "expected initialize and tools/list, got {text}"
    );
    assert_eq!(messages[0]["id"], 1);
    assert_eq!(messages[0]["result"]["protocolVersion"], "2025-03-26");
    assert_eq!(messages[0]["result"]["serverInfo"]["name"], "vc-mcp-shim");
    assert!(messages[0]["result"]["capabilities"]["tools"].is_object());
    assert_eq!(messages[1]["id"], 2);
    assert_eq!(messages[1]["result"]["tools"], serde_json::json!([]));
    assert!(messages[1].get("error").is_none());
}

#[test]
fn live_peer_receives_pid_and_proxies_tools_list() {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
    let port = listener.local_addr().expect("addr").port();
    let (tx, rx) = mpsc::channel::<String>();
    thread::spawn(move || {
        for _ in 0..2 {
            let Ok((mut stream, _)) = listener.accept() else {
                break;
            };
            stream
                .set_read_timeout(Some(Duration::from_secs(3)))
                .expect("timeout");
            let request = read_http(&mut stream);
            let body = if request.contains("\"initialize\"") {
                r#"{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-03-26","capabilities":{"tools":{}},"serverInfo":{"name":"vc-server","version":"test"}}}"#
            } else {
                r#"{"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"vc_ping","description":"pilot"}]}}"#
            };
            let header = format!(
                "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n",
                body.len()
            );
            let _ = stream.write_all(header.as_bytes());
            let _ = stream.write_all(body.as_bytes());
            let _ = tx.send(request);
        }
    });

    let url = format!("http://127.0.0.1:{port}/mcp");
    let text = drive(
        &[
            ("VC_MCP_URL", url.as_str()),
            ("VC_SERVER_MCP_BEARER", "shim-bearer"),
            ("VIBECRAFTED_RUN_ID", "run-from-env"),
        ],
        concat!(
            r#"{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}"#,
            "\n",
            r#"{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}"#,
            "\n",
        ),
    );
    let messages = lines(&text);
    assert_eq!(messages[0]["result"]["serverInfo"]["name"], "vc-server");
    let tools = messages[1]["result"]["tools"]
        .as_array()
        .expect("proxied tools");
    assert_eq!(tools[0]["name"], "vc_ping");

    let mut saw_pid = false;
    let mut saw_run = false;
    let mut saw_auth = false;
    for _ in 0..2 {
        let request = rx
            .recv_timeout(Duration::from_secs(3))
            .expect("mock received a request");
        let lower = request.to_ascii_lowercase();
        if lower.contains("x-vibecrafted-pid:") {
            saw_pid = true;
        }
        if lower.contains("x-vibecrafted-run-id: run-from-env") {
            saw_run = true;
        }
        if lower.contains("authorization: bearer shim-bearer") {
            saw_auth = true;
        }
        assert!(
            lower.contains("x-vibecrafted-ancestors:"),
            "ancestor header missing: {request}"
        );
    }
    assert!(saw_pid, "shim pid header missing");
    assert!(saw_run, "run id header missing");
    assert!(saw_auth, "bearer missing");
}
