mod buffer;
mod config;
mod probes;
mod scheduler;
mod sender;
mod topology;
mod types;

use buffer::DiskBuffer;
use clap::Parser;
use config::CollectorConfig;
use scheduler::Scheduler;
use sender::MetricSender;
use std::sync::Arc;
use tokio::sync::mpsc;
use tracing::{info, Level};
use tracing_subscriber::FmtSubscriber;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let subscriber = FmtSubscriber::builder()
        .with_max_level(Level::INFO)
        .finish();
    tracing::subscriber::set_global_default(subscriber).expect("setting default subscriber failed");

    let mut config = CollectorConfig::parse();

    if config.topology_only {
        info!("Starting ZadDesh topology worker for {}", config.server_url);
        tokio::select! {
            _ = topology::run(config) => {}
            result = shutdown_signal() => result?,
        }
        return Ok(());
    }

    info!("=====================================================");
    info!("Starting ZadDesh Collector Engine v0.1.0 (Rust tokio)");
    info!("Target Core Server: {}", config.server_url);
    info!("Collector Probe ID: {}", config.collector_id);
    info!(
        "Concurrency Limit:  {} concurrent probes",
        config.concurrency_limit
    );
    info!("Buffer Directory:   {:?}", config.buffer_dir);
    info!("=====================================================");

    // Register collector with Core
    if config.enrollment_token.is_some() {
        config.collector_id = register_with_core(&config).await?;
    }

    let heartbeat_handle = tokio::spawn(heartbeat(config.clone()));
    let topology_handle = config
        .network_diagram_beta
        .then(|| tokio::spawn(topology::run(config.clone())));

    // Initialize offline disk buffer
    let disk_buffer = DiskBuffer::open(config.buffer_dir.clone(), config.batch_size)?;

    // Create metrics ingestion channel (bounded buffer of 5000 points)
    let (tx, rx) = mpsc::channel(5000);

    // Spawn Batch Ingestion Sender
    let sender = MetricSender::new(config.clone(), disk_buffer, rx);
    let sender_handle = tokio::spawn(async move {
        sender.run().await;
    });

    // Spawn Task Scheduler
    let scheduler = Arc::new(Scheduler::new(config.clone(), tx));
    let scheduler_handle = tokio::spawn(async move {
        scheduler.run().await;
    });

    shutdown_signal().await?;
    info!("Received shutdown signal. Stopping collector engine...");
    heartbeat_handle.abort();
    if let Some(handle) = topology_handle {
        handle.abort();
    }
    scheduler_handle.abort();
    let _ = scheduler_handle.await;
    sender_handle.await?;

    info!("ZadDesh Collector stopped cleanly.");
    Ok(())
}

async fn shutdown_signal() -> std::io::Result<()> {
    #[cfg(unix)]
    {
        use tokio::signal::unix::{signal, SignalKind};
        let mut terminate = signal(SignalKind::terminate())?;
        tokio::select! {
            result = tokio::signal::ctrl_c() => result,
            _ = terminate.recv() => Ok(()),
        }
    }
    #[cfg(windows)]
    {
        let mut interrupt = tokio::signal::windows::ctrl_break()?;
        tokio::select! {
            result = tokio::signal::ctrl_c() => result,
            _ = interrupt.recv() => Ok(()),
        }
    }
    #[cfg(not(any(unix, windows)))]
    tokio::signal::ctrl_c().await
}

async fn register_with_core(config: &CollectorConfig) -> Result<i64, reqwest::Error> {
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(5))
        .build()
        .unwrap_or_default();

    let reg_url = format!("{}/api/v1/collectors/register", config.server_url);

    let hostname = std::env::var("COMPUTERNAME")
        .or_else(|_| std::env::var("HOSTNAME"))
        .unwrap_or_else(|_| "localhost".to_string());

    let payload = serde_json::json!({
        "name": format!("collector-probe-{}", config.collector_id),
        "auth_token": config.token,
        "version": "0.1.0",
        "hostname": hostname,
        "ip_address": "127.0.0.1"
    });

    let response = client
        .post(&reg_url)
        .bearer_auth(config.enrollment_token.as_deref().unwrap_or_default())
        .json(&payload)
        .send()
        .await?
        .error_for_status()?;
    #[derive(serde::Deserialize)]
    struct Registration {
        id: i64,
    }
    let registered: Registration = response.json().await?;
    info!("Enrolled collector as ID {}", registered.id);
    Ok(registered.id)
}

async fn heartbeat(config: CollectorConfig) {
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(5))
        .build()
        .unwrap_or_default();
    let url = format!("{}/api/v1/collectors/heartbeat", config.server_url);
    let mut timer = tokio::time::interval(std::time::Duration::from_secs(15));
    timer.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    loop {
        timer.tick().await;
        match client
            .post(&url)
            .bearer_auth(&config.token)
            .json(&serde_json::json!({ "collector_id": config.collector_id }))
            .send()
            .await
        {
            Ok(response) if response.status().is_success() => {}
            Ok(response) => tracing::warn!("Heartbeat rejected ({})", response.status()),
            Err(error) => tracing::warn!("Heartbeat failed: {}", error.without_url()),
        }
    }
}
