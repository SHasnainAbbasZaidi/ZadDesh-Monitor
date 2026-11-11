use crate::types::{CheckTask, MetricPoint, ProbeResult, ProbeStatus};
use chrono::Utc;
use std::collections::HashMap;
use std::time::Duration;
use tokio::process::Command;

pub async fn probe_icmp(task: &CheckTask) -> ProbeResult {
    let count = task
        .params
        .get("count")
        .and_then(|v| v.as_u64())
        .unwrap_or(3)
        .clamp(1, 10);
    let timeout_ms = task
        .params
        .get("timeout_ms")
        .and_then(|v| v.as_u64())
        .unwrap_or(2000)
        .clamp(50, 10_000);

    let target = &task.target;
    let valid_target = target.parse::<std::net::IpAddr>().is_ok()
        || (!target.starts_with('-')
            && !target.is_empty()
            && target
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || matches!(c, b'-' | b'.')));
    let output_res = if !valid_target {
        Err(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            "invalid ping target",
        ))
    } else {
        #[cfg(windows)]
        let mut command = {
            let mut command = Command::new("ping.exe");
            command.args([
                "-n",
                &count.to_string(),
                "-w",
                &timeout_ms.to_string(),
                target,
            ]);
            command
        };
        #[cfg(not(windows))]
        let mut command = {
            let mut command = Command::new("ping");
            command.env("LC_ALL", "C").args([
                "-c",
                &count.to_string(),
                "-W",
                &(timeout_ms / 1000).max(1).to_string(),
                target,
            ]);
            command
        };
        command.kill_on_drop(true);
        match tokio::time::timeout(
            Duration::from_secs(task.timeout_seconds.max(1)),
            command.output(),
        )
        .await
        {
            Ok(result) => result,
            Err(_) => Err(std::io::Error::new(
                std::io::ErrorKind::TimedOut,
                "ping deadline exceeded",
            )),
        }
    };

    match output_res {
        Ok(output) => {
            let stdout = String::from_utf8_lossy(&output.stdout);
            let stderr = String::from_utf8_lossy(&output.stderr);
            let combined = format!("{}\n{}", stdout, stderr);

            let (loss_pct, rtt_avg, rtt_min, rtt_max) = parse_ping_output(&combined);

            let is_up = loss_pct < 100.0 && output.status.success();
            let jitter = rtt_jitter(&combined);

            let now = Utc::now();
            let mut tags = HashMap::new();
            tags.insert("target".to_string(), target.clone());

            let mut metrics = vec![
                MetricPoint {
                    device_id: task.device_id,
                    check_id: Some(task.check_id),
                    metric_key: "icmp.availability".to_string(),
                    timestamp: now,
                    value: if is_up { 1.0 } else { 0.0 },
                    tags: tags.clone(),
                },
                MetricPoint {
                    device_id: task.device_id,
                    check_id: Some(task.check_id),
                    metric_key: "icmp.packet_loss".to_string(),
                    timestamp: now,
                    value: loss_pct,
                    tags: tags.clone(),
                },
            ];

            if is_up {
                metrics.push(MetricPoint {
                    device_id: task.device_id,
                    check_id: Some(task.check_id),
                    metric_key: "icmp.rtt_avg".to_string(),
                    timestamp: now,
                    value: rtt_avg,
                    tags: tags.clone(),
                });
                metrics.push(MetricPoint {
                    device_id: task.device_id,
                    check_id: Some(task.check_id),
                    metric_key: "icmp.rtt_min".to_string(),
                    timestamp: now,
                    value: rtt_min,
                    tags: tags.clone(),
                });
                metrics.push(MetricPoint {
                    device_id: task.device_id,
                    check_id: Some(task.check_id),
                    metric_key: "icmp.rtt_max".to_string(),
                    timestamp: now,
                    value: rtt_max,
                    tags: tags.clone(),
                });
                if let Some(jitter) = jitter {
                    metrics.push(MetricPoint {
                        device_id: task.device_id,
                        check_id: Some(task.check_id),
                        metric_key: "icmp.jitter".to_string(),
                        timestamp: now,
                        value: jitter,
                        tags: tags.clone(),
                    });
                }
            }

            let status = if loss_pct == 0.0 {
                ProbeStatus::Ok
            } else if loss_pct < 50.0 {
                ProbeStatus::Warning
            } else {
                ProbeStatus::Critical
            };

            let msg = if is_up {
                format!("Ping OK: {:.1}ms avg, {:.0}% loss", rtt_avg, loss_pct)
            } else {
                format!("Ping Failed: 100% packet loss to {}", target)
            };

            ProbeResult {
                check_id: task.check_id,
                device_id: task.device_id,
                status,
                metrics,
                message: msg,
            }
        }
        Err(e) => {
            let now = Utc::now();
            let mut tags = HashMap::new();
            tags.insert("target".to_string(), target.clone());

            let metrics = vec![
                MetricPoint {
                    device_id: task.device_id,
                    check_id: Some(task.check_id),
                    metric_key: "icmp.availability".to_string(),
                    timestamp: now,
                    value: 0.0,
                    tags: tags.clone(),
                },
                MetricPoint {
                    device_id: task.device_id,
                    check_id: Some(task.check_id),
                    metric_key: "icmp.packet_loss".to_string(),
                    timestamp: now,
                    value: 100.0,
                    tags,
                },
            ];

            ProbeResult {
                check_id: task.check_id,
                device_id: task.device_id,
                status: ProbeStatus::Critical,
                metrics,
                message: format!("Failed to spawn ICMP probe: {}", e),
            }
        }
    }
}

fn parse_ping_output(text: &str) -> (f64, f64, f64, f64) {
    let mut loss = 100.0;
    let mut avg = 0.0;
    let mut min = 0.0;
    let mut max = 0.0;

    for line in text.lines() {
        let lower = line.to_lowercase();

        // Parse loss
        if lower.contains("% loss") || lower.contains("% packet loss") {
            if let Some(pos) = lower.find('%') {
                let prefix = &lower[..pos];
                if let Some(last_space) = prefix.rfind(|c: char| !c.is_numeric() && c != '.') {
                    if let Ok(val) = prefix[last_space + 1..].parse::<f64>() {
                        loss = val;
                    }
                }
            }
        }

        // Windows RTT: Minimum = 1ms, Maximum = 4ms, Average = 2ms
        if lower.contains("average =") {
            for part in lower.split(',') {
                let trimmed = part.trim();
                if trimmed.starts_with("minimum =") {
                    min = parse_ms(trimmed);
                } else if trimmed.starts_with("maximum =") {
                    max = parse_ms(trimmed);
                } else if trimmed.starts_with("average =") {
                    avg = parse_ms(trimmed);
                }
            }
        }

        // Linux RTT: rtt min/avg/max/mdev = 0.035/0.045/0.055/0.008 ms
        if lower.contains("min/avg/max") {
            if let Some(eq_pos) = lower.find('=') {
                let stats = &lower[eq_pos + 1..].trim();
                let nums: Vec<&str> = stats.split('/').collect();
                if nums.len() >= 3 {
                    min = nums[0].trim().parse::<f64>().unwrap_or(0.0);
                    avg = nums[1].trim().parse::<f64>().unwrap_or(0.0);
                    if let Some(space_pos) = nums[2].find(' ') {
                        max = nums[2][..space_pos].trim().parse::<f64>().unwrap_or(0.0);
                    } else {
                        max = nums[2].trim().parse::<f64>().unwrap_or(0.0);
                    }
                }
            }
        }
    }

    (loss, avg, min, max)
}

fn parse_ms(text: &str) -> f64 {
    // extracts number before "ms"
    if let Some(ms_pos) = text.find("ms") {
        let pre = &text[..ms_pos];
        if let Some(eq_pos) = pre.find('=') {
            return pre[eq_pos + 1..].trim().parse::<f64>().unwrap_or(0.0);
        }
    }
    0.0
}

fn rtt_jitter(output: &str) -> Option<f64> {
    let times: Vec<f64> = output
        .lines()
        .filter_map(|line| {
            let (_, value) = line.split_once("time=")?;
            value
                .trim()
                .split(|c: char| !c.is_ascii_digit() && c != '.')
                .next()?
                .parse()
                .ok()
        })
        .collect();
    (times.len() > 1).then(|| {
        times
            .windows(2)
            .map(|pair| (pair[1] - pair[0]).abs())
            .sum::<f64>()
            / (times.len() - 1) as f64
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_platform_statistics_without_inventing_rtt() {
        assert_eq!(parse_ping_output("    Packets: Sent = 1, Received = 1, Lost = 0 (0% loss),\n    Minimum = 0ms, Maximum = 0ms, Average = 0ms"), (0.0, 0.0, 0.0, 0.0));
        assert_eq!(parse_ping_output("2 packets transmitted, 2 received, 0% packet loss\nrtt min/avg/max/mdev = 0.035/0.045/0.055/0.008 ms"), (0.0, 0.045, 0.035, 0.055));
        assert_eq!(
            rtt_jitter("reply time=1.25 ms\nreply time=2.25 ms"),
            Some(1.0)
        );
        assert_eq!(rtt_jitter("reply time<1ms\nreply time<1ms"), None);
    }
}
