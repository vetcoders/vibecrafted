#![cfg(all(feature = "ssr", unix))]
//! Real HTTP witness: slow legacy parsing must not block measurements or the
//! reactor. One test owns its interpreter override for this entire process.

use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::Path;
use std::time::{Duration, Instant};

use leptos::config::{Env, LeptosOptions};
use serde_json::{Value, json};
use vibecrafted_server_web::control::api::control_routes_for;

async fn read(client: &reqwest::Client, base: &str, route: &str) -> (Value, Duration) {
    let start = Instant::now();
    let response = client.get(format!("{base}{route}")).send().await.unwrap();
    assert!(response.status().is_success());
    (response.json().await.unwrap(), start.elapsed())
}

async fn settled(client: &reqwest::Client, base: &str) -> Value {
    let deadline = Instant::now() + Duration::from_secs(13);
    loop {
        let (report, _) = read(client, base, "/api/usage?window=24h").await;
        if report["recovery"]["refreshing"] == false {
            return report;
        }
        assert!(Instant::now() < deadline, "recovery exceeded its deadline");
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
}

fn meta(home: &Path, id: &str, value: Value) {
    let dir = home.join("control_plane/runtime_runs").join(id);
    fs::create_dir_all(&dir).unwrap();
    fs::write(dir.join("meta.json"), value.to_string()).unwrap();
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn usage_foreground_is_fast_single_flight_and_invalidates_real_sources() {
    // nosemgrep: rust.lang.security.temp-dir.temp-dir -- atomic private directory creation below rejects existing paths and symlinks before writing an executable.
    let root = std::env::temp_dir().join(format!(
        "vc-usage-latency-{}-{}",
        std::process::id(),
        chrono::Utc::now().timestamp_nanos_opt().unwrap()
    ));
    {
        use std::os::unix::fs::DirBuilderExt;
        fs::DirBuilder::new().mode(0o700).create(&root).unwrap();
    }
    let home = root.join("home");
    let now = chrono::Utc::now().to_rfc3339();
    for index in 0..250 {
        let id = format!("measured-{index:03}");
        meta(
            &home,
            &id,
            json!({
                "run_id": id, "agent":"codex", "provider":"openai", "model":"fixture",
                "status":"completed", "completed_at":now,
                "usage":{"tokens_total":25}, "cost":{"amount":0.1,"currency":"USD"}
            }),
        );
    }
    for index in 0..4 {
        let id = format!("legacy-{index}");
        meta(
            &home,
            &id,
            json!({
                "run_id":id,"agent":"claude","provider":"anthropic","status":"completed","completed_at":now
            }),
        );
        fs::write(
            home.join("control_plane/runtime_runs")
                .join(id)
                .join("transcript.log"),
            "51\n",
        )
        .unwrap();
    }
    meta(
        &home,
        "unmeasured",
        json!({"run_id":"unmeasured","agent":"kimi","completed_at":now}),
    );
    let original_meta =
        fs::read(home.join("control_plane/runtime_runs/legacy-0/meta.json")).unwrap();
    let helper = root.join("inert-python");
    let python_script = helper.with_extension("py");
    let python = std::env::var("VIBECRAFTED_PYTHON").unwrap_or_else(|_| {
        String::from_utf8(
            std::process::Command::new("python3")
                .args(["-c", "import sys; print(sys.executable)"])
                .output()
                .unwrap()
                .stdout,
        )
        .unwrap()
        .trim()
        .to_string()
    });
    let script = r#"import json, os, sys, time
from pathlib import Path
items = json.load(sys.stdin)
with Path(__file__).with_suffix('.calls').open('a') as calls:
    calls.write(str(os.getpid()) + '\n')
time.sleep(1.0)
for item in items:
    print(json.dumps({'run_id':item['run_id'],'event':'started'}), flush=True)
    tokens = int(Path(item['transcript']).read_text().strip())
    print(json.dumps({'run_id':item['run_id'],'telemetry':{
        'usage':{'tokens_total':tokens,'source':'fixture-reader'},
        'cost':{'amount':{'value':'unknown','reason':'unpriced fixture'}},
        'telemetry_source':'fixture-reader'}}), flush=True)
"#;
    fs::write(&python_script, script).unwrap();
    let quote = |value: &str| format!("'{}'", value.replace('\'', "'\\''"));
    fs::write(
        &helper,
        format!(
            "#!/bin/sh\nexec {} {} \"$@\"\n",
            quote(&python),
            quote(&python_script.to_string_lossy())
        ),
    )
    .unwrap();
    fs::set_permissions(&helper, fs::Permissions::from_mode(0o700)).unwrap();
    // SAFETY: this binary has exactly one test, and the override is established
    // before any request/background parser can read it. It is never changed.
    // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage -- this single-test process sets its interpreter before any request or parser reads it; never changed afterward.
    unsafe {
        std::env::set_var("VIBECRAFTED_PYTHON", &helper);
    }
    let opts = LeptosOptions::builder()
        .output_name("usage-latency")
        .site_root("target/site-test")
        .site_pkg_dir("pkg")
        .env(Env::PROD)
        .site_addr("127.0.0.1:0".parse::<std::net::SocketAddr>().unwrap())
        .reload_port(0)
        .build();
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(async move {
        axum::serve(listener, control_routes_for(&home).with_state(opts))
            .await
            .unwrap();
    });
    let client = reqwest::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(3))
        .build()
        .unwrap();
    let (first, cold) = read(&client, &base, "/api/usage?window=24h").await;
    assert!(cold < Duration::from_millis(800), "cold={cold:?}");
    assert_eq!(first["totals"]["tokens_total_known"], 6250);
    assert_eq!(first["recovery"]["pending_runs"], 4);
    let mut tasks = Vec::new();
    for _ in 0..8 {
        let client = client.clone();
        let base = base.clone();
        tasks.push(tokio::spawn(async move {
            read(&client, &base, "/api/usage?window=24h").await.1
        }));
    }
    let mut concurrent = Vec::new();
    for task in tasks {
        let elapsed = task.await.unwrap();
        assert!(
            elapsed < Duration::from_millis(800),
            "concurrent={elapsed:?}"
        );
        concurrent.push(elapsed.as_secs_f64());
    }
    let (_, health) = read(&client, &base, "/api/health").await;
    assert!(health < Duration::from_millis(200));
    let recovered = settled(&client, &base).await;
    assert_eq!(recovered["totals"]["tokens_total_known"], 6454);
    assert_eq!(recovered["totals"]["runs_tokens_unknown"], 1);
    let calls = || {
        fs::read_to_string(helper.with_extension("calls"))
            .unwrap()
            .lines()
            .count()
    };
    assert_eq!(calls(), 1, "all foreground requests share one recovery");
    let (filtered, warm) = read(&client, &base, "/api/usage?window=24h&agent=claude").await;
    assert_eq!(filtered["totals"]["tokens_total_known"], 204);
    assert!(warm < Duration::from_millis(800));
    assert_eq!(calls(), 1, "unpriced costs are negatively cached");
    let home = root.join("home");
    let transcript = home.join("control_plane/runtime_runs/legacy-0/transcript.log");
    fs::write(&transcript, "151\n").unwrap();
    let (previous, _) = read(&client, &base, "/api/usage?window=24h").await;
    assert_eq!(previous["totals"]["tokens_total_known"], 6454);
    assert_eq!(previous["recovery"]["stale_runs"], 1);
    let updated = settled(&client, &base).await;
    assert_eq!(updated["totals"]["tokens_total_known"], 6554);
    assert_eq!(
        calls(),
        2,
        "source fingerprint invalidates the cached result"
    );
    assert_eq!(
        fs::read(home.join("control_plane/runtime_runs/legacy-0/meta.json")).unwrap(),
        original_meta
    );
    fs::write(
        &python_script,
        script.replace(
            "    tokens = int",
            "    if item['run_id'] == 'legacy-0': time.sleep(30.0)\n    tokens = int",
        ),
    )
    .unwrap();
    fs::write(&transcript, "222\n").unwrap();
    fs::write(
        home.join("control_plane/runtime_runs/legacy-1/transcript.log"),
        "161\n",
    )
    .unwrap();
    fs::write(
        home.join("control_plane/runtime_runs/legacy-2/transcript.log"),
        "171\n",
    )
    .unwrap();
    let timeout_started = Instant::now();
    let (_, fast_during_failure) = read(&client, &base, "/api/usage?window=24h").await;
    assert!(fast_during_failure < Duration::from_millis(800));
    let failed = settled(&client, &base).await;
    assert!(timeout_started.elapsed() < Duration::from_secs(13));
    assert_eq!(failed["recovery"]["failure_reasons"]["timeout"], 1);
    assert_eq!(
        failed["totals"]["tokens_total_known"], 6784,
        "the hung row keeps its measurement while unvisited rows advance without a60s backoff"
    );
    assert_eq!(failed["recovery"]["stale_runs"], 1);
    assert_eq!(calls(), 4);
    let killed = fs::read_to_string(helper.with_extension("calls"))
        .unwrap()
        .lines()
        .nth(2)
        .unwrap()
        .parse::<u32>()
        .unwrap();
    assert!(
        !std::process::Command::new("/bin/kill")
            .args(["-0", &killed.to_string()])
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status()
            .unwrap()
            .success(),
        "the owned timed-out fixture interpreter was reaped"
    );
    let _ = read(&client, &base, "/api/usage?window=24h").await;
    assert_eq!(calls(), 4, "timeout retries are bounded, not per request");
    let partial = script.replace("    tokens = int", "    if item['run_id'] == 'legacy-3':\n        print(json.dumps({'run_id':item['run_id'],'error_code':'parser_failed'}), flush=True)\n        continue\n    tokens = int");
    fs::write(&python_script, partial).unwrap();
    fs::write(
        home.join("control_plane/runtime_runs/legacy-2/transcript.log"),
        "181\n",
    )
    .unwrap();
    fs::write(
        home.join("control_plane/runtime_runs/legacy-3/transcript.log"),
        "181\n",
    )
    .unwrap();
    let _ = read(&client, &base, "/api/usage?window=24h").await;
    let partial = settled(&client, &base).await;
    assert_eq!(partial["recovery"]["failure_reasons"]["parser_failed"], 1);
    assert_eq!(
        partial["totals"]["tokens_total_known"], 6794,
        "one parser failure preserves its old measurement and other rows still advance"
    );
    assert_eq!(calls(), 5);
    println!(
        "USAGE_LATENCY {}",
        json!({"fixture_runs":255,"cold_s":cold.as_secs_f64(),"warm_s":warm.as_secs_f64(),"concurrent_s":concurrent,"health_s":health.as_secs_f64(),"failure_foreground_s":fast_during_failure.as_secs_f64(),"recovery_calls":calls()})
    );
    server.abort();
    let _ = server.await;
    fs::remove_dir_all(root).unwrap();
}
