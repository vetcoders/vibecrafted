//! Real host path in a private PTY/home: idle reads, invalidation and input.
#![cfg(unix)]

use std::fs::{self, File};
use std::io::{Read, Write};
use std::os::fd::FromRawFd;
use std::os::unix::process::CommandExt;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

struct Host {
    child: Child,
    master: File,
    screen: Arc<Mutex<vt100::Parser>>,
    root: tempfile::TempDir,
}

impl Host {
    fn launch() -> Self {
        let root = tempfile::tempdir().unwrap();
        let state = root.path().join("home/.vibecrafted/control_plane");
        fs::create_dir_all(state.join("runs")).unwrap();
        let mut master = -1;
        let mut slave = -1;
        let mut size = libc::winsize {
            ws_row: 40,
            ws_col: 160,
            ws_xpixel: 0,
            ws_ypixel: 0,
        };
        // SAFETY: valid output pointers; returned descriptors are owned below.
        assert_eq!(
            unsafe {
                libc::openpty(
                    &mut master,
                    &mut slave,
                    std::ptr::null_mut(),
                    std::ptr::null_mut(),
                    &mut size,
                )
            },
            0
        );
        let master = unsafe { File::from_raw_fd(master) };
        let slave = unsafe { File::from_raw_fd(slave) };
        let screen = Arc::new(Mutex::new(vt100::Parser::new(40, 160, 0)));
        let mut output = master.try_clone().unwrap();
        let sink = screen.clone();
        std::thread::spawn(move || {
            let mut bytes = [0; 8192];
            while let Ok(n) = output.read(&mut bytes) {
                if n == 0 {
                    break;
                }
                sink.lock().unwrap().process(&bytes[..n]);
            }
        });
        let binary = std::env::var_os("VOC_HOST_TEST_BIN")
            .unwrap_or_else(|| env!("CARGO_BIN_EXE_voc").into());
        let mut command = Command::new(binary);
        command
            .args([
                "--view",
                "host-runs",
                "--tick-ms",
                "50",
                "--server",
                "http://127.0.0.1:1",
            ])
            .arg("--state-root")
            .arg(&state)
            .arg("--repo")
            .arg(root.path())
            .env_clear()
            .env("HOME", root.path().join("home"))
            .env("VIBECRAFTED_HOME", root.path().join("home/.vibecrafted"))
            .env("VOC_REFRESH_TRACE_PATH", root.path().join("reads.jsonl"))
            .env("PATH", "/usr/bin:/bin")
            .env("TERM", "xterm-256color")
            .current_dir(root.path())
            .stdin(Stdio::from(slave.try_clone().unwrap()))
            .stdout(Stdio::from(slave.try_clone().unwrap()))
            .stderr(Stdio::from(slave));
        // SAFETY: async-signal-safe calls only, for this fixture's new session.
        unsafe {
            command.pre_exec(|| {
                if libc::setsid() < 0 || libc::ioctl(0, libc::TIOCSCTTY as _, 0) < 0 {
                    return Err(std::io::Error::last_os_error());
                }
                Ok(())
            });
        }
        let child = command.spawn().unwrap();
        Self {
            child,
            master,
            screen,
            root,
        }
    }
    fn contents(&self) -> String {
        self.screen.lock().unwrap().screen().contents()
    }
    fn reads(&self) -> usize {
        fs::read_to_string(self.root.path().join("reads.jsonl"))
            .unwrap_or_default()
            .lines()
            .filter(|line| line.contains("host_control_plane"))
            .count()
    }
    fn wait(&mut self, label: &str, predicate: impl Fn(&Self) -> bool) {
        let deadline = Instant::now() + Duration::from_secs(10);
        while !predicate(self) {
            assert!(
                self.child.try_wait().unwrap().is_none(),
                "host exited during {label}"
            );
            assert!(Instant::now() < deadline, "{label}: {}", self.contents());
            std::thread::sleep(Duration::from_millis(20));
        }
    }
}
impl Drop for Host {
    fn drop(&mut self) {
        if self.child.try_wait().unwrap().is_none() {
            let _ = self.child.kill();
        }
        let _ = self.child.wait();
    }
}

#[test]
fn idle_host_skips_history_but_receives_new_runs_and_keys() {
    let mut host = Host::launch();
    host.wait("initial read", |h| {
        h.reads() > 0 && h.contents().contains("Active runs")
    });
    fs::write(
        host.root
            .path()
            .join("home/.vibecrafted/control_plane/caretaker.json"),
        "{}",
    )
    .unwrap();
    // More than two old three-second refresh periods, on a PTY with no input.
    std::thread::sleep(Duration::from_secs(7));
    assert_eq!(
        host.reads(),
        1,
        "idle host repeatedly rebuilt unchanged history"
    );
    let now = chrono::Utc::now().to_rfc3339();
    fs::write(host.root.path().join("home/.vibecrafted/control_plane/runs/new-run.json"),
        serde_json::json!({"run_id":"new-run", "state":"running", "health":"active", "worker_alive":true, "agent":"codex", "skill":"workflow", "mode":"headless", "root":"/fixture/new-project", "started_at":now, "updated_at":now, "worker_pid":std::process::id(), "operator_session":"", "latest_report":"", "latest_transcript":"", "last_error":"", "source":"fixture", "lock_present":false}).to_string()).unwrap();
    host.wait("event-driven run arrival", |h| {
        h.reads() >= 2 && h.contents().contains("new-project")
    });
    let reads = host.reads();
    host.master.write_all(b"r").unwrap();
    host.wait("explicit refresh", |h| h.reads() > reads);
    host.master.write_all(b"3").unwrap();
    host.wait("responsive navigation", |h| {
        h.contents().contains("Control plane:")
    });
    host.master.write_all(b"q").unwrap();
    let deadline = Instant::now() + Duration::from_secs(2);
    while host.child.try_wait().unwrap().is_none() {
        assert!(Instant::now() < deadline, "quit blocked by reader");
        std::thread::sleep(Duration::from_millis(10));
    }
}
