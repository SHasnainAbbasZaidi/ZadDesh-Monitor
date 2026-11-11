use crate::types::{CheckTask, MetricPoint, ProbeResult, ProbeStatus};
use chrono::Utc;
use std::collections::HashMap;
use std::time::{Duration, Instant};
use tokio::io::AsyncReadExt;
use tokio::net::TcpStream;
use tokio::time::timeout;

pub async fn probe_tcp(task: &CheckTask) -> ProbeResult {
    let timeout_secs = task.timeout_seconds.max(1);
    let target = &task.target;

    let port = task
        .params
        .get("port")
        .and_then(|value| value.as_u64())
        .and_then(|value| u16::try_from(value).ok())
        .unwrap_or(80);
    let addr = if let Ok(ip) = target.parse::<std::net::IpAddr>() {
        std::net::SocketAddr::new(ip, port).to_string()
    } else if target.contains(':') {
        target.to_string()
    } else {
        format!("{target}:{port}")
    };

    let start = Instant::now();
    let connect_future = TcpStream::connect(&addr);

    let result = timeout(Duration::from_secs(timeout_secs), connect_future).await;
    let now = Utc::now();
    let mut tags = HashMap::new();
    tags.insert("target".to_string(), addr.clone());

    match result {
        Ok(Ok(mut stream)) => {
            let connect_time_ms = start.elapsed().as_secs_f64() * 1000.0;

            // Optional banner reading
            let mut banner = String::new();
            let mut buf = [0u8; 256];
            if let Ok(Ok(n)) = timeout(Duration::from_millis(500), stream.read(&mut buf)).await {
                if n > 0 {
                    banner = String::from_utf8_lossy(&buf[..n])
                        .trim()
                        .chars()
                        .take(80)
                        .collect();
                }
            }

            let metrics = vec![
                MetricPoint {
                    device_id: task.device_id,
                    check_id: Some(task.check_id),
                    metric_key: "tcp.available".to_string(),
                    timestamp: now,
                    value: 1.0,
                    tags: tags.clone(),
                },
                MetricPoint {
                    device_id: task.device_id,
                    check_id: Some(task.check_id),
                    metric_key: "tcp.connect_time_ms".to_string(),
                    timestamp: now,
                    value: (connect_time_ms * 100.0).round() / 100.0,
                    tags,
                },
            ];

            let message = if !banner.is_empty() {
                format!(
                    "TCP connect OK ({:.2}ms), Banner: {}",
                    connect_time_ms, banner
                )
            } else {
                format!("TCP connect OK ({:.2}ms)", connect_time_ms)
            };

            ProbeResult {
                check_id: task.check_id,
                device_id: task.device_id,
                status: ProbeStatus::Ok,
                metrics,
                message,
            }
        }
        Ok(Err(e)) => {
            let metrics = vec![MetricPoint {
                device_id: task.device_id,
                check_id: Some(task.check_id),
                metric_key: "tcp.available".to_string(),
                timestamp: now,
                value: 0.0,
                tags,
            }];

            ProbeResult {
                check_id: task.check_id,
                device_id: task.device_id,
                status: ProbeStatus::Critical,
                metrics,
                message: format!("TCP connect failed to {}: {}", addr, e),
            }
        }
        Err(_) => {
            let metrics = vec![MetricPoint {
                device_id: task.device_id,
                check_id: Some(task.check_id),
                metric_key: "tcp.available".to_string(),
                timestamp: now,
                value: 0.0,
                tags,
            }];

            ProbeResult {
                check_id: task.check_id,
                device_id: task.device_id,
                status: ProbeStatus::Critical,
                metrics,
                message: format!("TCP connect timed out after {}s to {}", timeout_secs, addr),
            }
        }
    }
}
