//! A collection reads only what can still change, and every number stays whole.
//!
//! Closed days live in `ccusage-sealed.json` beside the projection. A session's
//! row is the sum of its sealed days and of the open window, so a session that
//! crosses the boundary counts every event exactly once.
//!
//! These tests read the projection file, which the collection writes before it
//! returns. A report is served from a cache refreshed in the background, so a
//! stale read could pass over a double count. Dates follow the real clock because
//! the window does.

#[path = "support/ccusage_env.rs"]
mod ccusage_env;

use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, Once, OnceLock, PoisonError};
use std::time::{Duration, SystemTime};

use chrono::{DateTime, Datelike, Days, SecondsFormat, Utc};
use control_core::ControlPlane;
use serde_json::{Value, json};

use ccusage_env::{point_provider_stores_at, private_dir};

const PROJECTION: &str = "ccusage-sessions.json";
const SEALED: &str = "ccusage-sealed.json";
const OPUS: &str = "claude-opus-5-5";
const SONNET: &str = "claude-sonnet-5-5";
const UNPRICED: &str = "vendor-unreleased-99";

fn store() -> &'static Path {
    static STORE: OnceLock<PathBuf> = OnceLock::new();
    STORE.get_or_init(|| private_dir("store"))
}

fn prepare_environment() {
    static ENVIRONMENT: Once = Once::new();
    ENVIRONMENT.call_once(|| {
        let host = private_dir("host");
        // SAFETY: every test passes through this Once before it reads anything,
        // and no test starts a collector before that.
        // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage -- established under the Once above.
        unsafe { point_provider_stores_at(store(), &host) };
    });
}

fn fresh_home(label: &str) -> PathBuf {
    prepare_environment();
    private_dir(label)
}

fn collect(home: &Path) {
    static COLLECTIONS: Mutex<()> = Mutex::new(());
    prepare_environment();
    let _flight = COLLECTIONS.lock().unwrap_or_else(PoisonError::into_inner);
    let outcome = ControlPlane::new(home).collect_local_usage(Duration::ZERO);
    assert!(outcome.started, "{outcome:?}");
}

fn days_ago(days: u64) -> DateTime<Utc> {
    Utc::now()
        .checked_sub_days(Days::new(days))
        .expect("date in range")
}

fn stamp(at: DateTime<Utc>) -> String {
    at.to_rfc3339_opts(SecondsFormat::Millis, true)
}

fn compact_date(at: DateTime<Utc>) -> String {
    at.format("%Y%m%d").to_string()
}

fn append(path: &Path, line: &str) {
    fs::create_dir_all(path.parent().expect("parent")).expect("store dir");
    let mut file = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .expect("open session file");
    writeln!(file, "{line}").expect("append entry");
}

fn claude_file(session: &str) -> PathBuf {
    store()
        .join("claude/projects/-fixture-project")
        .join(format!("{session}.jsonl"))
}

/// `usage` is input, cache read, cache write, output.
fn claude_entry(
    session: &str,
    id: &str,
    at: DateTime<Utc>,
    model: &str,
    usage: [u64; 4],
) -> String {
    let [input, cache_read, cache_write, output] = usage;
    json!({
        "cwd": "/fixture/project",
        "isSidechain": false,
        "message": {
            "content": [],
            "id": format!("msg_{session}_{id}"),
            "model": model,
            "role": "assistant",
            "type": "message",
            "usage": {
                "cache_creation_input_tokens": cache_write,
                "cache_read_input_tokens": cache_read,
                "input_tokens": input,
                "output_tokens": output,
            },
        },
        "requestId": format!("req_{session}_{id}"),
        "sessionId": session,
        "timestamp": stamp(at),
        "type": "assistant",
        "uuid": format!("{session}-{id}"),
        "version": "2.1.316",
    })
    .to_string()
}

fn codex_start(session: &str, at: DateTime<Utc>) -> PathBuf {
    let path = store()
        .join("codex/sessions")
        .join(format!("{:04}", at.year()))
        .join(format!("{:02}", at.month()))
        .join(format!("{:02}", at.day()))
        .join(format!(
            "rollout-{}-{session}.jsonl",
            at.format("%Y-%m-%dT%H-%M-%S")
        ));
    append(
        &path,
        &json!({
            "payload": {
                "cli_version": "0.162.1",
                "cwd": "/fixture/project",
                "id": session,
                "model_provider": "openai",
                "originator": "codex-tui",
                "source": "cli",
                "timestamp": stamp(at),
            },
            "timestamp": stamp(at),
            "type": "session_meta",
        })
        .to_string(),
    );
    append(
        &path,
        &json!({
            "payload": {"cwd": "/fixture/project", "model": "gpt-6.1-sol", "turn_id": "t"},
            "timestamp": stamp(at),
            "type": "turn_context",
        })
        .to_string(),
    );
    path
}

/// Counts are input (cached included), cached, output, as a Codex log records them.
fn codex_usage(path: &Path, at: DateTime<Utc>, last: [u64; 3], total: [u64; 3]) {
    let block = |[input, cached, output]: [u64; 3]| {
        json!({
            "cache_write_input_tokens": 0,
            "cached_input_tokens": cached,
            "input_tokens": input,
            "output_tokens": output,
            "reasoning_output_tokens": 0,
            "total_tokens": input + output,
        })
    };
    append(
        path,
        &json!({
            "payload": {
                "info": {
                    "last_token_usage": block(last),
                    "model_context_window": 475_000,
                    "total_token_usage": block(total),
                },
                "type": "token_count",
            },
            "timestamp": stamp(at),
            "type": "event_msg",
        })
        .to_string(),
    );
}

fn sealed_path(home: &Path, agent: &str) -> PathBuf {
    home.join("telemetry").join(agent).join(SEALED)
}

fn projected(home: &Path, agent: &str, session: &str) -> Value {
    let path = home.join("telemetry").join(agent).join(PROJECTION);
    let file: Value = serde_json::from_slice(&fs::read(&path).expect("projection"))
        .unwrap_or_else(|error| panic!("{}: {error}", path.display()));
    file["sessions"]
        .as_array()
        .expect("sessions")
        .iter()
        .find(|row| row["id"] == session)
        .cloned()
        .unwrap_or_else(|| panic!("no {agent} row for {session} in {file}"))
}

/// Input, cache read, cache write, output, and a total that adds up.
fn assert_tokens(home: &Path, agent: &str, session: &str, expected: [u64; 4], label: &str) {
    let row = projected(home, agent, session);
    let actual = ["input", "cache_read", "cache_write", "output"]
        .map(|field| row[field].as_u64().expect("whole count"));
    assert_eq!(actual, expected, "{label}: {row}");
    assert_eq!(
        row["total"].as_u64(),
        Some(expected.iter().sum::<u64>()),
        "{label}: {row}"
    );
}

fn cost(home: &Path, agent: &str, session: &str) -> f64 {
    let row = projected(home, agent, session);
    row["cost_amount"]
        .as_f64()
        .unwrap_or_else(|| panic!("no cost in {row}"))
}

fn set_sealed_through(home: &Path, agent: &str, date: &str) {
    let path = sealed_path(home, agent);
    let mut sealed: Value =
        serde_json::from_slice(&fs::read(&path).expect("sealed")).expect("json");
    sealed["sealed_through"] = json!(date);
    fs::write(&path, serde_json::to_vec(&sealed).expect("encode")).expect("rewrite sealed");
}

#[test]
fn a_session_crossing_the_window_counts_every_entry_once() {
    let session = "c1a0de00-0000-4000-8000-000000000101";
    let home = fresh_home("crossing");
    let file = claude_file(session);
    append(
        &file,
        &claude_entry(session, "old", days_ago(5), OPUS, [10, 20, 30, 40]),
    );
    append(
        &file,
        &claude_entry(session, "new", Utc::now(), OPUS, [1, 2, 3, 4]),
    );

    collect(&home);
    assert_tokens(&home, "claude", session, [11, 22, 33, 44], "first pass");

    collect(&home);
    assert_tokens(&home, "claude", session, [11, 22, 33, 44], "nothing new");

    append(
        &file,
        &claude_entry(session, "later", Utc::now(), OPUS, [100, 200, 300, 400]),
    );
    collect(&home);
    assert_tokens(&home, "claude", session, [111, 222, 333, 444], "appended");

    let rebuilt = fresh_home("crossing-rebuilt");
    collect(&rebuilt);
    let (grown, whole) = (
        cost(&home, "claude", session),
        cost(&rebuilt, "claude", session),
    );
    assert!(whole > 0.0, "the model has a price");
    assert!(
        (grown - whole).abs() < 1e-9,
        "the sum of the windows prices like one reading: {grown} != {whole}"
    );
}

#[test]
fn a_codex_session_crossing_the_window_takes_the_right_deltas() {
    let session = "01a0de00-0000-7000-8000-000000000102";
    let home = fresh_home("codex-crossing");
    let file = codex_start(session, days_ago(5));
    codex_usage(&file, days_ago(5), [1000, 200, 50], [1000, 200, 50]);
    codex_usage(&file, Utc::now(), [500, 100, 25], [1500, 300, 75]);

    collect(&home);
    assert_tokens(&home, "codex", session, [1200, 300, 0, 75], "first pass");

    collect(&home);
    assert_tokens(&home, "codex", session, [1200, 300, 0, 75], "nothing new");

    codex_usage(&file, Utc::now(), [100, 0, 10], [1600, 300, 85]);
    collect(&home);
    assert_tokens(&home, "codex", session, [1300, 300, 0, 85], "appended");

    let rebuilt = fresh_home("codex-crossing-rebuilt");
    collect(&rebuilt);
    let (grown, whole) = (
        cost(&home, "codex", session),
        cost(&rebuilt, "codex", session),
    );
    assert!(whole > 0.0, "the model has a price");
    assert!(
        (grown - whole).abs() < 1e-9,
        "the sum of the windows prices like one reading: {grown} != {whole}"
    );
}

#[test]
fn a_sealed_day_is_not_read_again() {
    let session = "c1a0de00-0000-4000-8000-000000000103";
    let home = fresh_home("sealed-day");
    let file = claude_file(session);
    let age = SystemTime::now() - Duration::from_secs(5 * 86_400);
    let set_age = || {
        fs::File::options()
            .write(true)
            .open(&file)
            .expect("session file")
            .set_modified(age)
            .expect("set mtime");
    };
    append(
        &file,
        &claude_entry(session, "old", days_ago(5), OPUS, [10, 20, 30, 40]),
    );
    set_age();

    collect(&home);
    assert_tokens(&home, "claude", session, [10, 20, 30, 40], "sealed");

    append(
        &file,
        &claude_entry(session, "late", days_ago(5), OPUS, [1000, 0, 0, 0]),
    );
    set_age();
    collect(&home);
    assert_tokens(&home, "claude", session, [10, 20, 30, 40], "still sealed");

    fs::remove_file(sealed_path(&home, "claude")).expect("drop sealed");
    collect(&home);
    assert_tokens(&home, "claude", session, [1010, 20, 30, 40], "rebuilt");
}

#[test]
fn a_day_closes_exactly_once() {
    let session = "c1a0de00-0000-4000-8000-000000000104";
    let home = fresh_home("day-closes");
    let file = claude_file(session);
    append(
        &file,
        &claude_entry(session, "old", days_ago(5), OPUS, [10, 20, 30, 40]),
    );
    collect(&home);
    assert_tokens(&home, "claude", session, [10, 20, 30, 40], "first pass");

    // As if the last collection ran before the next day was closed.
    set_sealed_through(&home, "claude", &compact_date(days_ago(4)));
    append(
        &file,
        &claude_entry(session, "next", days_ago(3), OPUS, [1, 2, 3, 4]),
    );

    collect(&home);
    assert_tokens(&home, "claude", session, [11, 22, 33, 44], "day closed");
    let sealed: Value =
        serde_json::from_slice(&fs::read(sealed_path(&home, "claude")).expect("sealed"))
            .expect("json");
    assert_eq!(
        sealed["sealed_through"].as_str(),
        Some(compact_date(days_ago(2)).as_str()),
        "the two most recent days stay open"
    );

    collect(&home);
    assert_tokens(&home, "claude", session, [11, 22, 33, 44], "closed once");
}

#[test]
fn a_damaged_sealed_file_is_rebuilt_not_added_to() {
    let session = "c1a0de00-0000-4000-8000-000000000105";
    let home = fresh_home("damaged");
    let file = claude_file(session);
    append(
        &file,
        &claude_entry(session, "old", days_ago(5), OPUS, [10, 20, 30, 40]),
    );
    append(
        &file,
        &claude_entry(session, "new", Utc::now(), OPUS, [1, 2, 3, 4]),
    );
    collect(&home);
    let path = sealed_path(&home, "claude");

    let mut other_schema: Value =
        serde_json::from_slice(&fs::read(&path).expect("sealed")).expect("json");
    other_schema["schema"] = json!("vibecrafted.ccusage-sealed.v0");
    let damage: [(&str, Vec<u8>); 2] = [
        ("not json", b"{ truncated".to_vec()),
        (
            "another schema",
            serde_json::to_vec(&other_schema).expect("encode"),
        ),
    ];
    for (label, bytes) in damage {
        fs::write(&path, bytes).expect("damage sealed");
        collect(&home);
        assert_tokens(&home, "claude", session, [11, 22, 33, 44], label);
    }

    // A clock moved back must not leave days counted by nobody or twice.
    let ahead = Utc::now()
        .checked_add_days(Days::new(3))
        .expect("date in range");
    set_sealed_through(&home, "claude", &compact_date(ahead));
    collect(&home);
    assert_tokens(&home, "claude", session, [11, 22, 33, 44], "date ahead");
}

#[test]
fn one_unpriced_day_leaves_the_whole_session_without_a_price() {
    let sealed_day = "c1a0de00-0000-4000-8000-000000000106";
    let open_day = "c1a0de00-0000-4000-8000-000000000107";
    let home = fresh_home("unpriced");
    for (session, old, new) in [(sealed_day, UNPRICED, OPUS), (open_day, OPUS, UNPRICED)] {
        let file = claude_file(session);
        append(
            &file,
            &claude_entry(session, "old", days_ago(5), old, [10, 20, 30, 40]),
        );
        append(
            &file,
            &claude_entry(session, "new", Utc::now(), new, [1, 2, 3, 4]),
        );
    }

    collect(&home);

    for session in [sealed_day, open_day] {
        let row = projected(&home, "claude", session);
        assert!(row["cost_amount"].is_null(), "{row}");
        assert_eq!(row["unpriced_models"], json!([UNPRICED]), "{row}");
    }
}

#[test]
fn the_dominant_model_and_last_activity_count_every_window() {
    let session = "c1a0de00-0000-4000-8000-000000000108";
    let home = fresh_home("dominant");
    let file = claude_file(session);
    let newest = Utc::now();
    append(
        &file,
        &claude_entry(session, "old", days_ago(5), OPUS, [1000, 0, 0, 1000]),
    );
    append(
        &file,
        &claude_entry(session, "new", newest, SONNET, [1, 0, 0, 1]),
    );

    collect(&home);

    let row = projected(&home, "claude", session);
    assert_eq!(row["model"], OPUS, "{row}");
    let recorded = DateTime::parse_from_rfc3339(row["recorded_at"].as_str().expect("stamp"))
        .expect("rfc3339")
        .with_timezone(&Utc);
    assert_eq!(
        recorded.timestamp_millis(),
        newest.timestamp_millis(),
        "{row}"
    );
}
