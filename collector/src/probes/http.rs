use crate::types::{CheckTask, MetricPoint, ProbeResult, ProbeStatus};
use chrono::Utc;
use reqwest::{Client, Method};
use std::collections::HashMap;
use std::time::{Duration, Instant};

pub async fn probe_http(task: &CheckTask) -> ProbeResult {
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
