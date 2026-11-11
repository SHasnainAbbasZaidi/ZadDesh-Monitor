use clap::Parser;
use std::path::PathBuf;

#[derive(Parser, Clone)]
#[command(name = "zaddesh-collector")]
#[command(author = "ZadDesh Core Team")]
#[command(version = "0.1.0")]
#[command(about = "High-concurrency async polling and probing engine for ZadDesh")]
pub struct CollectorConfig {
    #[arg(
        short,
        long,
        env = "ZADDESH_SERVER_URL",
        default_value = "http://127.0.0.1:8000"
    )]
    pub server_url: String,

    #[arg(short, long, env = "ZADDESH_COLLECTOR_TOKEN", hide_env_values = true)]
    pub token: String,

    #[arg(
        long,
        env = "ZADDESH_COLLECTOR_ENROLLMENT_TOKEN",
        hide_env_values = true
    )]
    pub enrollment_token: Option<String>,

    #[arg(short, long, env = "ZADDESH_COLLECTOR_ID", default_value_t = 1)]
    pub collector_id: i64,

    #[arg(long, default_value_t = 250, value_parser = positive_size)]
    pub concurrency_limit: usize,

    #[arg(long, default_value_t = 500, value_parser = batch_size)]
    pub batch_size: usize,

    #[arg(long, default_value_t = 3, value_parser = clap::value_parser!(u64).range(1..))]
    pub flush_interval_secs: u64,

    #[arg(long, default_value_t = 15, value_parser = clap::value_parser!(u64).range(1..))]
    pub poll_tasks_interval_secs: u64,

    #[arg(long, default_value = "./buffer_queue")]
    pub buffer_dir: PathBuf,

    #[arg(long, env = "ZADDESH_COLLECTOR_NETWORK_DIAGRAM_BETA")]
    pub network_diagram_beta: bool,
}

fn positive_size(value: &str) -> Result<usize, String> {
    let size = value
        .parse::<usize>()
        .map_err(|_| "expected a positive integer".to_string())?;
    if size == 0 {
        return Err("expected a positive integer".into());
    }
    Ok(size)
}

fn batch_size(value: &str) -> Result<usize, String> {
    let size = positive_size(value)?;
    if size > 1000 {
        return Err("batch size must be 1 through 1000".into());
    }
    Ok(size)
}
