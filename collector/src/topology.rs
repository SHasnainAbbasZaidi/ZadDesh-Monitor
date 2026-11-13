use crate::{config::CollectorConfig, probes::snmp::discover_topology, types::CheckTask};
use serde::Deserialize;
use std::time::Duration;

#[derive(Deserialize)]
struct Job {
    run_id: i64,
    lease: String,
    task: CheckTask,
}

pub async fn run(config: CollectorConfig) {
    let Ok(client) = reqwest::Client::builder()
        .timeout(Duration::from_secs(10))
        .redirect(reqwest::redirect::Policy::none())
        .build()
    else {
        return;
    };
    let base = format!(
        "{}/api/v1/collectors",
        config.server_url.trim_end_matches('/')
    );
    let mut timer = tokio::time::interval(Duration::from_secs(5));
    timer.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    loop {
        timer.tick().await;
        let Ok(response) = client
            .post(format!("{base}/topology-next"))
            .bearer_auth(&config.token)
            .send()
            .await
        else {
            continue;
        };
        if response.status() != reqwest::StatusCode::OK {
            continue;
        }
        let Ok(job) = response.json::<Job>().await else {
            continue;
        };
        let result =
            tokio::time::timeout(Duration::from_secs(65), discover_topology(&job.task)).await;
        let payload = match result {
            Ok(Ok(values)) => serde_json::json!({ "lease": job.lease, "values": values }),
            Ok(Err(error)) => serde_json::json!({ "lease": job.lease, "error": error }),
            Err(_) => {
                serde_json::json!({ "lease": job.lease, "error": "SNMPv3 discovery timed out" })
            }
        };
        for _ in 0..2 {
            if let Ok(response) = client
                .post(format!("{base}/topology-result/{}", job.run_id))
                .bearer_auth(&config.token)
                .json(&payload)
                .send()
                .await
            {
                if response.status().is_success() || response.status().is_client_error() {
                    break;
                }
            }
            tokio::time::sleep(Duration::from_secs(1)).await;
        }
    }
}
