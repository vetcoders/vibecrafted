use std::fs;
use std::io::Read;

use control_core::transcript_open::open_provider_transcript;

#[test]
fn opens_a_regular_transcript_and_refuses_links_and_directories() {
    let root = std::env::temp_dir().join(format!("vc-transcript-open-{}", std::process::id()));
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(&root).unwrap();
    let real = root.join("session.jsonl");
    fs::write(&real, "{}\n").unwrap();

    let mut text = String::new();
    open_provider_transcript(&real)
        .unwrap()
        .read_to_string(&mut text)
        .unwrap();
    assert_eq!(text, "{}\n");

    #[cfg(unix)]
    {
        let outside = root.join("outside.jsonl");
        fs::write(&outside, "secret\n").unwrap();
        let link = root.join("linked.jsonl");
        std::os::unix::fs::symlink(&outside, &link).unwrap();
        let refused = open_provider_transcript(&link).unwrap_err();
        assert_eq!(refused.kind(), std::io::ErrorKind::InvalidInput);
    }
    #[cfg(windows)]
    {
        let outside = root.join("outside.jsonl");
        fs::write(&outside, "secret\n").unwrap();
        let link = root.join("linked.jsonl");
        std::os::windows::fs::symlink_file(&outside, &link).unwrap();
        let refused = open_provider_transcript(&link).unwrap_err();
        assert_eq!(refused.kind(), std::io::ErrorKind::InvalidInput);
    }

    let dir = open_provider_transcript(&root).unwrap_err();
    assert_eq!(dir.kind(), std::io::ErrorKind::InvalidInput);

    fs::remove_dir_all(&root).unwrap();
}

#[cfg(unix)]
#[test]
fn concurrent_symlink_replacement_never_reads_the_target() {
    use std::sync::Arc;
    use std::sync::atomic::{AtomicBool, Ordering};

    let root = std::env::temp_dir().join(format!("vc-transcript-race-{}", std::process::id()));
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(&root).unwrap();
    let path = root.join("session.jsonl");
    let outside = root.join("outside.jsonl");
    fs::write(&path, "safe\n").unwrap();
    fs::write(&outside, "outside-secret\n").unwrap();
    let running = Arc::new(AtomicBool::new(true));
    let writer_running = Arc::clone(&running);
    let writer_root = root.clone();
    let writer_path = path.clone();
    let writer = std::thread::spawn(move || {
        let replacement = writer_root.join("replacement");
        while writer_running.load(Ordering::Relaxed) {
            fs::write(&replacement, "safe\n").unwrap();
            fs::rename(&replacement, &writer_path).unwrap();
            std::os::unix::fs::symlink(&outside, &replacement).unwrap();
            fs::rename(&replacement, &writer_path).unwrap();
        }
    });
    let mut leaked = false;
    for _ in 0..3000 {
        if let Ok(mut file) = open_provider_transcript(&path) {
            let mut text = String::new();
            file.read_to_string(&mut text).unwrap();
            if text != "safe\n" {
                leaked = true;
                break;
            }
        }
    }
    running.store(false, Ordering::Relaxed);
    writer.join().unwrap();
    fs::remove_dir_all(root).unwrap();
    assert!(!leaked, "followed a concurrently substituted symlink");
}
