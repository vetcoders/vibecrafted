//! What a person sees after VOC hands its terminal to a child and takes it back.
//!
//! The real `voc` binary runs in a pseudo-terminal and every byte it writes is
//! replayed into a VT100 emulator, so these tests compare the physical screen,
//! not a Ratatui buffer: a buffer cannot notice that a child cleared the
//! display. The deck stand-in plays an interactive child. It scribbles,
//! clears the screen, toggles the alternate screen, waits until the test lets
//! it go, and exits with the requested status.
//!
//! `VOC_HANDBACK_BIN` points the suite at another build (used for the baseline
//! proof); `VOC_HANDBACK_EVIDENCE_DIR` keeps every compared frame.

#![cfg(unix)]

use std::fs;
use std::io::{Read, Write};
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd};
use std::os::unix::fs::PermissionsExt;
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

const ROWS: u16 = 40;
const COLS: u16 = 150;
/// Failure guard for every wait; no verdict depends on elapsed time.
const GUARD: Duration = Duration::from_secs(45);
const POLL: Duration = Duration::from_millis(50);
/// The board counts as drawn once it has stayed identical across this many
/// 100 ms redraw ticks.
const STABLE: Duration = Duration::from_millis(1200);

const DECK: &str = r#"#!/bin/sh
here="$(cd "$(dirname "$0")" && pwd)"
case "$1" in
  capabilities)
    cat "$here/capabilities.json"
    exit 0
    ;;
  dashboard)
    mode="$(cat "$here/mode")"
    printf 'child output that must not survive the hand-back\n'
    printf '\033[2J\033[H'
    printf '\033[?1049h\033[2J\033[Hchild owns the alternate screen\n\033[?1049l'
    printf 'HANDBACK-CHILD-%s\n' "$mode"
    while [ ! -e "$here/proceed" ]; do sleep 0.05; done
    rm -f "$here/proceed"
    if [ "$mode" = fail ]; then
      echo 'child failed on purpose' >&2
      exit 3
    fi
    exit 0
    ;;
esac
exit 64
"#;

struct Console {
    child: Child,
    master: fs::File,
    slave: OwnedFd,
    screen: Arc<Mutex<vt100::Parser>>,
    deck_dir: PathBuf,
    _root: tempfile::TempDir,
}

impl Console {
    fn launch() -> Self {
        let root = tempfile::tempdir().expect("tempdir");
        let home = root.path().join("home");
        let vibecrafted_home = home.join(".vibecrafted");
        let state_root = vibecrafted_home.join("control_plane");
        fs::create_dir_all(state_root.join("runs")).unwrap();
        fs::create_dir_all(vibecrafted_home.join("artifacts")).unwrap();
        let repo = root.path().join("repo");
        fs::create_dir_all(repo.join(".git")).unwrap();
        for letter in ["a", "b", "c"] {
            let snapshot = serde_json::json!({
                "run_id": format!("handback-run-{letter}"),
                "agent": "codex",
                "skill": "workflow",
                "state": "completed",
                "exit_code": 0,
                "started_at": "2026-09-01T09:00:00Z",
                "updated_at": "2026-09-01T10:00:00Z",
                "completed_at": "2026-09-01T10:00:00Z",
                "operator_session": format!("handback-session-{letter}"),
                "root": repo.display().to_string(),
            });
            fs::write(
                state_root.join(format!("runs/handback-run-{letter}.json")),
                snapshot.to_string(),
            )
            .unwrap();
        }

        let deck_dir = root.path().join("deck");
        fs::create_dir_all(&deck_dir).unwrap();
        fs::copy(
            Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/capabilities.json"),
            deck_dir.join("capabilities.json"),
        )
        .unwrap();
        let deck = deck_dir.join("vibecrafted");
        fs::write(&deck, DECK).unwrap();
        fs::set_permissions(&deck, fs::Permissions::from_mode(0o755)).unwrap();
        fs::write(deck_dir.join("mode"), "ok").unwrap();

        let (master, slave) = open_pty(ROWS, COLS);
        let screen = Arc::new(Mutex::new(vt100::Parser::new(ROWS, COLS, 0)));
        let mut reader = master.try_clone().expect("clone pty master");
        let sink = Arc::clone(&screen);
        thread::spawn(move || {
            let mut buffer = [0_u8; 8192];
            while let Ok(read) = reader.read(&mut buffer) {
                if read == 0 {
                    return;
                }
                sink.lock().unwrap().process(&buffer[..read]);
            }
        });

        let binary = std::env::var_os("VOC_HANDBACK_BIN")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from(env!("CARGO_BIN_EXE_voc")));
        let mut command = Command::new(binary);
        command
            .args([
                "--view",
                "full",
                "--tick-ms",
                "100",
                "--runtime",
                "headless",
            ])
            .arg("--no-verify-gate")
            .arg("--state-root")
            .arg(&state_root)
            .arg("--repo")
            .arg(&repo)
            .arg("--deck")
            .arg(&deck)
            .args(["--server", "http://127.0.0.1:9"])
            .env_clear()
            .env("HOME", &home)
            .env("VIBECRAFTED_HOME", &vibecrafted_home)
            .env("PATH", "/usr/bin:/bin")
            .env("TERM", "xterm-256color")
            .env("LANG", "en_US.UTF-8")
            .env("TMPDIR", root.path())
            .current_dir(&repo)
            .stdin(Stdio::from(slave.try_clone().unwrap()))
            .stdout(Stdio::from(slave.try_clone().unwrap()))
            .stderr(Stdio::from(slave.try_clone().unwrap()));
        // SAFETY: only async-signal-safe calls run between fork and exec. The
        // console gets its own session with the pseudo-terminal as controlling
        // terminal, as it would under a real terminal emulator.
        unsafe {
            command.pre_exec(|| {
                if libc::setsid() == -1 || libc::ioctl(0, libc::TIOCSCTTY.into(), 0) == -1 {
                    return Err(std::io::Error::last_os_error());
                }
                Ok(())
            });
        }
        let child = command.spawn().expect("spawn voc in a pseudo-terminal");
        Console {
            child,
            master,
            slave,
            screen,
            deck_dir,
            _root: root,
        }
    }

    fn rows(&self) -> Vec<String> {
        let parser = self.screen.lock().unwrap();
        let (_, cols) = parser.screen().size();
        parser.screen().rows(0, cols).collect()
    }

    /// Every row except the status line, whose text legitimately changes.
    fn board(&self) -> Vec<String> {
        let mut rows = self.rows();
        rows.pop();
        rows
    }

    fn status(&self) -> String {
        self.rows().pop().unwrap_or_default()
    }

    fn contents(&self) -> String {
        self.screen.lock().unwrap().screen().contents()
    }

    fn send(&mut self, bytes: &[u8]) {
        self.master.write_all(bytes).unwrap();
        self.master.flush().unwrap();
    }

    fn wait_for(&self, what: &str, done: impl Fn(&vt100::Screen) -> bool) {
        let deadline = Instant::now() + GUARD;
        loop {
            if done(self.screen.lock().unwrap().screen()) {
                return;
            }
            assert!(
                Instant::now() < deadline,
                "timed out waiting for {what}; screen:\n{}",
                self.contents()
            );
            thread::sleep(POLL);
        }
    }

    fn wait_for_text(&self, text: &str) {
        self.wait_for(text, |screen| screen.contents().contains(text));
    }

    fn wait_for_text_gone(&self, text: &str) {
        self.wait_for(&format!("{text} to disappear"), |screen| {
            !screen.contents().contains(text)
        });
    }

    /// The board once it has stopped changing across several redraw ticks.
    fn settle(&self) -> Vec<String> {
        let deadline = Instant::now() + GUARD;
        let mut last = self.board();
        let mut unchanged_since = Instant::now();
        loop {
            thread::sleep(POLL);
            let now = self.board();
            if now != last {
                last = now;
                unchanged_since = Instant::now();
            } else if unchanged_since.elapsed() >= STABLE {
                return last;
            }
            assert!(Instant::now() < deadline, "the board never settled");
        }
    }

    fn resize(&self, rows: u16, cols: u16) {
        let size = libc::winsize {
            ws_row: rows,
            ws_col: cols,
            ws_xpixel: 0,
            ws_ypixel: 0,
        };
        // SAFETY: TIOCSWINSZ only reads the winsize this frame owns.
        let status = unsafe { libc::ioctl(self.master.as_raw_fd(), libc::TIOCSWINSZ, &size) };
        assert_eq!(status, 0, "resize: {}", std::io::Error::last_os_error());
        self.screen
            .lock()
            .unwrap()
            .screen_mut()
            .set_size(rows, cols);
    }

    /// Alternate screen, mouse capture and raw input, as the terminal holds them.
    fn terminal_modes(&self) -> (bool, bool, bool) {
        let (alternate, mouse) = {
            let parser = self.screen.lock().unwrap();
            let screen = parser.screen();
            (
                screen.alternate_screen(),
                screen.mouse_protocol_mode() != vt100::MouseProtocolMode::None,
            )
        };
        // SAFETY: termios is plain data that tcgetattr fills for an fd we own.
        let mut termios: libc::termios = unsafe { std::mem::zeroed() };
        let read = unsafe { libc::tcgetattr(self.slave.as_raw_fd(), &mut termios) };
        let raw = read == 0 && termios.c_lflag & (libc::ICANON | libc::ECHO) == 0;
        (alternate, mouse, raw)
    }

    fn set_mode(&self, mode: &str) {
        fs::write(self.deck_dir.join("mode"), mode).unwrap();
    }

    fn let_child_exit(&self) {
        fs::write(self.deck_dir.join("proceed"), "").unwrap();
    }

    fn set_deck_executable(&self, executable: bool) {
        let mode = if executable { 0o755 } else { 0o000 };
        fs::set_permissions(
            self.deck_dir.join("vibecrafted"),
            fs::Permissions::from_mode(mode),
        )
        .unwrap();
    }

    /// The child owned the terminal and VOC has taken it back.
    fn wait_for_hand_back(&self, marker: &str) {
        self.wait_for(
            &format!("VOC to take the terminal back from {marker}"),
            |screen| screen.alternate_screen() && !screen.contents().contains(marker),
        );
    }

    fn quit(&mut self) {
        self.send(b"q");
        let deadline = Instant::now() + GUARD;
        while Instant::now() < deadline {
            if self.child.try_wait().ok().flatten().is_some() {
                return;
            }
            thread::sleep(POLL);
        }
        panic!("voc did not exit after q");
    }
}

impl Drop for Console {
    fn drop(&mut self) {
        if self.child.try_wait().ok().flatten().is_none() {
            let _ = self.child.kill();
            let _ = self.child.wait();
        }
    }
}

fn open_pty(rows: u16, cols: u16) -> (fs::File, OwnedFd) {
    let mut master = -1;
    let mut slave = -1;
    let mut size = libc::winsize {
        ws_row: rows,
        ws_col: cols,
        ws_xpixel: 0,
        ws_ypixel: 0,
    };
    // SAFETY: openpty writes two fresh descriptors and reads the size we own.
    let status = unsafe {
        libc::openpty(
            &mut master,
            &mut slave,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            &mut size,
        )
    };
    assert_eq!(status, 0, "openpty: {}", std::io::Error::last_os_error());
    // SAFETY: both descriptors were just created by openpty and are owned here.
    unsafe { (fs::File::from_raw_fd(master), OwnedFd::from_raw_fd(slave)) }
}

fn keep_evidence(label: &str, frame: &str, rows: &[String]) {
    if let Some(dir) = std::env::var_os("VOC_HANDBACK_EVIDENCE_DIR") {
        let dir = PathBuf::from(dir);
        fs::create_dir_all(&dir).unwrap();
        fs::write(dir.join(format!("{label}-{frame}.txt")), rows.join("\n")).unwrap();
    }
}

fn compare_board(label: &str, reference: &[String], after: &[String], failures: &mut Vec<String>) {
    keep_evidence(label, "reference", reference);
    keep_evidence(label, "after", after);
    let differing = reference
        .iter()
        .zip(after)
        .enumerate()
        .filter(|(_, (before, now))| before != now)
        .map(|(row, _)| row)
        .collect::<Vec<_>>();
    if !differing.is_empty() || reference.len() != after.len() {
        failures.push(format!(
            "{label}: {} of {} rows differ from the board drawn before (rows {differing:?})\n--- before ---\n{}\n--- after ---\n{}",
            differing.len(),
            reference.len(),
            reference.join("\n"),
            after.join("\n")
        ));
    }
}

fn expect_terminal_restored(label: &str, console: &Console, failures: &mut Vec<String>) {
    let (alternate, mouse, raw) = console.terminal_modes();
    if !(alternate && mouse && raw) {
        failures.push(format!(
            "{label}: terminal not restored (alternate screen {alternate}, mouse capture {mouse}, raw input {raw})"
        ));
    }
}

#[test]
fn every_terminal_hand_back_leaves_the_whole_board_on_the_physical_screen() {
    let mut console = Console::launch();
    console.wait_for_text("Dispatch workflow/agy");
    console.send(b"f");
    console.wait_for_text("handback-run-a");
    console.send(b"d");
    console.wait_for_text("Attach session");
    let reference = console.settle();
    let drawn = reference.join("\n");
    assert!(
        drawn.contains("Surface") && drawn.contains("handback-run-a") && drawn.contains('┌'),
        "the reference must be a populated board:\n{drawn}"
    );
    let mut failures = Vec::new();

    // 1. The pictured path: Controls → attach, a child that clears and toggles
    //    the screen, then succeeds.
    console.set_mode("ok");
    console.send(b"\r");
    console.wait_for_text("HANDBACK-CHILD-ok");
    console.let_child_exit();
    console.wait_for_hand_back("HANDBACK-CHILD-ok");
    let after = console.settle();
    compare_board("attach-exit-0", &reference, &after, &mut failures);
    expect_terminal_restored("attach-exit-0", &console, &mut failures);
    if !console.status().contains("ran: ") {
        failures.push(format!(
            "attach-exit-0: status does not report the run: {}",
            console.status()
        ));
    }

    // A size change forces Ratatui to repaint on every build, so each hand-back
    // below starts from a whole board.
    console.resize(ROWS + 3, COLS + 7);
    console.settle();
    console.resize(ROWS, COLS);
    let bounced = console.settle();
    compare_board("resize-bounce", &reference, &bounced, &mut failures);

    // 2. The child fails; its error is closed again with Esc.
    console.set_mode("fail");
    console.send(b"\r");
    console.wait_for_text("HANDBACK-CHILD-fail");
    console.let_child_exit();
    console.wait_for_hand_back("HANDBACK-CHILD-fail");
    console.wait_for_text("Esc back to dispatch");
    expect_terminal_restored("attach-exit-3", &console, &mut failures);
    console.send(b"\x1b");
    console.wait_for_text_gone("Esc back to dispatch");
    let after = console.settle();
    compare_board("attach-exit-3", &reference, &after, &mut failures);

    console.resize(ROWS + 3, COLS + 7);
    console.settle();
    console.resize(ROWS, COLS);
    console.settle();

    // 3. The child cannot even be started.
    console.set_deck_executable(false);
    console.send(b"\r");
    console.wait_for_text("Esc back to dispatch");
    console.set_deck_executable(true);
    expect_terminal_restored("attach-spawn-failure", &console, &mut failures);
    console.send(b"\x1b");
    console.wait_for_text_gone("Esc back to dispatch");
    let after = console.settle();
    compare_board("attach-spawn-failure", &reference, &after, &mut failures);

    console.resize(ROWS + 3, COLS + 7);
    console.settle();
    console.resize(ROWS, COLS);
    console.settle();

    // 4. The terminal is resized while the child owns it and is back at the
    //    same size before VOC returns, so no size change is left to notice.
    console.set_mode("ok");
    console.send(b"\r");
    console.wait_for_text("HANDBACK-CHILD-ok");
    console.resize(ROWS + 6, COLS + 20);
    console.resize(ROWS, COLS);
    console.let_child_exit();
    console.wait_for_hand_back("HANDBACK-CHILD-ok");
    let after = console.settle();
    compare_board(
        "attach-resized-while-away",
        &reference,
        &after,
        &mut failures,
    );
    expect_terminal_restored("attach-resized-while-away", &console, &mut failures);

    console.quit();
    assert!(failures.is_empty(), "{}", failures.join("\n\n"));
}
