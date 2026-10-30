#![cfg(all(feature = "ssr", unix))]
//! Native-sized collector cache must not turn Usage into a five-second503.
use leptos::config::{Env, LeptosOptions};
use serde_json::{Value, json};
use std::{
    fs,
    io::Write,
    os::unix::fs::DirBuilderExt,
    time::{Duration, Instant},
};
use vibecrafted_server_web::control::api::control_routes_for;
async fn read(client: &reqwest::Client, base: &str) -> (Value, Duration) {
    let start = Instant::now();
    let response = client
        .get(format!("{base}/api/usage?window=24h"))
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), reqwest::StatusCode::OK);
    (response.json().await.unwrap(), start.elapsed())
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn native_sized_cache_is_single_flight_and_http_stays_responsive() {
    // nosemgrep: rust.lang.security.temp-dir.temp-dir -- atomic mode0700 create rejects existing paths/symlinks before fixture writes.
    let home = std::env::temp_dir().join(format!(
        "vc-usage-native-cache-{}-{}",
        std::process::id(),
        chrono::Utc::now().timestamp_nanos_opt().unwrap()
    ));
    fs::DirBuilder::new().mode(0o700).create(&home).unwrap();
    let stamp = chrono::Utc::now();
    let run = home.join("control_plane/runtime_runs/recorded");
    fs::create_dir_all(&run).unwrap();
    fs::write(run.join("meta.json"),json!({"run_id":"recorded","agent":"codex","model":"fixture","completed_at":stamp.to_rfc3339(),"usage":{"tokens_total":10},"cost":{"amount":0.1,"currency":"USD"}}).to_string()).unwrap();
    let dir = home.join("telemetry/claude");
    fs::create_dir_all(&dir).unwrap();
    let mut file = fs::File::create(dir.join("scan-cache.json")).unwrap();
    write!(file,"{{\"files\":{{\"/synthetic/session.jsonl\":{{\"days\":[{{\"day\":\"{}\",\"model\":\"fixture\",\"input_tokens\":40,\"output_tokens\":11}}],\"record\":{{\"id\":\"synthetic-session\",\"mtime\":{},\"native\":\"",stamp.format("%Y-%m-%d"),stamp.timestamp()).unwrap();
    let block = vec![b'x'; 1024 * 1024];
    for _ in 0..211 {
        file.write_all(&block).unwrap();
    }
    file.write_all(b"\"}}}}").unwrap();
    drop(file);
    let opts = LeptosOptions::builder()
        .output_name("usage-native-cache")
        .site_root("unused")
        .site_pkg_dir("pkg")
        .env(Env::PROD)
        .site_addr("127.0.0.1:0".parse::<std::net::SocketAddr>().unwrap())
        .reload_port(0)
        .build();
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    let selected = home.clone();
    let server = tokio::spawn(async move {
        axum::serve(listener, control_routes_for(selected).with_state(opts))
            .await
            .unwrap();
    });
    let client = reqwest::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(2))
        .build()
        .unwrap();
    let (first, cold) = read(&client, &base).await;
    assert!(cold < Duration::from_millis(500));
    assert_eq!(first["totals"]["tokens_total_known"], 10);
    assert!(
        first["local_sources"]
            .as_array()
            .unwrap()
            .iter()
            .any(|s| s["refreshing"] == true)
    );
    let mut tasks = Vec::new();
    for _ in 0..8 {
        let client = client.clone();
        let base = base.clone();
        tasks.push(tokio::spawn(async move {
            let (_, elapsed) = read(&client, &base).await;
            assert!(elapsed < Duration::from_millis(500));
            elapsed.as_secs_f64()
        }));
    }
    let mut concurrent = Vec::new();
    for task in tasks {
        concurrent.push(task.await.unwrap());
    }
    let start = Instant::now();
    let complete = loop {
        let (report, _) = read(&client, &base).await;
        if !report["local_sources"]
            .as_array()
            .unwrap()
            .iter()
            .any(|s| s["refreshing"] == true)
        {
            break report;
        }
        assert!(start.elapsed() < Duration::from_secs(15));
        tokio::time::sleep(Duration::from_millis(50)).await;
    };
    assert_eq!(complete["totals"]["tokens_total_known"], 61);
    assert_eq!(complete["totals"]["runs"], 2);
    let (warm, warm_s) = read(&client, &base).await;
    assert_eq!(warm["totals"], complete["totals"]);
    assert!(warm_s < Duration::from_millis(300));
    eprintln!(
        "USAGE_LARGE_CACHE {}",
        json!({"cache_bytes":211*1024*1024,"cold_s":cold.as_secs_f64(),"concurrent_s":concurrent,"warm_s":warm_s.as_secs_f64(),"local_completion_s":start.elapsed().as_secs_f64(),"complete_rows":2,"complete_synthetic_tokens":61})
    );
    server.abort();
    let _ = server.await;
    fs::remove_dir_all(home).unwrap();
}
