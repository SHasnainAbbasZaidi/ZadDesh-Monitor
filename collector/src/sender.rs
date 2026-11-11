use crate::buffer::DiskBuffer;
use crate::config::CollectorConfig;
use crate::types::{MetricIngestBatch, MetricPoint};
use reqwest::Client;
use std::fs;
use std::time::Duration;
use tokio::sync::mpsc::Receiver;
use tokio::time::{interval, sleep, MissedTickBehavior};
use tracing::{error, info, warn};

pub struct MetricSender {
    client: Client,
    config: CollectorConfig,
    disk_buffer: DiskBuffer,
    rx: Receiver<MetricPoint>,
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

    pub async fn run(mut self) {
        let mut pending = Vec::with_capacity(self.config.batch_size);
        let mut flush_timer = interval(Duration::from_secs(self.config.flush_interval_secs));
        flush_timer.set_missed_tick_behavior(MissedTickBehavior::Delay);
        info!("Metric ingestion sender started");
        loop {
            tokio::select! {
                point = self.rx.recv() => match point {
                    Some(point) => {
                        pending.push(point);
                        if pending.len() >= self.config.batch_size {
                            self.persist(&mut pending).await;
                        }
                    }
                    None => {
                        self.persist(&mut pending).await;
                        self.deliver().await;
                        break;
                    }
                },
                _ = flush_timer.tick() => {
                    self.persist(&mut pending).await;
                    self.deliver().await;
                }
            }
        }
    }

    async fn persist(&mut self, pending: &mut Vec<MetricPoint>) {
        if pending.is_empty() {
            return;
        }
        loop {
            match self.disk_buffer.persist(pending) {
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

    async fn deliver(&self) {
        let url = format!("{}/api/v1/metrics/ingest", self.config.server_url);
        for _ in 0..16 {
            let (path, points) = match self.disk_buffer.peek() {
                Ok(Some(batch)) => batch,
                Ok(None) => return,
                Err(error) => {
                    error!("Cannot read telemetry spool: {error}");
                    return;
                }
            };
            let batch = MetricIngestBatch {
                collector_id: Some(self.config.collector_id),
                points,
            };
            match self
                .client
                .post(&url)
                .bearer_auth(&self.config.token)
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
