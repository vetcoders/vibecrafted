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

    let outside = root.join("outside.jsonl");
    fs::write(&outside, "secret\n").unwrap();
    let link = root.join("linked.jsonl");
    std::os::unix::fs::symlink(&outside, &link).unwrap();
    let refused = open_provider_transcript(&link).unwrap_err();
    assert_eq!(refused.kind(), std::io::ErrorKind::InvalidInput);

    let dir = open_provider_transcript(&root).unwrap_err();
    assert_eq!(dir.kind(), std::io::ErrorKind::InvalidInput);

    fs::remove_dir_all(&root).unwrap();
}
