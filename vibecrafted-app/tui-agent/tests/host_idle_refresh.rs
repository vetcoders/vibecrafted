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
        Self::launch_route("host-runs")
    }
    fn launch_route(route: &str) -> Self {
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
                route,
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
    fn count(&self, kind: &str) -> usize {
        fs::read_to_string(self.root.path().join("reads.jsonl"))
            .unwrap_or_default()
            .lines()
            .filter(|line| line.contains(kind))
            .count()
    }
    fn reads(&self) -> usize {
        self.count("host_control_plane")
    }
    fn wait(&mut self, label: &str, predicate: impl Fn(&Self) -> bool) {
        self.wait_for(label, Duration::from_secs(10), predicate);
    }
    fn wait_for(&mut self, label: &str, timeout: Duration, predicate: impl Fn(&Self) -> bool) {
        let deadline = Instant::now() + timeout;
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

#[test]
fn sustained_updates_reuse_raw_inventory_and_watcher_on_every_host_route() {
    for route in [
        "host",
        "host-runs",
        "host-config",
        "host-doctor",
        "host-projects",
    ] {
        let mut host = Host::launch_route(route);
        host.wait("initial raw discovery", |h| h.count("host_raw_sources") > 0);
        // All source roots exist before the steady-state measurement. Their
        // creation legitimately changes registration once, but writes do not.
        let home = host.root.path().join("home/.vibecrafted");
        for name in ["artifacts", "locks", "marbles"] {
            fs::create_dir_all(home.join(name)).unwrap();
        }
        host.master.write_all(b"r").unwrap();
        std::thread::sleep(Duration::from_secs(2));
        let raw_before = host.count("host_raw_sources");
        let watchers_before = host.count("host_watcher");
        let reads_before = host.reads();
        let now = chrono::Utc::now().to_rfc3339();
        for i in 0..12 {
            fs::write(home.join("control_plane/runs/updates.json"), serde_json::json!({
                "run_id":"updates", "state":"completed", "health":"final", "agent":"codex", "skill":"workflow", "mode":"headless", "root":format!("/fixture/update-{i}"), "started_at":now, "updated_at":now, "operator_session":"", "latest_report":"", "latest_transcript":"", "last_error":"", "source":"fixture", "lock_present":false
            }).to_string()).unwrap();
            std::thread::sleep(Duration::from_millis(150));
        }
        host.wait("fresh projection after sustained writes", |h| {
            h.reads() > reads_before
        });
        std::thread::sleep(Duration::from_secs(1));
        assert_eq!(
            host.count("host_raw_sources"),
            raw_before,
            "{route}: control-plane writes rescanned raw history"
        );
        assert_eq!(
            host.count("host_watcher"),
            watchers_before,
            "{route}: writes recreated the watcher"
        );
        // A raw-only insertion into a deep existing source tree must also wake
        // the host, even though no Python snapshot/event was written.
        let raw_dir = home.join("artifacts/org/repo/day/reports");
        fs::create_dir_all(&raw_dir).unwrap();
        let before = host.count("host_raw_sources");
        fs::write(raw_dir.join("raw-arrival.meta.json"), serde_json::json!({"run_id":"raw-arrival", "status":"completed", "agent":"codex", "skill":"workflow", "root":"/fixture/raw-arrival", "started_at":now, "finished_at":now}).to_string()).unwrap();
        host.wait("artifact-only invalidation", |h| {
            h.count("host_raw_sources") > before
        });
        std::thread::sleep(Duration::from_secs(1));
        let before = host.count("host_raw_sources");
        fs::write(raw_dir.join("raw-arrival.meta.json"), serde_json::json!({"run_id":"raw-arrival", "status":"running", "agent":"codex", "skill":"workflow", "root":"/fixture/raw-changed", "started_at":now, "updated_at":now}).to_string()).unwrap();
        host.master.write_all(b"2").unwrap();
        host.wait("raw in-place change stays fresh", |h| {
            h.contents().contains("raw-changed")
        });
        assert_eq!(
            host.count("host_raw_sources"),
            before,
            "{route}: content write rescanned names"
        );
        let before = host.count("host_raw_sources");
        fs::rename(&raw_dir, home.join("artifacts/renamed-reports")).unwrap();
        host.wait("raw directory rename", |h| {
            h.count("host_raw_sources") > before
        });
        let before = host.count("host_raw_sources");
        fs::remove_file(home.join("artifacts/renamed-reports/raw-arrival.meta.json")).unwrap();
        host.wait("raw deletion", |h| h.count("host_raw_sources") > before);
        host.master.write_all(b"3").unwrap();
        host.wait("responsive input during updates", |h| {
            h.contents().contains("Control plane:")
        });
        host.master.write_all(b"q").unwrap();
        let until = Instant::now() + Duration::from_secs(2);
        while host.child.try_wait().unwrap().is_none() {
            assert!(Instant::now() < until, "{route}: quit blocked");
            std::thread::sleep(Duration::from_millis(10));
        }
    }
}

#[test]
fn host_rechecks_pid_exit_within_the_liveness_bound_without_disk_changes() {
    let mut host = Host::launch();
    host.wait("initial projection", |h| h.reads() > 0);
    let mut worker = Command::new("/bin/sleep").arg("60").spawn().unwrap();
    let old = (chrono::Utc::now() - chrono::Duration::days(30)).to_rfc3339();
    fs::write(host.root.path().join("home/.vibecrafted/control_plane/runs/pid-fixture.json"), serde_json::json!({"run_id":"pid-fixture", "state":"running", "health":"active", "worker_pid":worker.id(), "worker_alive":true, "agent":"codex", "skill":"workflow", "mode":"headless", "root":"/fixture/pid-project", "started_at":old, "updated_at":old, "operator_session":"", "latest_report":"", "latest_transcript":"", "last_error":"", "source":"fixture", "lock_present":false}).to_string()).unwrap();
    // Always reap this fixture process before an assertion can unwind.
    let live_until = Instant::now() + Duration::from_secs(10);
    while !host.contents().contains("Active runs 1") && Instant::now() < live_until {
        std::thread::sleep(Duration::from_millis(20));
    }
    let was_live = host.contents().contains("Active runs 1");
    worker.kill().unwrap();
    worker.wait().unwrap();
    assert!(was_live, "fixture live PID was never projected");
    let died_at = Instant::now();
    host.wait_for("PID exit without a write", Duration::from_secs(31), |h| {
        h.contents().contains("Active runs 0")
    });
    assert!(
        died_at.elapsed() <= Duration::from_secs(31),
        "30s recheck plus one input/render tick"
    );
    eprintln!(
        "PID exit projected after {:.3}s; raw discoveries={}, watchers={}",
        died_at.elapsed().as_secs_f64(),
        host.count("host_raw_sources"),
        host.count("host_watcher")
    );
}
