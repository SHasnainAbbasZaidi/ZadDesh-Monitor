use crate::types::{CheckTask, MetricPoint, ProbeResult, ProbeStatus};
use chrono::Utc;
use snmp2::{v3, AsyncSession, Oid, Pdu, Value};
use std::collections::{BTreeMap, HashMap};
use std::time::Duration;

type Reading = (String, f64, &'static str);
type ProbeError = &'static str;
type Binding = (String, Option<Reading>);

fn param<'a>(task: &'a CheckTask, name: &str, default: &'a str) -> &'a str {
    task.params
        .get(name)
        .and_then(|value| value.as_str())
        .unwrap_or(default)
}

fn secret(task: &CheckTask, name: &str) -> Result<String, ProbeError> {
    let value = if let Some(credentials) = &task.snmp_credentials {
        match name {
            "COMMUNITY" => credentials.community.clone(),
            "USERNAME" => credentials.username.clone(),
            "AUTH_PASSWORD" => credentials.auth_password.clone(),
            "PRIV_PASSWORD" => credentials.priv_password.clone(),
            _ => None,
        }
    } else {
        let alias = param(task, "credential", "");
        if alias.is_empty()
            || alias.len() > 48
            || !alias
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || c == b'_')
        {
            return Err("SNMP credential alias is required");
        }
        std::env::var(format!(
            "ZADDESH_SNMP_{}_{name}",
            alias.to_ascii_uppercase()
        ))
        .ok()
    };
    value
        .filter(|value| !value.is_empty() && value.len() <= 255)
        .ok_or("SNMP credential is missing or invalid")
}

fn security(task: &CheckTask) -> Result<v3::Security, ProbeError> {
    let username = secret(task, "USERNAME")?;
    let level = param(task, "security_level", "authPriv");
    let password = if level == "noAuthNoPriv" {
        String::new()
    } else {
        secret(task, "AUTH_PASSWORD")?
    };
    let protocol = match param(task, "auth_protocol", "sha256") {
        "md5" => v3::AuthProtocol::Md5,
        "sha1" => v3::AuthProtocol::Sha1,
        "sha224" => v3::AuthProtocol::Sha224,
        "sha256" => v3::AuthProtocol::Sha256,
        "sha384" => v3::AuthProtocol::Sha384,
        "sha512" => v3::AuthProtocol::Sha512,
        _ => return Err("Unsupported SNMP authentication protocol"),
    };
    let auth = match level {
        "noAuthNoPriv" => v3::Auth::NoAuthNoPriv,
        "authNoPriv" => v3::Auth::AuthNoPriv,
        "authPriv" => {
            let cipher = match param(task, "privacy_protocol", "aes128") {
                "des" => v3::Cipher::Des,
                "aes128" => v3::Cipher::Aes128,
                "aes192" => v3::Cipher::Aes192,
                "aes256" => v3::Cipher::Aes256,
                _ => return Err("Unsupported SNMP privacy protocol"),
            };
            let privacy_password = secret(task, "PRIV_PASSWORD")?.into_bytes();
            if privacy_password.len() < 8 {
                return Err("SNMP privacy password is too short");
            }
            v3::Auth::AuthPriv {
                cipher,
                privacy_password,
            }
        }
        _ => return Err("Unsupported SNMP security level"),
    };
    if level != "noAuthNoPriv" && password.len() < 8 {
        return Err("SNMP authentication password is too short");
    }
    Ok(v3::Security::new(username.as_bytes(), password.as_bytes())
        .with_context_name(param(task, "context_name", ""))
        .with_auth_protocol(protocol)
        .with_auth(auth))
}

fn oid(input: &str) -> Result<Oid<'static>, ProbeError> {
    let input = input.strip_prefix('.').unwrap_or(input);
    let numbers: Vec<u64> = input
        .split('.')
        .map(str::parse)
        .collect::<Result<_, _>>()
        .map_err(|_| "Invalid numeric SNMP OID")?;
    if !(2..=64).contains(&numbers.len()) {
        return Err("Invalid numeric SNMP OID length");
    }
    Oid::from(&numbers)
        .map(|value| value.to_owned())
        .map_err(|_| "Invalid numeric SNMP OID")
}

fn numeric(value: Value<'_>) -> Option<(f64, &'static str)> {
    Some(match value {
        Value::Boolean(value) => (if value { 1.0 } else { 0.0 }, "boolean"),
        Value::Integer(value) => (value as f64, "integer"),
        Value::Unsigned32(value) => (f64::from(value), "unsigned32"),
        Value::Timeticks(value) => (f64::from(value) / 100.0, "seconds"),
        Value::Counter32(value) => (f64::from(value), "counter32"),
        Value::Counter64(value) => (value as f64, "counter64"),
        _ => return None,
    })
}

fn decode(pdu: Pdu<'_>) -> Result<Vec<Binding>, ProbeError> {
    if pdu.error_status != 0 {
        return Err("SNMP agent returned an error");
    }
    Ok(pdu
        .varbinds
        .filter_map(|(oid, value)| {
            if matches!(
                value,
                Value::EndOfMibView | Value::NoSuchObject | Value::NoSuchInstance
            ) {
                return None;
            }
            let name = oid.to_string().trim_start_matches('.').to_string();
            let reading = numeric(value).map(|(value, unit)| (name.clone(), value, unit));
            Some((name, reading))
        })
        .collect())
}

async fn request(
    session: &mut AsyncSession,
    oid: &Oid<'_>,
    operation: &str,
    max_repetitions: u32,
) -> Result<Vec<Binding>, ProbeError> {
    for _ in 0..3 {
        let response = match operation {
            "get" => session.get(oid).await,
            "getnext" | "walk" => session.getnext(oid).await,
            "getbulk" => session.getbulk(&[oid], 0, max_repetitions).await,
            _ => return Err("Unsupported SNMP operation"),
        };
        match response {
            Ok(pdu) => return decode(pdu),
            Err(snmp2::Error::AuthUpdated) => continue,
            Err(_) => return Err("SNMP request failed"),
        }
    }
    Err("SNMP session could not synchronize")
}

async fn open_session(task: &CheckTask) -> Result<AsyncSession, ProbeError> {
    let version = param(task, "version", "3");
    let port = task
        .params
        .get("port")
        .and_then(|value| value.as_u64())
        .and_then(|port| u16::try_from(port).ok())
        .filter(|port| *port != 0)
        .unwrap_or(161);
    let address = (task.target.as_str(), port);
    let req_id = (rand::random::<u32>() & 0x3fff_ffff) as i32;
    let mut session = match version {
        "1" => AsyncSession::new_v1(address, secret(task, "COMMUNITY")?.as_bytes(), req_id).await,
        "2c" => AsyncSession::new_v2c(address, secret(task, "COMMUNITY")?.as_bytes(), req_id).await,
        "3" => AsyncSession::new_v3(address, req_id, security(task)?).await,
        _ => return Err("Unsupported SNMP version"),
    }
    .map_err(|_| "Could not open SNMP session")?;
    if version == "3" {
        session
            .init()
            .await
            .map_err(|_| "SNMP engine discovery failed")?;
    }
    Ok(session)
}

async fn collect(task: &CheckTask) -> Result<Vec<Reading>, ProbeError> {
    let version = param(task, "version", "3");
    let operation = param(task, "operation", "get");
    if version == "1" && operation == "getbulk" {
        return Err("GETBULK requires SNMPv2c or SNMPv3");
    }
    let mut session = open_session(task).await?;
    let root = oid(param(task, "oid", "1.3.6.1.2.1.1.3.0"))?;
    let max_repetitions = task
        .params
        .get("max_repetitions")
        .and_then(|value| value.as_u64())
        .filter(|count| (1..=64).contains(count))
        .unwrap_or(16) as u32;
    if operation != "walk" {
        let bindings = request(&mut session, &root, operation, max_repetitions).await?;
        if bindings.is_empty() {
            return Err("SNMP object not returned by agent");
        }
        return Ok(bindings
            .into_iter()
            .filter_map(|(_, value)| value)
            .collect());
    }
    let prefix = format!("{}.", root.to_string().trim_start_matches('.'));
    let mut current = root;
    let mut readings = Vec::new();
    let mut found = false;
    for _ in 0..64 {
        let Some((next, value)) = request(&mut session, &current, operation, max_repetitions)
            .await?
            .into_iter()
            .next()
        else {
            break;
        };
        if !next.starts_with(&prefix) || next == current.to_string().trim_start_matches('.') {
            break;
        }
        found = true;
        readings.extend(value);
        current = oid(&next)?;
    }
    if !found {
        return Err("SNMP subtree not returned by agent");
    }
    Ok(readings)
}

const TOPOLOGY_ROOTS: &[&str] = &[
    "1.3.6.1.2.1.1",                // system
    "1.3.6.1.2.1.2.2.1.2",          // ifDescr
    "1.3.6.1.2.1.2.2.1.5",          // ifSpeed
    "1.3.6.1.2.1.2.2.1.8",          // ifOperStatus
    "1.3.6.1.2.1.31.1.1.1.1",       // ifName
    "1.3.6.1.2.1.31.1.1.1.6",       // ifHCInOctets
    "1.3.6.1.2.1.31.1.1.1.10",      // ifHCOutOctets
    "1.3.6.1.2.1.31.1.1.1.15",      // ifHighSpeed
    "1.0.8802.1.1.2.1.3.7.1.3",     // LLDP local port ID
    "1.0.8802.1.1.2.1.4.1.1.5",     // LLDP remote chassis ID
    "1.0.8802.1.1.2.1.4.1.1.7",     // LLDP remote port ID
    "1.0.8802.1.1.2.1.4.1.1.9",     // LLDP remote system name
    "1.0.8802.1.1.2.1.4.2.1.3",     // LLDP remote management address index
    "1.3.6.1.4.1.9.9.23.1.2.1.1.6", // CDP remote device
    "1.3.6.1.4.1.9.9.23.1.2.1.1.7", // CDP remote port
    "1.3.6.1.2.1.17.1.4.1.2",       // bridge port to ifIndex
    "1.3.6.1.2.1.17.7.1.4.3.1.2",   // static VLAN egress port bitmap
    "1.3.6.1.2.1.17.7.1.4.3.1.4",   // static VLAN untagged port bitmap
    "1.3.6.1.2.1.17.7.1.4.5.1.1",   // PVID
    "1.3.6.1.2.1.2.2.1.7",          // ifAdminStatus
    "1.3.6.1.2.1.2.2.1.4",          // ifMtu
    "1.3.6.1.2.1.31.1.1.1.18",      // ifAlias
    "1.3.6.1.2.1.31.1.1.1.19",      // ifCounterDiscontinuityTime
    "1.3.6.1.2.1.2.2.1.13",         // ifInDiscards
    "1.3.6.1.2.1.2.2.1.14",         // ifInErrors
    "1.3.6.1.2.1.2.2.1.19",         // ifOutDiscards
    "1.3.6.1.2.1.2.2.1.20",         // ifOutErrors
];

fn topology_value(value: Value<'_>) -> Option<String> {
    match value {
        Value::OctetString(bytes) if bytes.iter().all(|byte| !byte.is_ascii_control()) => {
            Some(String::from_utf8_lossy(bytes).chars().take(120).collect())
        }
        Value::OctetString(bytes) => Some(
            bytes
                .iter()
                .take(32)
                .map(|byte| format!("{byte:02x}"))
                .collect::<Vec<_>>()
                .join(":"),
        ),
        Value::Integer(value) => Some(value.to_string()),
        Value::Counter64(value) => Some(value.to_string()),
        Value::Counter32(value) | Value::Unsigned32(value) | Value::Timeticks(value) => {
            Some(value.to_string())
        }
        Value::ObjectIdentifier(value) => Some(value.to_string()),
        Value::IpAddress(value) => Some(std::net::Ipv4Addr::from(value).to_string()),
        _ => None,
    }
}

pub async fn discover_topology(
    task: &CheckTask,
    findings: bool,
) -> Result<BTreeMap<String, String>, &'static str> {
    let v2c = findings && param(task, "version", "3") == "2c";
    if !v2c
        && (param(task, "version", "3") != "3"
            || !matches!(
                param(task, "security_level", "authPriv"),
                "authPriv" | "authNoPriv"
            ))
    {
        return Err("Discovery requires authenticated SNMPv3");
    }
    let mut session = tokio::time::timeout(Duration::from_secs(5), open_session(task))
        .await
        .map_err(|_| {
            if v2c {
                "SNMPv2c session timed out"
            } else {
                "SNMPv3 session timed out"
            }
        })??;
    let mut values = BTreeMap::new();
    let deadline = tokio::time::Instant::now() + Duration::from_secs(55);
    for root in TOPOLOGY_ROOTS {
        if tokio::time::Instant::now() >= deadline || values.len() >= 8000 {
            break;
        }
        let prefix = format!("{root}.");
        let mut current = oid(root)?;
        let mut count = 0;
        while count < 512 && values.len() < 8000 && tokio::time::Instant::now() < deadline {
            let reply =
                tokio::time::timeout(Duration::from_secs(2), session.getbulk(&[&current], 0, 20))
                    .await;
            let pdu = match reply {
                Ok(Ok(pdu)) if pdu.error_status == 0 => pdu,
                Ok(Err(snmp2::Error::AuthUpdated)) if count == 0 => {
                    count += 1;
                    continue;
                }
                _ if values.is_empty() => {
                    return Err(if v2c {
                        "SNMPv2c community rejected or system MIB did not respond"
                    } else {
                        "SNMPv3 authentication failed or system MIB did not respond"
                    });
                }
                _ => break,
            };
            let mut next = None;
            let mut done = false;
            for (binding, value) in pdu.varbinds {
                let name = binding.to_string().trim_start_matches('.').to_owned();
                if !name.starts_with(&prefix)
                    || matches!(
                        value,
                        Value::EndOfMibView | Value::NoSuchObject | Value::NoSuchInstance
                    )
                {
                    done = true;
                    break;
                }
                let decoded = if matches!(
                    *root,
                    "1.3.6.1.2.1.17.7.1.4.3.1.2" | "1.3.6.1.2.1.17.7.1.4.3.1.4"
                ) {
                    match value {
                        Value::OctetString(bytes) if bytes.len() <= 256 => Some(
                            bytes
                                .iter()
                                .map(|byte| format!("{byte:02x}"))
                                .collect::<String>(),
                        ),
                        _ => None,
                    }
                } else {
                    topology_value(value)
                };
                if let Some(value) = decoded {
                    values.insert(name, value);
                }
                next = Some(binding.to_owned());
                count += 1;
                if count >= 512 || values.len() >= 8000 {
                    break;
                }
            }
            if done || next.as_ref().is_none_or(|next| next == &current) {
                break;
            }
            current = next.expect("checked presence above");
        }
    }
    if values.is_empty() {
        Err(if v2c {
            "SNMPv2c agent returned no readable system data"
        } else {
            "SNMPv3 agent returned no readable system data"
        })
    } else {
        Ok(values)
    }
}

pub async fn probe_snmp(task: &CheckTask) -> ProbeResult {
    let readings = tokio::time::timeout(
        Duration::from_secs(task.timeout_seconds.max(1)),
        collect(task),
    )
    .await
    .unwrap_or(Err("SNMP deadline exceeded"));
    let timestamp = Utc::now();
    let point = |metric_key, value, tags| MetricPoint {
        device_id: task.device_id,
        check_id: Some(task.check_id),
        metric_key,
        timestamp,
        value,
        tags,
    };
    let reason = readings
        .as_ref()
        .err()
        .map(|error| HashMap::from([("reason".to_string(), error.to_string())]))
        .unwrap_or_default();
    let mut metrics = vec![point(
        "snmp.available".into(),
        if readings.is_ok() { 1.0 } else { 0.0 },
        reason,
    )];
    let (status, message) = match readings {
        Ok(values) => {
            for (oid, value, unit) in values.into_iter().filter(|(oid, _, _)| oid.len() <= 123) {
                metrics.push(point(
                    format!("snmp.{oid}"),
                    value,
                    HashMap::from([("unit".into(), unit.into())]),
                ));
            }
            (ProbeStatus::Ok, "SNMP probe succeeded")
        }
        Err(error) => (ProbeStatus::Critical, error),
    };
    ProbeResult {
        check_id: task.check_id,
        device_id: task.device_id,
        status,
        metrics,
        message: message.into(),
    }
}
