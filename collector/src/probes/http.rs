use crate::types::{CheckTask, MetricPoint, ProbeResult, ProbeStatus};
use chrono::Utc;
use reqwest::{Client, Method};
use std::collections::HashMap;
use std::time::{Duration, Instant};

pub async fn probe_http(task: &CheckTask) -> ProbeResult {
    if task
        .params
        .get("operation")
        .and_then(|value| value.as_str())
        == Some("wan_speed_test")
    {
        return probe_wan(task).await;
    }
    let timeout_secs = task.timeout_seconds.max(1);
    let mut url = task.target.clone();
    if !url.starts_with("http://") && !url.starts_with("https://") {
        url = format!(
            "{}://{}",
            if task.check_type == "https" {
                "https"
            } else {
                "http"
            },
            url
        );
    }

    let method_str = task
        .params
        .get("method")
        .and_then(|v| v.as_str())
        .unwrap_or("GET");
    let method = match method_str.to_uppercase().as_str() {
        "POST" => Method::POST,
        "HEAD" => Method::HEAD,
        "PUT" => Method::PUT,
        _ => Method::GET,
    };

    let expected_status = task
        .params
        .get("expected_status")
        .and_then(|v| v.as_u64())
        .unwrap_or(200) as u16;

    let body_contains = task.params.get("body_contains").and_then(|v| v.as_str());

    let client = Client::builder()
        .timeout(Duration::from_secs(timeout_secs))
        .build()
        .unwrap_or_default();

    let start = Instant::now();
    let request_future = client.request(method, &url).send();

    let now = Utc::now();
    let mut tags = HashMap::new();
    tags.insert("protocol".to_string(), "http".to_string());

    match request_future.await {
        Ok(resp) => {
            let status_code = resp.status().as_u16();

            // Read body to compute size and assertions
            const MAX_BODY_BYTES: usize = 1024 * 1024;
            let mut resp = resp;
            let mut body_bytes = Vec::new();
            let mut complete = true;
            loop {
                match resp.chunk().await {
                    Ok(Some(chunk)) if body_bytes.len() + chunk.len() <= MAX_BODY_BYTES => {
                        body_bytes.extend_from_slice(&chunk);
                    }
                    Ok(None) => break,
                    _ => {
                        complete = false;
                        break;
                    }
                }
            }
            let response_time_ms = start.elapsed().as_secs_f64() * 1000.0;
            let response_size = body_bytes.len() as f64;

            let mut is_ok = status_code == expected_status && complete;

            if let Some(expected_sub) = body_contains {
                let body_str = String::from_utf8_lossy(&body_bytes);
                if !body_str.contains(expected_sub) {
                    is_ok = false;
                }
            }

            let metrics = vec![
                MetricPoint {
                    device_id: task.device_id,
                    check_id: Some(task.check_id),
                    metric_key: "http.available".to_string(),
                    timestamp: now,
                    value: if is_ok { 1.0 } else { 0.0 },
                    tags: tags.clone(),
                },
                MetricPoint {
                    device_id: task.device_id,
                    check_id: Some(task.check_id),
                    metric_key: "http.status_code".to_string(),
                    timestamp: now,
                    value: status_code as f64,
                    tags: tags.clone(),
                },
                MetricPoint {
                    device_id: task.device_id,
                    check_id: Some(task.check_id),
                    metric_key: "http.response_time_ms".to_string(),
                    timestamp: now,
                    value: (response_time_ms * 100.0).round() / 100.0,
                    tags: tags.clone(),
                },
                MetricPoint {
                    device_id: task.device_id,
                    check_id: Some(task.check_id),
                    metric_key: "http.response_size_bytes".to_string(),
                    timestamp: now,
                    value: response_size,
                    tags,
                },
            ];

            let probe_status = if is_ok {
                ProbeStatus::Ok
            } else if status_code >= 500 {
                ProbeStatus::Critical
            } else {
                ProbeStatus::Warning
            };

            let message = format!(
                "HTTP {} ({:.2}ms, {} bytes)",
                status_code, response_time_ms, response_size
            );

            ProbeResult {
                check_id: task.check_id,
                device_id: task.device_id,
                status: probe_status,
                metrics,
                message,
            }
        }
        Err(e) => {
            let metrics = vec![
                MetricPoint {
                    device_id: task.device_id,
                    check_id: Some(task.check_id),
                    metric_key: "http.available".to_string(),
                    timestamp: now,
                    value: 0.0,
                    tags: tags.clone(),
                },
                MetricPoint {
                    device_id: task.device_id,
                    check_id: Some(task.check_id),
                    metric_key: "http.status_code".to_string(),
                    timestamp: now,
                    value: 0.0,
                    tags,
                },
            ];

            ProbeResult {
                check_id: task.check_id,
                device_id: task.device_id,
                status: ProbeStatus::Critical,
                metrics,
                message: format!("HTTP probe failed: {}", e.without_url()),
            }
        }
    }
}

async fn probe_wan(task: &CheckTask) -> ProbeResult {
    static SERIAL: tokio::sync::Semaphore = tokio::sync::Semaphore::const_new(1);
    let _permit = SERIAL.acquire().await.expect("WAN semaphore remains open");
    let config = &task.params["configuration"];
    let targets = config["targets"].as_array();
    let interval = config["interval_seconds"].as_u64().unwrap_or(900).max(300);
    static ROTATION: std::sync::LazyLock<tokio::sync::Mutex<HashMap<i64, usize>>> =
        std::sync::LazyLock::new(|| tokio::sync::Mutex::new(HashMap::new()));
    let mut rotations = ROTATION.lock().await;
    let cursor = rotations
        .entry(task.check_id)
        .or_insert((Utc::now().timestamp().max(0) as u64 / interval) as usize);
    let selected = targets.and_then(|rows| {
        if rows.is_empty() {
            None
        } else {
            let selected = rows.get(*cursor % rows.len());
            *cursor = cursor.wrapping_add(1);
            selected
        }
    });
    drop(rotations);
    let index = selected
        .and_then(|row| row["index"].as_str())
        .unwrap_or("unknown");
    let tags = HashMap::from([("interface_index".to_string(), index.to_string())]);
    let timestamp = Utc::now();
    let point = |name: &str, value: f64, tags: HashMap<String, String>| MetricPoint {
        device_id: task.device_id,
        check_id: Some(task.check_id),
        timestamp,
        metric_key: format!("wan.test.{index}.{name}"),
        value,
        tags,
    };
    let mut metrics = Vec::new();
    let result: Result<(), &'static str> = async {
        let target = selected.ok_or("No WAN test target configured")?;
        let source: std::net::IpAddr = target["source_ip"]
            .as_str()
            .ok_or("Missing source IP")?
            .parse()
            .map_err(|_| "Invalid source IP")?;
        let client = Client::builder()
            .local_address(source)
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .connect_timeout(Duration::from_secs(3))
            .timeout(Duration::from_secs(10))
            .build()
            .map_err(|_| "Could not bind WAN source")?;
        let limit = config["max_bytes"]
            .as_u64()
            .unwrap_or(10_000_000)
            .clamp(1_000_000, 20_000_000) as usize;
        let start = Instant::now();
        let mut response = client
            .get(
                target["download_url"]
                    .as_str()
                    .ok_or("Missing download URL")?,
            )
            .header("Cache-Control", "no-cache")
            .header("Accept-Encoding", "identity")
            .send()
            .await
            .map_err(|_| "Download failed; check source binding, routing, and test server")?;
        if !response.status().is_success() {
            return Err("Download server returned an error");
        }
        let mut bytes = 0;
        while bytes < limit {
            match response.chunk().await.map_err(|_| "Download interrupted")? {
                Some(chunk) => {
                    bytes += chunk.len().min(limit - bytes);
                }
                None => break,
            }
        }
        if bytes == 0 {
            return Err("Test server returned no data");
        }
        metrics.push(point(
            "download_bps",
            bytes as f64 * 8.0 / start.elapsed().as_secs_f64().max(0.001),
            tags.clone(),
        ));
        if let Some(url) = target["upload_url"].as_str() {
            let payload = vec![0u8; limit.min(2_000_000)];
            let size = payload.len();
            let start = Instant::now();
            let response = client
                .post(url)
                .header("Content-Type", "application/octet-stream")
                .body(payload)
                .send()
                .await
                .map_err(|_| "Upload test failed")?;
            if !response.status().is_success() {
                return Err("Upload server returned an error");
            }
            metrics.push(point(
                "upload_bps",
                size as f64 * 8.0 / start.elapsed().as_secs_f64().max(0.001),
                tags.clone(),
            ));
        }
        Ok(())
    }
    .await;
    let mut outcome_tags = tags;
    if let Err(reason) = result {
        outcome_tags.insert("reason".into(), reason.into());
    }
    metrics.push(point(
        "ok",
        if result.is_ok() { 1.0 } else { 0.0 },
        outcome_tags,
    ));
    ProbeResult {
        device_id: task.device_id,
        check_id: task.check_id,
        status: if result.is_ok() {
            ProbeStatus::Ok
        } else {
            ProbeStatus::Warning
        },
        message: result
            .err()
            .unwrap_or("WAN throughput measured using configured source IP")
            .into(),
        metrics,
    }
}
#[cfg(test)]
mod wan_tests {
    use super::*;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    #[tokio::test]
    async fn wan_rotation_binds_source_and_measures_real_http_bytes() {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            for _ in 0..2 {
                let (mut socket, peer) = listener.accept().await.unwrap();
                assert_eq!(peer.ip().to_string(), "127.0.0.1");
                let mut header = vec![0; 4096];
                let length = socket.read(&mut header).await.unwrap();
                assert!(String::from_utf8_lossy(&header[..length])
                    .contains("accept-encoding: identity"));
                socket
                    .write_all(
                        b"HTTP/1.1 200 OK\r\nContent-Length: 100000\r\nConnection: close\r\n\r\n",
                    )
                    .await
                    .unwrap();
                socket.write_all(&vec![7; 100000]).await.unwrap();
            }
        });
        let mut task: CheckTask = serde_json::from_value(serde_json::json!({
            "check_id": 900001, "device_id": 1, "device_name": "Lab", "name": "WAN test",
            "check_type": "http", "target": "192.0.2.1", "interval_seconds": 600, "timeout_seconds":25, "retries":0,
            "params": {"operation":"wan_speed_test", "configuration":{"max_bytes":1000000,"targets":[
                {"index":"42","source_ip":"127.0.0.1","download_url":format!("http://{address}/download")},
                {"index":"43","source_ip":"127.0.0.1","download_url":format!("http://{address}/download")}
            ]}}
        })).unwrap();
        let first = probe_http(&task).await;
        let second = probe_http(&task).await;
        assert_eq!(first.status, ProbeStatus::Ok);
        assert_eq!(second.status, ProbeStatus::Ok);
        assert_ne!(first.metrics[0].metric_key, second.metrics[0].metric_key);
        assert!(first.metrics[0].value > 0.0);
        assert!(first
            .metrics
            .iter()
            .all(|point| !point.metric_key.starts_with("http.available")));
        server.await.unwrap();
        task.params["configuration"]["targets"] = serde_json::json!([]);
        let failure = probe_http(&task).await;
        assert_eq!(failure.status, ProbeStatus::Warning);
        assert_eq!(failure.metrics.len(), 1);
        assert_eq!(failure.metrics[0].value, 0.0);
    }
}
