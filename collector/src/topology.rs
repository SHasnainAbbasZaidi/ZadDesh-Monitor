use crate::{
    config::CollectorConfig,
    probes::snmp::{discover_topology, discover_workstation},
    types::CheckTask,
};
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
    if !config.network_diagram_beta && !config.topology_only {
        run_jobs(&client, &base, &config.token, "inventory").await;
        return;
    }
    tokio::join!(
        run_jobs(&client, &base, &config.token, "automatic"),
        run_jobs(&client, &base, &config.token, "findings"),
        run_jobs(&client, &base, &config.token, "inventory")
    );
}

async fn run_jobs(client: &reqwest::Client, base: &str, token: &str, mode: &str) {
    let next_path = match mode {
        "findings" => "topology-next/findings",
        "inventory" => "inventory-next",
        _ => "topology-next",
    };
    let mut timer = tokio::time::interval(Duration::from_secs(5));
    timer.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    loop {
        timer.tick().await;
        let Ok(response) = client
            .post(format!("{base}/{next_path}"))
            .bearer_auth(token)
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
        let result = tokio::time::timeout(Duration::from_secs(65), async {
            if mode == "inventory" {
                discover_workstation(&job.task).await
            } else {
                discover_topology(&job.task, mode == "findings").await
            }
        })
        .await;
        let payload = match result {
            Ok(Ok(values)) => serde_json::json!({ "lease": job.lease, "values": values }),
            Ok(Err(error)) => serde_json::json!({ "lease": job.lease, "error": error }),
            Err(_) => {
                let error = if mode == "inventory" {
                    "SNMP inventory timed out"
                } else if job
                    .task
                    .params
                    .get("version")
                    .and_then(serde_json::Value::as_str)
                    == Some("2c")
                {
                    "SNMPv2c discovery timed out"
                } else {
                    "SNMPv3 discovery timed out"
                };
                serde_json::json!({ "lease": job.lease, "error": error })
            }
        };
        for _ in 0..2 {
            if let Ok(response) = client
                .post(format!(
                    "{base}/{}-result/{}",
                    if mode == "inventory" {
                        "inventory"
                    } else {
                        "topology"
                    },
                    job.run_id
                ))
                .bearer_auth(token)
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
