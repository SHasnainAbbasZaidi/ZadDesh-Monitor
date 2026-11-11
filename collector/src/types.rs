use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MetricPoint {
    pub device_id: i64,
    pub check_id: Option<i64>,
    pub metric_key: String,
    pub timestamp: DateTime<Utc>,
    pub value: f64,
    #[serde(default)]
    pub tags: HashMap<String, String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MetricIngestBatch {
    pub collector_id: Option<i64>,
    pub points: Vec<MetricPoint>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CheckTask {
    pub check_id: i64,
    pub device_id: i64,
    pub device_name: String,
    pub name: String,
    pub check_type: String, // "icmp", "http", "tcp", "udp", "snmp"
    pub target: String,
    #[serde(default)]
    pub params: serde_json::Value,
    #[serde(default, skip_serializing)]
    pub snmp_credentials: Option<SnmpCredentials>,
    pub interval_seconds: u64,
    pub timeout_seconds: u64,
    pub retries: u32,
}

#[derive(Clone, Deserialize, PartialEq)]
pub struct SnmpCredentials {
    pub community: Option<String>,
    pub username: Option<String>,
    pub auth_password: Option<String>,
    pub priv_password: Option<String>,
}

impl std::fmt::Debug for SnmpCredentials {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("SnmpCredentials([redacted])")
    }
}

#[derive(Debug, Clone)]
pub struct ProbeResult {
    pub check_id: i64,
    pub device_id: i64,
    pub status: ProbeStatus,
    pub metrics: Vec<MetricPoint>,
    pub message: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ProbeStatus {
    Ok,
    Warning,
    Critical,
}
