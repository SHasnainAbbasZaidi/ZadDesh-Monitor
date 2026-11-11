pub mod http;
pub mod icmp;
pub mod snmp;
pub mod tcp;

use crate::types::{CheckTask, ProbeResult};

pub async fn execute_probe(task: &CheckTask) -> ProbeResult {
    match task.check_type.to_lowercase().as_str() {
        "icmp" => icmp::probe_icmp(task).await,
        "snmp" => snmp::probe_snmp(task).await,
        "tcp" => tcp::probe_tcp(task).await,
        "http" | "https" => http::probe_http(task).await,
        other => ProbeResult {
            check_id: task.check_id,
            device_id: task.device_id,
            status: crate::types::ProbeStatus::Warning,
            metrics: Vec::new(),
            message: format!("Unsupported probe type: {}", other),
        },
    }
}
