use crate::config::CollectorConfig;
use crate::probes::execute_probe;
use crate::types::{CheckTask, MetricPoint, ProbeStatus};
use rand::Rng;
use reqwest::Client;
use std::collections::{HashMap, HashSet};
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::mpsc::Sender;
use tokio::sync::Semaphore;
use tokio::time::sleep;
use tracing::{debug, error, info, warn};

pub struct Scheduler {
    config: CollectorConfig,
    client: Client,
    metric_tx: Sender<MetricPoint>,
    concurrency_pool: Arc<Semaphore>,
}

impl Scheduler {
    pub fn new(config: CollectorConfig, metric_tx: Sender<MetricPoint>) -> Self {
        let client = Client::builder()
            .timeout(Duration::from_secs(10))
            .build()
            .unwrap_or_default();

        let concurrency_pool = Arc::new(Semaphore::new(config.concurrency_limit));

        Self {
            config,
            client,
            metric_tx,
            concurrency_pool,
        }
    }

    pub async fn run(self: Arc<Self>) {
        info!(
            "Starting Check Task Scheduler (Polling tasks from {} every {}s, concurrency limit: {})",
            self.config.server_url,
            self.config.poll_tasks_interval_secs,
            self.config.concurrency_limit
        );

        struct Runner {
            task: CheckTask,
            handle: tokio::task::JoinHandle<()>,
        }
        impl Drop for Runner {
            fn drop(&mut self) {
                self.handle.abort();
            }
        }
        let mut active_tasks: HashMap<i64, Runner> = HashMap::new();

        loop {
            match self.fetch_scheduled_tasks().await {
                Ok(tasks) => {
                    let ids: HashSet<_> = tasks.iter().map(|task| task.check_id).collect();
                    active_tasks
                        .retain(|id, runner| ids.contains(id) && !runner.handle.is_finished());
                    for task in tasks {
                        if active_tasks
                            .get(&task.check_id)
                            .is_some_and(|runner| runner.task == task)
                        {
                            continue;
                        }
                        let scheduler = Arc::clone(&self);
                        let running = task.clone();
                        let handle = tokio::spawn(async move {
                            scheduler.run_check_loop(running).await;
                        });
                        active_tasks.insert(task.check_id, Runner { task, handle });
                    }
                }
                Err(e) => {
                    warn!(
                        "Could not fetch tasks from Core Server ({}). Retrying in {}s...",
                        e, self.config.poll_tasks_interval_secs
                    );
                }
            }

            sleep(Duration::from_secs(self.config.poll_tasks_interval_secs)).await;
        }
    }

    async fn fetch_scheduled_tasks(&self) -> Result<Vec<CheckTask>, reqwest::Error> {
        let url = format!(
            "{}/api/v1/collectors/{}/tasks",
            self.config.server_url, self.config.collector_id
        );

        let resp = self
            .client
            .get(&url)
            .header("Authorization", format!("Bearer {}", self.config.token))
            .send()
            .await?
            .error_for_status()?;

        #[derive(serde::Deserialize)]
        struct TasksResponse {
            tasks: Vec<CheckTask>,
        }

        let body: TasksResponse = resp.json().await?;
        Ok(body.tasks)
    }

    async fn run_check_loop(&self, task: CheckTask) {
        let interval_secs = task.interval_seconds.max(5);
        debug!(
            "Worker spawned for check #{} ('{}' - target: {}) interval: {}s",
            task.check_id, task.name, task.target, interval_secs
        );

        // Add initial random jitter (0 - 2000ms) to spread initial probe load
        let initial_jitter = rand::thread_rng().gen_range(0..2000);
        sleep(Duration::from_millis(initial_jitter)).await;

        loop {
            // Acquire permit from concurrency pool to enforce bounded concurrency
            let permit = match self.concurrency_pool.acquire().await {
                Ok(p) => p,
                Err(_) => break, // semaphore closed
            };

            let mut result = execute_probe(&task).await;
            for _ in 0..task.retries {
                if result.status == ProbeStatus::Ok || result.metrics.is_empty() {
                    break;
                }
                result = execute_probe(&task).await;
            }
            drop(permit); // Release permit immediately after probe finishes

            debug!(
                "Check #{} for device {} finished: {} ({} metrics generated)",
                result.check_id,
                result.device_id,
                result.message,
                result.metrics.len()
            );

            // Forward points to batch sender
            for pt in result.metrics {
                if self.metric_tx.send(pt).await.is_err() {
                    error!("Metrics channel closed. Worker shutting down.");
                    return;
                }
            }

            // Sleep interval with subtle jitter (+-5%)
            let jitter_pct: f64 = rand::thread_rng().gen_range(0.95..1.05);
            let sleep_duration = Duration::from_secs_f64((interval_secs as f64) * jitter_pct);
            sleep(sleep_duration).await;
        }
    }
}
