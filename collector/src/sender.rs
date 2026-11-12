use crate::buffer::DiskBuffer;
use crate::config::CollectorConfig;
use crate::types::{MetricIngestBatch, MetricPoint};
use reqwest::Client;
use std::fs;
use std::path::Path;
use std::time::Duration;
use tokio::sync::mpsc::Receiver;
use tokio::sync::oneshot;
use tokio::time::{interval, sleep, MissedTickBehavior};
use tracing::{error, info, warn};

pub struct MetricSender {
    client: Client,
    config: CollectorConfig,
    disk_buffer: DiskBuffer,
    rx: Receiver<MetricPoint>,
}

#[cfg(test)]
mod tests {
    use super::*;
    use clap::Parser;
    use std::collections::HashMap;
    use tokio::net::TcpListener;
    use tokio::time::timeout;

    #[tokio::test]
    async fn slow_upload_does_not_stop_spooling() {
        let core = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let url = format!("http://{}", core.local_addr().unwrap());
        let directory = std::env::temp_dir().join(format!(
            "zaddesh-sender-{}-{}",
            std::process::id(),
            core.local_addr().unwrap().port(),
        ));
        let config = CollectorConfig::parse_from([
            "collector",
            "--token",
            "test",
            "--server-url",
            &url,
            "--batch-size",
            "1",
            "--flush-interval-secs",
            "1",
        ]);
        let point = |value| MetricPoint {
            device_id: 1,
            check_id: Some(1),
            metric_key: "icmp.rtt_avg".into(),
            timestamp: chrono::Utc::now(),
            value,
            tags: HashMap::new(),
        };
        let mut queue = DiskBuffer::open(directory.clone(), 1).unwrap();
        queue.persist(&[point(1.0)]).unwrap();
        let (tx, rx) = tokio::sync::mpsc::channel(1);
        let sender = tokio::spawn(MetricSender::new(config, queue, rx).run());

        // Keep the first request open while another poll completes.
        let (_request, _) = timeout(Duration::from_secs(2), core.accept())
            .await
            .unwrap()
            .unwrap();
        tx.send(point(2.0)).await.unwrap();
        let second = directory.join("00000000000000000001.json");
        let persisted = timeout(Duration::from_secs(2), async {
            while !second.exists() {
                sleep(Duration::from_millis(10)).await;
            }
        })
        .await;
        sender.abort();
        let _ = sender.await;
        let (_, oldest) = DiskBuffer::peek(&directory).unwrap().unwrap();
        fs::remove_dir_all(directory).unwrap();
        assert!(persisted.is_ok());
        assert_eq!(oldest[0].value, 1.0);
    }
}

impl MetricSender {
    pub fn new(
        config: CollectorConfig,
        disk_buffer: DiskBuffer,
        rx: Receiver<MetricPoint>,
    ) -> Self {
        Self {
            client: Client::builder()
                .timeout(Duration::from_secs(5))
                .build()
                .unwrap_or_default(),
            config,
            disk_buffer,
            rx,
        }
    }

    pub async fn run(self) {
        let Self {
            client,
            config,
            mut disk_buffer,
            mut rx,
        } = self;
        let directory = disk_buffer.directory().to_owned();
        let flush_interval = Duration::from_secs(config.flush_interval_secs);
        let (closed, mut finished) = oneshot::channel();
        let mut delivery_timer = interval(flush_interval);
        delivery_timer.set_missed_tick_behavior(MissedTickBehavior::Delay);
        let deliver = async {
            loop {
                tokio::select! {
                    biased;
                    _ = &mut finished => break,
                    _ = delivery_timer.tick() => Self::deliver(&client, &config, &directory).await,
                }
            }
            Self::deliver(&client, &config, &directory).await;
        };
        let spool = async {
            Self::spool(&mut disk_buffer, &mut rx, config.batch_size, flush_interval).await;
            let _ = closed.send(());
        };
        info!("Metric ingestion sender started");
        // Keep writing the ordered disk queue even when the core is slow or unavailable.
        tokio::join!(spool, deliver);
    }

    async fn spool(
        disk_buffer: &mut DiskBuffer,
        rx: &mut Receiver<MetricPoint>,
        batch_size: usize,
        flush_interval: Duration,
    ) {
        let mut pending = Vec::with_capacity(batch_size);
        let mut flush_timer = interval(flush_interval);
        flush_timer.set_missed_tick_behavior(MissedTickBehavior::Delay);
        loop {
            tokio::select! {
                point = rx.recv() => match point {
                    Some(point) => {
                        pending.push(point);
                        if pending.len() >= batch_size {
                            Self::persist(disk_buffer, &mut pending).await;
                        }
                    }
                    None => {
                        Self::persist(disk_buffer, &mut pending).await;
                        break;
                    }
                },
                _ = flush_timer.tick() => {
                    Self::persist(disk_buffer, &mut pending).await;
                }
            }
        }
    }

    async fn persist(disk_buffer: &mut DiskBuffer, pending: &mut Vec<MetricPoint>) {
        if pending.is_empty() {
            return;
        }
        loop {
            match disk_buffer.persist(pending) {
                Ok(()) => {
                    pending.clear();
                    return;
                }
                Err(error) => {
                    error!("Cannot write telemetry spool; retaining batch and applying backpressure: {error}");
                    sleep(Duration::from_secs(2)).await;
                }
            }
        }
    }

    async fn deliver(client: &Client, config: &CollectorConfig, directory: &Path) {
        let url = format!("{}/api/v1/metrics/ingest", config.server_url);
        for _ in 0..16 {
            let (path, points) = match DiskBuffer::peek(directory) {
                Ok(Some(batch)) => batch,
                Ok(None) => return,
                Err(error) => {
                    error!("Cannot read telemetry spool: {error}");
                    return;
                }
            };
            let batch = MetricIngestBatch {
                collector_id: Some(config.collector_id),
                points,
            };
            match client
                .post(&url)
                .bearer_auth(&config.token)
                .json(&batch)
                .send()
                .await
            {
                Ok(response) if response.status().is_success() => {
                    if let Err(error) = fs::remove_file(path) {
                        error!("Could not acknowledge spooled batch: {error}");
                        return;
                    }
                }
                Ok(response) => {
                    warn!(
                        "Core rejected oldest spooled batch ({}); preserving it",
                        response.status()
                    );
                    return;
                }
                Err(error) => {
                    warn!(
                        "Core unavailable; preserving telemetry spool: {}",
                        error.without_url()
                    );
                    return;
                }
            }
        }
    }
}
